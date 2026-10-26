import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session,Run,Record,now
from app.service import record
from app.security import fingerprint
client=TestClient(app)
def create(scenario='verified',**kwargs):
    response=client.post('/api/verifications',json={'scenario':scenario,'authorized':True,**kwargs})
    assert response.status_code==201,response.text
    return response.json()
def test_product_api_flow():
    response=client.post('/api/verifications/preview',json={'scenario':'verified','authorized':True});assert response.status_code==200
    assert len(response.json()['plan'])==2
    run=create();id=run['id']
    assert client.get('/api/verifications').json()['total']==1
    assert client.get(f'/api/verifications/{id}/requirements').json()[0]['status']=='UNKNOWN'
    assert client.get(f'/api/verifications/{id}/report').json()['recommendation']=='NEEDS_REVIEW'
    assert '# ShipProof' in client.get(f'/api/verifications/{id}/export').text
    assert client.post(f'/api/verifications/{id}/cancel').status_code==200
    assert client.get(f'/api/verifications/{id}').json()['status']=='CANCELLED'

def test_preview_requires_authorization(): assert client.post('/api/verifications/preview',json={}).status_code==422

def test_external_target_rejected(): assert client.post('/api/verifications',json={'authorized':True,'staging_url':'https://example.com'}).status_code==422

def test_duplicate_requirement_ids_rejected():
    req={'id':'same','description':'test','checks':[]}
    assert client.post('/api/verifications',json={'authorized':True,'requirements':[req,req]}).status_code==422

def approval_run(expires=None):
    r=create('approval');id=r['id'];check=r['data']['plan'][-1]
    fp=fingerprint({'run_id':id,'environment':r['data']['staging_url'],**check})
    with Session.begin() as s:
        run=s.get(Run,id);run.status='WAITING_FOR_APPROVAL'
        a=record(s,'approval',id,{'fingerprint':fp,'state':'pending','expires':expires or now()+600,'payload':check,'environment':r['data']['staging_url']});aid=a.id
    return id,aid,fp

def test_single_use_approval_and_mismatch():
    id,aid,fp=approval_run()
    assert client.post('/api/approvals/'+aid+'/approve',json={'fingerprint':'wrong'}).status_code==409
    assert client.post('/api/approvals/'+aid+'/approve',json={'fingerprint':fp}).status_code==200
    assert client.post('/api/approvals/'+aid+'/approve',json={'fingerprint':fp}).status_code==409
    assert client.get('/api/verifications/'+id).json()['status']=='QUEUED'

def test_expired_approval_rejected():
    id,aid,fp=approval_run(now()-1)
    assert client.post('/api/approvals/'+aid+'/approve',json={'fingerprint':fp}).status_code==409

def test_rejected_approval_stops_run():
    id,aid,fp=approval_run()
    assert client.post('/api/approvals/'+aid+'/reject',json={'fingerprint':fp}).status_code==200
    assert client.get('/api/verifications/'+id).json()['status']=='STOP_INCOMPLETE'

def test_origin_protection():
    assert client.post('/api/verifications',json={'authorized':True},headers={'origin':'https://evil.test'}).status_code==403

def test_comparison_uses_equal_ceilings():
    e=client.post('/api/experiments/compare',json={'scenario':'repetitive','actions':7}).json()
    data=client.get('/api/experiments/'+e['id']).json()
    assert len(data['runs'])==3
    assert all(r['data']['budget']['actions']==7 for r in data['runs'])
    assert all(r['usage']=={} for r in data['runs'])

def test_ai_without_key_labels_deterministic_and_requires_review():
    r=create(ai=True)
    assert r['data']['mode'].startswith('Deterministic')
    assert r['status']=='DRAFT'
    assert client.post('/api/verifications/'+r['id']+'/start',json={}).status_code==200

def test_settings_do_not_expose_secrets():
    r=client.put('/api/settings',json={'model':'google/gemini-2.5-flash'})
    assert r.status_code==200
    assert 'api_key' not in r.text.lower()
