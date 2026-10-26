import asyncio
from types import SimpleNamespace
import pytest
from app.schemas import CreateRun,Check
from app.scenarios import fixture
from app.controller import before,after,requirement_status,recommendation
from app.agents import verify
from app.security import destination,fingerprint,artifact_path,redact
from app.db import now,Session,Run,Record
from app.service import record,report
from app.worker import claim

@pytest.mark.parametrize('scenario',['verified','broken','incomplete','repetitive','approval','interrupted'])
def test_fixture_validates(scenario):
    data=fixture(scenario)
    CreateRun(requirements=data['requirements'])

def test_no_evidence_cannot_pass():
    r=fixture('verified')['requirements'][0]
    assert requirement_status(r,[])=='UNKNOWN'

def test_all_checks_required():
    r=fixture('verified')['requirements'][0];r['checks'].append({**r['checks'][0],'check_id':'other'})
    ev=[{'requirement_id':'R1','check_id':'orders-visible','assertion_result':'PASS'}]
    assert requirement_status(r,ev)=='UNKNOWN'

@pytest.mark.parametrize('assertion,status',[('PASS','PASS'),('FAIL','FAIL'),('UNKNOWN','UNKNOWN')])
def test_status_propagation(assertion,status):
    r=fixture('verified')['requirements'][0]
    assert requirement_status(r,[{'requirement_id':'R1','check_id':'orders-visible','assertion_result':assertion,'action_id':'a','artifacts':[{'name':'proof.json','sha256':'hash'}]}])==status

@pytest.mark.parametrize('statuses,run_status,expected',[
    (['PASS','PASS'],'FINISH_VERIFICATION','READY'),
    (['PASS','FAIL'],'FINISH_VERIFICATION','BLOCKED'),
    (['PASS','UNKNOWN'],'FINISH_VERIFICATION','NEEDS_REVIEW'),
    (['PASS','PASS'],'STOP_INCOMPLETE','NEEDS_REVIEW')])
def test_release_rules(statuses,run_status,expected):
    req=fixture('verified')['requirements'];ev=[{'requirement_id':r['id'],'check_id':r['checks'][0]['check_id'],'assertion_result':v,'action_id':'a','artifacts':[{'name':'proof.json','sha256':'hash'}]} for r,v in zip(req,statuses)]
    assert recommendation(req,ev,run_status)[1]==expected

def make_run(**usage):
    return SimpleNamespace(created=now(),data={'budget':CreateRun().budget.model_dump()},usage=usage)

@pytest.mark.parametrize('usage,check_type',[({'actions':20},'api'),({'browser':10},'browser'),({'api':20},'api'),({'active_seconds':180},'api')])
def test_hard_budgets(usage,check_type):
    assert before(make_run(**usage),{'action_type':check_type})[0]=='STOP_INCOMPLETE'

def test_approval_order_and_deadline():
    r=make_run(actions=20); c={'action_type':'interaction','approval_requirement':True}
    assert before(r,c)[0]=='WAITING_FOR_APPROVAL'
    assert before(r,c,True)[0]=='STOP_INCOMPLETE'
    r.created-=2000
    assert before(r,c)[0]=='STOP_INCOMPLETE'

@pytest.mark.parametrize('policy,stagnant,replans,expected',[
    ('fixed',3,0,'CONTINUE'),('stagnation',2,0,'STOP_INCOMPLETE'),('progress',2,0,'REPLAN'),('progress',2,1,'STOP_INCOMPLETE'),('progress',1,0,'CONTINUE')])
def test_stopping_policies(policy,stagnant,replans,expected): assert after(policy,1,stagnant,replans)[0]==expected

def test_completion_precedes_stagnation(): assert after('progress',0,99,5)[0]=='FINISH_VERIFICATION'

