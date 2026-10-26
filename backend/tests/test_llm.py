"""Mocked provider-contract tests; these are not live inference validation."""
import asyncio,json
from types import SimpleNamespace
import pytest
from app.db import Session,Run,Record
from app.schemas import CreateRun
from app.llm import OpenRouterClient
import app.llm as llm

class Catalog:
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def get(self,url):
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'data':[{'id':'test/model','supported_parameters':['structured_outputs'],'pricing':{'prompt':'0.000001','completion':'0.000002'}}]})
class Usage:
    def model_dump(self):return {'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}
class Provider:
    calls=0
    fail=False
    def __init__(self,**kwargs):
        assert kwargs['base_url'].startswith('https://openrouter.ai/')
        assert kwargs['max_retries']==0
        self.chat=SimpleNamespace(completions=self)
    async def create(self,**kwargs):
        Provider.calls+=1
        if Provider.fail:raise RuntimeError('controlled provider failure')
        assert kwargs['response_format']['type']=='json_schema'
        return SimpleNamespace(model='test/model',usage=Usage(),choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'summary':'Suggested checks only','requirements':[],'risks':['Manual review remains']})))])
    async def close(self):pass

def setup(monkeypatch,calls=8,tokens=32000):
    monkeypatch.setenv('OPENROUTER_API_KEY','unit-test-key');monkeypatch.setenv('OPENROUTER_MODEL','test/model')
    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kwargs:Catalog());monkeypatch.setattr(llm,'AsyncOpenAI',Provider)
    Provider.calls=0;Provider.fail=False
    d=CreateRun().model_dump();d['budget']['llm_calls']=calls;d['budget']['llm_tokens']=tokens
    with Session.begin() as s:s.add(Run(id='llm-run',data=d,usage={},status='DRAFT'))
    return OpenRouterClient()

def test_provider_advice_usage_and_budget_reservation(monkeypatch):
    client=setup(monkeypatch)
    advice=asyncio.run(client.advise('llm-run','Planning',{'untrusted':'input'}))
    assert advice.summary=='Suggested checks only'
    with Session() as s:
        r=s.get(Run,'llm-run');assert r.usage['llm_calls']==1;assert r.usage['llm_tokens_reserved']>30
        logs=s.query(Record).filter_by(kind='llm_call').all();assert logs[0].data['usage']['total_tokens']==30
        assert logs[0].data['estimated_cost_usd']>0

def test_call_limit_blocks_retry(monkeypatch):
    client=setup(monkeypatch,calls=1);Provider.fail=True
    assert asyncio.run(client.advise('llm-run','Planning',{})) is None
    assert Provider.calls==1
    with Session() as s:assert s.get(Run,'llm-run').usage['llm_calls']==1

def test_token_limit_blocks_before_inference(monkeypatch):
    client=setup(monkeypatch,tokens=0)
    assert asyncio.run(client.advise('llm-run','Planning',{})) is None
    assert Provider.calls==0
