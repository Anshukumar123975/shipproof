"""Leased PostgreSQL jobs; no database transaction spans a tool call."""
import asyncio, os, uuid, time
from sqlalchemy import select, or_
from .db import Session, Run, Record, now
from .service import record, records, event, transition, report
from .controller import before, after, TERMINAL
from .security import destination, fingerprint
from .agents import execute, verify, write_artifact, artifact_meta
from .llm import OpenRouterClient

LEASE_SECONDS=45

def claim(owner):
    with Session.begin() as s:
        run=s.scalar(select(Run).where(Run.status.in_(['QUEUED','RUNNING','REPLAN','CONTINUE']),Run.lease_until<now()).order_by(Run.created).with_for_update(skip_locked=True).limit(1))
        if not run: return None
        if run.owner:
            # An action reserved before a crash remains charged and becomes UNKNOWN.
            for action in s.scalars(select(Record).where(Record.run_id==run.id,Record.kind=='action')):
                if action.data.get('state')=='reserved':
                    a=dict(action.data); a['state']='interrupted'; action.data=a
                    record(s,'evidence',run.id,{'requirement_id':a['check']['requirement_id'],'check_id':a['check']['check_id'],'action_id':action.id,'fingerprint':a['fingerprint'],'assertion_result':'UNKNOWN','observation':{'inconclusive':'Worker interrupted; action not replayed'},'artifacts':[]})
                    u=dict(run.usage); u['active_seconds']=u.get('active_seconds',0)+min(LEASE_SECONDS,max(0,now()-a['started'])); run.usage=u
            event(s,run,'recovered',{'reason':'Expired lease reclaimed; reservations preserved'})
        run.owner=owner; run.lease_until=now()+LEASE_SECONDS
        transition(s,run,'RUNNING','Worker claimed exclusive run lease')
        return run.id

async def heartbeat(run_id,owner):
    while True:
        await asyncio.sleep(5)
        with Session.begin() as s:
            run=s.scalar(select(Run).where(Run.id==run_id).with_for_update())
            if run.owner!=owner or run.status in TERMINAL or run.status=='WAITING_FOR_APPROVAL': return
            run.lease_until=now()+LEASE_SECONDS