@pytest.mark.parametrize('base,target',[
    ('https://example.com','/'),('file:///etc/passwd','/'),('http://169.254.169.254','/'),('http://demo:8001','http://evil.test/'),('http://demo:8001','//evil.test/'),('http://demo:8001','/admin'),('http://demo:8001','http://user:pass@demo:8001/orders')])
def test_unauthorized_targets(base,target):
    with pytest.raises(ValueError): destination(base,target)

def test_destination_allowed(): assert destination('http://demo:8001','/orders')=='http://demo:8001/orders'

@pytest.mark.parametrize('path',['../secret.json','sub/file.png','/etc/passwd','secret.txt'])
def test_artifact_path_traversal(path):
    with pytest.raises(ValueError): artifact_path(path)

def test_secret_redaction(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','sk-test-private')
    assert 'sk-test-private' not in redact('problem sk-test-private')

def test_canonical_fingerprint():
    a={'target':'/orders','action_type':'browser','check_id':'a'}; b={**a,'check_id':'b'}
    assert fingerprint(a)==fingerprint(b)
    assert fingerprint(a)!=fingerprint({**b,'target':'/orders/1001'})

def test_200_is_not_business_evidence():
    c=fixture('verified')['requirements'][1]['checks'][0]
    assert verify(c,{'status':200,'json':{'total':120}})=='FAIL'
    assert verify(c,{'status':200,'json':{'total':125}})=='PASS'
    assert verify(c,{'status':200})=='FAIL'
    assert verify(c,{'error':'timeout'})=='UNKNOWN'

def test_interaction_forces_approval():
    c=Check(check_id='c',requirement_id='r',action_type='interaction')
    assert c.approval_requirement and c.method=='POST' and c.risk_level=='disposable-interaction'

def test_restart_retains_reservation_and_prevents_duplicate_claim():
    c=fixture('verified')['requirements'][0]['checks'][0]
    with Session.begin() as s:
        r=Run(id='run',status='CONTINUE',data={'requirements':fixture('verified')['requirements'],'snapshot':fixture('verified')},usage={'actions':1,'browser':1},owner='dead-worker',lease_until=now()-1);s.add(r)
        record(s,'action','run',{'state':'reserved','started':now()-20,'check':c,'fingerprint':'fp'})
    assert claim('new-worker')=='run'
    assert claim('duplicate-worker') is None
    with Session() as s:
        r=s.get(Run,'run'); assert r.usage['actions']==1
        assert report(s,r)['requirements'][0]['status']=='UNKNOWN'
        assert s.query(Record).filter_by(kind='action').one().data['state']=='interrupted'

@pytest.mark.parametrize('claimed',['PASS','FAIL'])
def test_classification_requires_executed_artifact(claimed):
    r=fixture('verified')['requirements'][0]
    assert requirement_status(r,[{'requirement_id':'R1','check_id':'orders-visible','assertion_result':claimed}])=='UNKNOWN'

def test_json_shape_and_navigation_assertions():
    api_check=Check(check_id='shape',requirement_id='R',action_type='api',json_shape={'orders':'array'}).model_dump()
    assert verify(api_check,{'status':200,'json':{'orders':[]}})=='PASS'
    assert verify(api_check,{'status':200,'json':{'orders':{}}})=='FAIL'
    nav_check=Check(check_id='navigate',requirement_id='R',action_type='browser',expected_path='/orders/1001').model_dump()
    assert verify(nav_check,{'status':200,'page_url':'http://demo:8001/orders/1001'})=='PASS'
    assert verify(nav_check,{'status':200,'page_url':'http://demo:8001/orders'})=='FAIL'

def test_api_mutation_method_rejected_by_schema():
    with pytest.raises(ValueError): Check(check_id='c',requirement_id='r',action_type='api',method='POST')

def test_json_boolean_does_not_match_number():
    c=Check(check_id='number',requirement_id='R',action_type='api',json_path='count',expected_value=1).model_dump()
    assert verify(c,{'status':200,'json':{'count':True}})=='FAIL'
