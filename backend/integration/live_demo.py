"""Real PostgreSQL/API/browser demonstration. Run inside the worker container."""
import json,time,os,hashlib
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
API=os.getenv('TEST_API','http://api:8000')
UI=os.getenv('TEST_UI','http://frontend')
c=httpx.Client(base_url=API,timeout=30)
TERMINAL={'FINISH_VERIFICATION','STOP_INCOMPLETE','CANCELLED','FAILED_TO_RUN'}
def create(scenario,**kwargs):
    r=c.post('/api/verifications',json={'authorized':True,'scenario':scenario,**kwargs});r.raise_for_status();return r.json()
def wait(id,expected=None,timeout=120):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        r=c.get('/api/verifications/'+id).json()
        if (expected and r['status']==expected) or (not expected and r['status'] in TERMINAL): return r
        time.sleep(.3)
    raise AssertionError('Run did not finish: '+json.dumps(r))

def main():
    results={}
    for scenario,expected in [('verified','READY'),('broken','BLOCKED'),('incomplete','NEEDS_REVIEW'),('repetitive','NEEDS_REVIEW')]:
        r=wait(create(scenario)['id']);assert r['recommendation']==expected,(scenario,r)
        assert any(e['artifacts'] for e in r['evidence'])
        for e in r['evidence']:
            if e['assertion_result'] in ('PASS','FAIL'): assert e['artifacts']
            for a in e['artifacts']:
                data=c.get('/api/artifacts/'+a['name']).content
                assert hashlib.sha256(data).hexdigest()==a['sha256']
        exported=c.get('/api/verifications/'+r['id']+'/export');assert exported.status_code==200 and expected in exported.text
        results[scenario]={'id':r['id'],'recommendation':r['recommendation'],'coverage':r['coverage'],'usage':r['usage']}
    r=wait(create('approval')['id'],'WAITING_FOR_APPROVAL')
    assert not any(e['check_id']=='ack' for e in r['evidence'])
    a=next(a for a in r['approvals'] if a['state']=='pending')
    assert c.post('/api/approvals/'+a['id']+'/approve',json={'fingerprint':'wrong'}).status_code==409
    assert c.post('/api/approvals/'+a['id']+'/approve',json={'fingerprint':a['fingerprint']}).status_code==200
    r=wait(r['id']);assert r['recommendation']=='READY'
    assert c.post('/api/approvals/'+a['id']+'/approve',json={'fingerprint':a['fingerprint']}).status_code==409
    results['approval']={'id':r['id'],'recommendation':r['recommendation'],'approval_state':r['approvals'][0]['state']}
    exp=c.post('/api/experiments/compare',json={'scenario':'repetitive','actions':12});exp.raise_for_status();eid=exp.json()['id']
    for rid in exp.json()['run_ids']:wait(rid)
    comparison=c.get('/api/experiments/'+eid).json();results['comparison']=comparison['metrics'];results['experiment_id']=eid
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(UI);page.get_by_role('heading',name='Know what actually works.').wait_for()
        page.get_by_role('button',name='New verification',exact=True).last.click()
        page.get_by_label('I am authorized').check()
        page.get_by_role('button',name='Generate verification plan').click()
        page.get_by_role('heading',name='Order history is visible',exact=True).wait_for()
        page.get_by_role('button',name='Start verification').click()
        page.get_by_role('heading',name='Order history is visible',exact=True).wait_for()
        page.wait_for_function("document.body.innerText.includes('FINISH VERIFICATION')",timeout=60000)
        page.screenshot(path='/evidence/dashboard-live.png',full_page=True)
        page.get_by_role('button',name='View report').click()
        page.get_by_role('heading',name='Scope & remaining risk').wait_for()
        page.locator('summary').first.click()
        page.locator('.artifact-list img').first.wait_for()
        page.screenshot(path='/evidence/dashboard-report.png',full_page=True)
        page.get_by_role('button',name='Policy comparison',exact=True).click()
        page.get_by_role('heading',name='Measure the stopping decision.').wait_for()
        page.get_by_role('button',name='Run comparison',exact=True).click()
        page.get_by_role('heading',name='Measured operations & assessed requirements').wait_for()
        page.screenshot(path='/evidence/dashboard-lab.png',full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.goto(UI+'/#overview')
        page.get_by_role('heading',name='Know what actually works.').wait_for()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors,errors
        browser.close()
    results['browser_ui']='Passed desktop workflow, evidence view, lab, and mobile overflow check'
    Path('/evidence/demo-results.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
if __name__=='__main__':main()
