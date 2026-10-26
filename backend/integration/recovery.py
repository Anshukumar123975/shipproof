"""Controls a separate leased worker process; never kills the normal worker."""
import asyncio,subprocess,time,json,os
from sqlalchemy import select
from app.db import Session,Run,Record,Event,now
from app.schemas import CreateRun
from app.scenarios import fixture,plan
from app.github import impact
from app.service import uid,record,view
from app.worker import claim,process

def main(browser=False):
    data=CreateRun(scenario='interrupted',authorized=True).model_dump();snap=fixture('interrupted');data.update(snapshot=snap,requirements=snap['requirements'],plan=plan(snap['requirements']),impact=impact(snap,snap['requirements']),mode='Deterministic — no AI inference')
    if browser:
        data['plan'][0]['target']='/orders?slow=true'; data['requirements'][0]['checks'][0]['target']='/orders?slow=true'
    id=uid()
    # Hold job against the standard worker until our dedicated process owns it.
    with Session.begin() as s:s.add(Run(id=id,status='RUNNING',owner='recovery-controller',lease_until=now()+300,data=data,usage={}))
    cmd=['python','-c',f"import asyncio;from app.worker import process;asyncio.run(process('{id}','recovery-controller'))"]
    child=subprocess.Popen(cmd)
    until=time.monotonic()+40
    while time.monotonic()<until:
        with Session() as s:
            actions=list(s.scalars(select(Record).where(Record.run_id==id,Record.kind=='action')))
            pending=next((a for a in actions if a.data['state']=='reserved' and (a.data['check']['action_type']=='browser' if browser else a.data['check']['check_id'].startswith('slow-'))),None)
            usage=s.get(Run,id).usage
        if pending:break
        time.sleep(.05)
    else:child.terminate();raise AssertionError('No in-flight slow check')
    time.sleep(1 if browser else .3)
    child.kill();child.wait()
    charged=usage['actions']
    # Simulate lease timeout after actual process death, preserving all reservations.
    with Session.begin() as s:r=s.get(Run,id);r.lease_until=now()-1
    owner='recovery-resumer'
    reclaimed=claim(owner)
    if reclaimed==id:asyncio.run(process(id,owner))
    until=time.monotonic()+70
    while time.monotonic()<until:
        with Session() as s:
            r=s.get(Run,id);v=view(s,r)
            recovered=s.scalar(select(Event).where(Event.run_id==id,Event.kind=='recovered'))
        if r.status in ('FINISH_VERIFICATION','STOP_INCOMPLETE','FAILED_TO_RUN'):break
        time.sleep(.3)
    assert v['status']=='FINISH_VERIFICATION',v
    assert v['usage']['actions']>=charged
    assert any(a['state']=='interrupted' for a in v['actions'])
    assert recovered
    assert len({a['id'] for a in v['actions']})==len(v['actions'])
    result={'interrupted_tool':'browser' if browser else 'api','id':id,'status':v['status'],'recommendation':v['recommendation'],'actions_before_interrupt':charged,'actions_after_recovery':v['usage']['actions'],'interrupted_actions':sum(a['state']=='interrupted' for a in v['actions']),'recovery_event':True}
    open('/evidence/'+('browser-recovery-results.json' if browser else 'recovery-results.json'),'w').write(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':
    import sys
    main('--browser' in sys.argv)