async def process(run_id,owner):
    beat=asyncio.create_task(heartbeat(run_id,owner))
    try:
        with Session() as s:
            run=s.get(Run,run_id); ai=run.data.get('ai',False); data=run.data
        if ai and not data.get('analysis_done'):
            advice=await OpenRouterClient().advise(run_id,'PR analysis and change impact',{'snapshot':data['snapshot'],'requirements':data['requirements'],'impact':data['impact']})
            with Session.begin() as s:
                r=s.get(Run,run_id); d=dict(r.data); d['analysis_done']=True; d['analysis_advice']=advice.model_dump() if advice else None; r.data=d
        while True:
            replan_context=None
            with Session.begin() as s:
                run=s.scalar(select(Run).where(Run.id==run_id).with_for_update())
                if run.owner!=owner or run.status in TERMINAL: return
                if now()>run.created+run.data['budget']['deadline_seconds']:
                    transition(s,run,'STOP_INCOMPLETE','Overall deadline expired'); break
                done={e['check_id'] for e in records(s,run_id,'evidence')}
                pending=[c for c in run.data['plan'] if c['check_id'] not in done]
                if not pending:
                    transition(s,run,'FINISH_VERIFICATION','All planned checks assessed; unsupported requirements remain UNKNOWN'); break
                check=pending[0]
                try: destination(run.data['staging_url'],check['target'])
                except ValueError as e:
                    transition(s,run,'STOP_INCOMPLETE',str(e)); break
                fp=fingerprint({'run_id':run_id,'environment':run.data['staging_url'],**check})
                approvals=list(s.scalars(select(Record).where(Record.run_id==run_id,Record.kind=='approval')))
                approval=next((a for a in approvals if a.data['fingerprint']==fp and a.data['state']=='approved' and a.data['expires']>now()),None)
                policy_started=time.perf_counter()
                decision,reason=before(run,check,approval is not None)
                accounted=dict(run.usage); accounted['controller_seconds']=accounted.get('controller_seconds',0)+(time.perf_counter()-policy_started); run.usage=accounted
                if decision=='WAITING_FOR_APPROVAL':
                    pending_approval=next((a for a in approvals if a.data['fingerprint']==fp and a.data['state']=='pending' and a.data['expires']>now()),None)
                    if not pending_approval: record(s,'approval',run_id,{'fingerprint':fp,'payload':check,'environment':run.data['staging_url'],'state':'pending','expires':now()+600})
                    run.lease_until=0; transition(s,run,decision,reason); return
                if decision!='CONTINUE': transition(s,run,decision,reason); break
                if approval:
                    a=dict(approval.data); a['state']='consumed'; a['consumed_at']=now(); approval.data=a
                usage=dict(run.usage); usage['actions']=usage.get('actions',0)+1
                kind='browser' if check['action_type']=='browser' else 'api'; usage[kind]=usage.get(kind,0)+1; run.usage=usage
                action=record(s,'action',run_id,{'check':check,'fingerprint':fp,'state':'reserved','started':now(),'owner':owner}); action_id=action.id
                record(s,'decision',run_id,{'outcome':'CONTINUE','reason':reason,'check_id':check['check_id'],'usage':usage})
                event(s,run,'action_started',{'action_id':action_id,'check':check,'fingerprint':fp})
                base=run.data['staging_url']
            started=time.monotonic()
            try: observed,artifacts=await execute(base,check)
            except Exception as e: observed={'error':type(e).__name__,'inconclusive':'Tool execution could not establish the assertion'}; artifacts=[]
            result=verify(check,observed); artifacts.append(write_artifact(observed)); metadata=[artifact_meta(a) for a in artifacts]
            with Session.begin() as s:
                run=s.scalar(select(Run).where(Run.id==run_id).with_for_update())
                if run.owner!=owner: return # fencing: a reclaimed worker may not commit
                a=s.get(Record,action_id); ad=dict(a.data); ad['state']='completed'; ad['completed']=now(); a.data=ad
                record(s,'observation',run_id,{'action_id':action_id,'value':observed})
                evidence={'requirement_id':check['requirement_id'],'check_id':check['check_id'],'action_id':action_id,'fingerprint':fp,'assertion_result':result,'observation':observed,'artifacts':metadata}
                record(s,'evidence',run_id,evidence,id='evidence-'+action_id)
                u=dict(run.usage); u['active_seconds']=u.get('active_seconds',0)+(time.monotonic()-started)
                u['stagnant']=u.get('stagnant',0)+1 if result=='UNKNOWN' else 0
                u['redundant_actions']=u.get('redundant_actions',0)+int(any(e['fingerprint']==fp for e in records(s,run_id,'evidence') if e['action_id']!=action_id)); run.usage=u
                event(s,run,'evidence',evidence)
                if run.status=='CANCELLED': return
                done={e['check_id'] for e in records(s,run_id,'evidence')}
                remaining=[c for c in run.data['plan'] if c['check_id'] not in done]
                policy_started=time.perf_counter()
                decision,reason=after(run.data['policy'],len(remaining),u['stagnant'],u.get('replans',0))
                u=dict(run.usage); u['controller_seconds']=u.get('controller_seconds',0)+(time.perf_counter()-policy_started); run.usage=u
                if decision=='REPLAN':
                    # Conservative alternate: stop redundant probes and inspect a seeded known element.
                    d=dict(run.data); removed=[c for c in remaining if fingerprint({'run_id':run_id,'environment':base,**c})==fp]
                    kept=[c for c in remaining if c not in removed]
                    for c in removed:
                        record(s,'evidence',run_id,{'requirement_id':c['requirement_id'],'check_id':c['check_id'],'action_id':action_id,'fingerprint':fp,'assertion_result':'UNKNOWN','observation':{'inconclusive':'Redundant probe omitted by controller; not executed'},'artifacts':[]})
                    alternate={**check,'check_id':'replanned-'+check['check_id'],'selector':'#order-1001','text':'Order #1001'}
                    # Preserve original unknown assertions. Alternate evidence never erases them.
                    d['plan']=d['plan']+[alternate]; run.data=d
                    u=dict(run.usage); u['replans']=u.get('replans',0)+1; u['stagnant']=0; run.usage=u
                    replan_context={'last_observation':observed,'remaining_checks':kept,'alternate':alternate}
                transition(s,run,decision,reason)
                if decision in TERMINAL: break
            if ai and replan_context: await OpenRouterClient().advise(run_id,'Replanning advice',replan_context)
        if ai:
            with Session() as s: context=report(s,s.get(Run,run_id))
            await OpenRouterClient().advise(run_id,'Evidence-backed release summary',{'requirements':context['requirements'],'evidence':context['evidence'],'recommendation':context['recommendation']})
        with Session.begin() as s:
            run=s.get(Run,run_id)
            rep=report(s,run); record(s,'report',run_id,rep,id='report-'+run_id)
            run.lease_until=0; event(s,run,'report_ready',{'recommendation':rep['recommendation']})
    except Exception as e:
        with Session.begin() as s:
            run=s.get(Run,run_id)
            if run.owner==owner and run.status not in TERMINAL:
                transition(s,run,'FAILED_TO_RUN','Worker error: '+type(e).__name__); run.lease_until=0
    finally:
        beat.cancel()
        try: await beat
        except asyncio.CancelledError: pass

def expire_waits():
    with Session.begin() as s:
        for run in s.scalars(select(Run).where(Run.status=='WAITING_FOR_APPROVAL').with_for_update(skip_locked=True)):
            if now()>run.created+run.data['budget']['deadline_seconds']:
                transition(s,run,'STOP_INCOMPLETE','Overall deadline expired while awaiting approval')

async def main():
    owner=str(uuid.uuid4())
    while True:
        expire_waits(); run_id=claim(owner)
        if run_id: await process(run_id,owner)
        else: await asyncio.sleep(0.5)
if __name__=='__main__': asyncio.run(main())
