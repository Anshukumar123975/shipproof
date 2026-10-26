import uuid
from sqlalchemy import select
from .db import Session, Run, Record, Event, now
from .controller import recommendation

def uid(): return str(uuid.uuid4())
def record(s,kind,run_id,data,id=None):
    item=Record(id=id or uid(),kind=kind,run_id=run_id,data=data); s.add(item); return item

def event(s,run,kind,data):
    s.add(Event(run_id=run.id,kind=kind,data=data))
    run.updated=now()

def transition(s,run,status,reason):
    previous=run.status; run.status=status
    data={'from':previous,'to':status,'reason':reason}
    record(s,'transition',run.id,data); record(s,'decision',run.id,{'outcome':status,'reason':reason,'usage':run.usage})
    event(s,run,'decision',data)

def records(s,run_id,kind):
    return [{'id':r.id,**r.data,'created':r.created} for r in s.scalars(select(Record).where(Record.run_id==run_id,Record.kind==kind).order_by(Record.created))]

def view(s,run):
    evidence=records(s,run.id,'evidence'); matrix,ready=recommendation(run.data['requirements'],evidence,run.status)
    if ready=='READY' and run.data['snapshot'].get('incomplete'): ready='NEEDS_REVIEW'
    return {'id':run.id,'status':run.status,'created':run.created,'updated':run.updated,'usage':run.usage,'data':run.data,'requirements':matrix,'recommendation':ready,'evidence':evidence,'decisions':records(s,run.id,'decision'),'actions':records(s,run.id,'action'),'approvals':records(s,run.id,'approval'),'llm_calls':records(s,run.id,'llm_call'),'llm_errors':records(s,run.id,'llm_error'),'coverage':round(100*sum(r['status']!='UNKNOWN' for r in matrix)/len(matrix),1) if matrix else 0}

def report(s,run):
    v=view(s,run)
    outcome='COMPLETE' if run.status=='FINISH_VERIFICATION' else 'FAILED_TO_RUN' if run.status=='FAILED_TO_RUN' else 'INCOMPLETE'
    return {**v,'verification_outcome':outcome,'scope_note':'READY means ready within the configured verification scope. It is not a guarantee of defect-free software.','risks':[r['description'] for r in v['requirements'] if r['status']!='PASS']+(['PR analysis is incomplete'] if run.data['snapshot'].get('incomplete') else [])}

def markdown(rep):
    lines=['# ShipProof release verification',f"\n**{rep['recommendation']}** · {rep['verification_outcome']}",f"\nRun: {rep['id']}",f"\n{rep['data']['snapshot']['title']}",f"\n{rep['scope_note']}",'\n## Requirements','\n| ID | Requirement | Status |','|---|---|---|']
    for r in rep['requirements']: lines.append(f"| {r['id']} | {r['description'].replace('|','/')} | {r['status']} |")
    lines+=['\n## Evidence']
    for e in rep['evidence']:
        lines.append(f"\n- {e['requirement_id']} / {e['check_id']} / {e['assertion_result']} · action {e['action_id']} · fingerprint `{e['fingerprint']}`")
        for a in e['artifacts']: lines.append(f"  - [{a['name']}](/api/artifacts/{a['name']}) · SHA-256 `{a['sha256']}`")
    lines+=['\n## Controller decisions']+[f"- {d['outcome']}: {d['reason']}" for d in rep['decisions']]
    lines+=['\n## Remaining risks']+[f'- {r}' for r in rep['risks']]
    lines+=['\n## Budget usage',f"```json\n{__import__('json').dumps(rep['usage'],indent=2)}\n```"]
    return '\n'.join(lines)
