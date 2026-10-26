import asyncio, json, os
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, Response, FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from .db import Session, Run, Record, Event, Setting, now
from .schemas import CreateRun, Requirement
from .scenarios import SCENARIOS, fixture, plan
from .github import analyze, impact
from .security import destination, artifact_path
from .service import uid, record, records, event, transition, view, report, markdown
from .controller import TERMINAL
from .llm import OpenRouterClient

app=FastAPI(title='ShipProof',version='0.1.0',description='Evidence-backed, scope-limited release verification. Local single-user deployment.')
@app.exception_handler(ValueError)
async def bad_value(request,exc): return JSONResponse(status_code=422,content={'detail':str(exc)})
@app.exception_handler(httpx.HTTPError)
async def upstream_error(request,exc): return JSONResponse(status_code=502,content={'detail':'Upstream service unavailable or request rejected. Try a demo fixture or retry later.'})
@app.middleware('http')
async def local_security(request,call_next):
    # Same-origin browser writes; local API clients can omit Origin.
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin and origin not in ('http://localhost:5178','http://127.0.0.1:5178','http://frontend',os.getenv('SHIPPROOF_UI_ORIGIN','http://localhost:5178')):
        return JSONResponse(status_code=403,content={'detail':'Untrusted browser origin'})
    return await call_next(request)
def get_run(s,id):
    r=s.get(Run,id)
    if not r: raise HTTPException(404,'Verification not found')
    return r
async def prepare(body):
    if not body.authorized: raise ValueError('Confirm authorization to test the disposable staging environment')
    destination(body.staging_url,'/')
    snapshot=await analyze(body.pr_url) if body.pr_url else fixture(body.scenario)
    requirements=[r.model_dump() for r in body.requirements] or snapshot.get('requirements',[])
    if body.natural_requirements:
        requirements += [Requirement(id=f'N{i}',description=line.strip(),priority='high').model_dump() for i,line in enumerate(body.natural_requirements.splitlines(),1) if line.strip()]
    if not requirements: raise ValueError('At least one requirement is required')
    if len({r['id'] for r in requirements})!=len(requirements): raise ValueError('Requirement IDs must be unique')
    checks=plan(requirements)
    if sum(len(r['checks']) for r in requirements)!=len(checks): raise ValueError('Check IDs must be globally unique')
    for c in checks: destination(body.staging_url,c['target'])
    return {**body.model_dump(),'snapshot':snapshot,'requirements':requirements,'plan':checks,'impact':impact(snapshot,requirements),'mode':'OpenRouter-assisted' if body.ai and os.getenv('OPENROUTER_API_KEY') else 'Deterministic — no AI inference'}
@app.get('/api/health')
def health():
    with Session() as s: s.execute(select(1))
    return {'status':'ok','external_execution':False,'ai_configured':bool(os.getenv('OPENROUTER_API_KEY'))}
@app.get('/api/scenarios')
def scenarios(): return [{'id':k,'title':v[0],'description':v[1],'fixture':fixture(k)} for k,v in SCENARIOS.items()]
@app.post('/api/verifications/preview')
async def preview(body:CreateRun): return await prepare(body)
@app.post('/api/verifications',status_code=201)
async def create(body:CreateRun):
    data=await prepare(body); id=uid()
    with Session.begin() as s:
        r=Run(id=id,status='DRAFT' if body.ai else 'QUEUED',data=data,usage={}); s.add(r); s.flush()
        record(s,'project',id,{'name':data['snapshot']['title']}); record(s,'pr_snapshot',id,data['snapshot'])
        event(s,r,'created',{'mode':data['mode']})
    if body.ai:
        advice=await OpenRouterClient().advise(id,'Requirement interpretation and verification plan generation',{'snapshot':data['snapshot'],'requirements':data['requirements']})
        with Session.begin() as s:
            r=s.get(Run,id); d=dict(r.data)
            if advice:
                d['planning_advice']=advice.model_dump()
                # AI suggestions must be reviewed; never silently dispatch generated actions.
                d['suggested_requirements']=[x.model_dump() for x in advice.requirements]
            r.data=d; event(s,r,'plan_review_required',{'reason':'Review AI-suggested checks before starting'})
    with Session() as s: return view(s,s.get(Run,id))
class StartPlan(BaseModel):
    accept_suggestions: bool=False
@app.post('/api/verifications/{id}/start')
def start(id:str,body:StartPlan):
    with Session.begin() as s:
        r=s.scalar(select(Run).where(Run.id==id).with_for_update())
        if not r: raise HTTPException(404)
        if r.status!='DRAFT': raise HTTPException(409,'Run is not awaiting plan review')
        d=dict(r.data)
        if body.accept_suggestions:
            suggestions=d.get('suggested_requirements',[])
            for req in suggestions:
                for c in req['checks']: destination(d['staging_url'],c['target'])
            if suggestions:
                validated=[Requirement.model_validate(x).model_dump() for x in suggestions]
                if len({x['id'] for x in validated})!=len(validated): raise ValueError('Duplicate suggested requirement IDs')
                d['requirements']=validated; d['plan']=plan(validated); d['impact']=impact(d['snapshot'],validated)
        r.data=d; transition(s,r,'QUEUED','User reviewed verification plan')
    return {'id':id,'status':'QUEUED'}
@app.get('/api/verifications')
def list_runs(q:str='',status:str='',policy:str='',offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
    with Session() as s:
        runs=s.scalars(select(Run).order_by(Run.created.desc())).all()
        result=[view(s,r) for r in runs if (not q or q.lower() in json.dumps(r.data).lower()) and (not policy or r.data['policy']==policy)]
        if status: result=[r for r in result if r['recommendation']==status or r['status']==status]
        return {'items':result[offset:offset+limit],'total':len(result)}
@app.get('/api/verifications/{id}')
def detail(id:str):
    with Session() as s: return view(s,get_run(s,id))
@app.get('/api/verifications/{id}/requirements')
def requirements(id:str):
    with Session() as s: return view(s,get_run(s,id))['requirements']
@app.get('/api/verifications/{id}/evidence')
def evidence(id:str):
    with Session() as s: get_run(s,id); return records(s,id,'evidence')
@app.get('/api/verifications/{id}/events')
async def events(id:str,request:Request,after:int=0):
    with Session() as s: get_run(s,id)
    last=max(after,int(request.headers.get('last-event-id','0')))
    async def stream():
        nonlocal last
        while not await request.is_disconnected():
            with Session() as s:
                rows=list(s.scalars(select(Event).where(Event.run_id==id,Event.id>last).order_by(Event.id).limit(100)))
                status=s.get(Run,id).status
            for e in rows:
                last=e.id
                yield f'id: {e.id}\ndata: {json.dumps({"id":e.id,"kind":e.kind,"data":e.data,"created":e.created})}\n\n'
            if status in TERMINAL and not rows: return
            yield ': heartbeat\n\n'
            await asyncio.sleep(0.75)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
@app.post('/api/verifications/{id}/cancel')
def cancel(id:str):
    with Session.begin() as s:
        r=s.scalar(select(Run).where(Run.id==id).with_for_update())
        if not r: raise HTTPException(404)
        if r.status not in TERMINAL: transition(s,r,'CANCELLED','Cancelled by user; in-flight read-only operation may finish recording evidence')
    return {'status':'CANCELLED'}
@app.get('/api/verifications/{id}/report')
def get_report(id:str):
    with Session() as s: return report(s,get_run(s,id))
@app.get('/api/verifications/{id}/export')
def export(id:str):
    with Session() as s: text=markdown(report(s,get_run(s,id)))
    return Response(text,media_type='text/markdown',headers={'Content-Disposition':f'attachment; filename="shipproof-{id}.md"'})
@app.get('/api/artifacts/{name}')
def artifact(name:str):
    path=artifact_path(name)
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path,headers={'X-Content-Type-Options':'nosniff'})
@app.get('/api/approvals')
def approvals():
    with Session() as s: return [{'id':r.id,'run_id':r.run_id,**r.data} for r in s.scalars(select(Record).where(Record.kind=='approval'))]
class ApprovalPayload(BaseModel):
    fingerprint: str
@app.post('/api/approvals/{id}/{decision}')
def approve(id:str,decision:str,body:ApprovalPayload):
    if decision not in ('approve','reject'): raise HTTPException(404)
    with Session.begin() as s:
        # Consistent lock ordering with worker: run first, then approval.
        lookup=s.get(Record,id)
        if not lookup or lookup.kind!='approval': raise HTTPException(404)
        r=s.scalar(select(Run).where(Run.id==lookup.run_id).with_for_update())
        a=s.scalar(select(Record).where(Record.id==id).with_for_update()); d=dict(a.data)
        if r.status!='WAITING_FOR_APPROVAL' or d['state']!='pending' or d['expires']<=now(): raise HTTPException(409,'Approval expired or already used')
        if body.fingerprint!=d['fingerprint']: raise HTTPException(409,'Action fingerprint mismatch')
        if now()>r.created+r.data['budget']['deadline_seconds']: raise HTTPException(409,'Run deadline expired')
        d['state']='approved' if decision=='approve' else 'rejected'; a.data=d
        if decision=='approve': transition(s,r,'QUEUED','Request-bound approval accepted'); r.owner=''; r.lease_until=0
        else: transition(s,r,'STOP_INCOMPLETE','Human rejected staging action')
    return {'state':d['state']}
@app.get('/api/policies')
def policies(): return [{'id':'fixed','description':'Execute until plan completion or hard ceiling'},{'id':'stagnation','description':'Stop after two consecutive inconclusive observations'},{'id':'progress','description':'Replan once, preserving unresolved original requirements'}]
class Comparison(BaseModel):
    scenario: str='repetitive'
    actions:int=Field(default=12,ge=1,le=30)
@app.post('/api/experiments/compare',status_code=201)
async def compare(body:Comparison):
    ids=[]
    for policy in ('fixed','stagnation','progress'):
        request=CreateRun(scenario=body.scenario,policy=policy,authorized=True)
        request.budget.actions=body.actions
        r=await create(request); ids.append(r['id'])
    id=uid()
    with Session.begin() as s: record(s,'experiment','',{'scenario':body.scenario,'run_ids':ids,'same_resource_ceilings':True},id=id)
    return {'id':id,'run_ids':ids}
@app.get('/api/experiments/{id}')
def experiment(id:str):
    with Session() as s:
        e=s.get(Record,id)
        if not e or e.kind!='experiment': raise HTTPException(404)
        runs=[view(s,s.get(Run,i)) for i in e.data['run_ids']]
        return {'id':id,**e.data,'runs':runs,'metrics':[{'policy':r['data']['policy'],'coverage':r['coverage'],'requirements_assessed':sum(x['status']!='UNKNOWN' for x in r['requirements']),'tool_operations':r['usage'].get('actions',0),'redundant_actions':r['usage'].get('redundant_actions',0),'active_seconds':r['usage'].get('active_seconds',0),'controller_seconds':r['usage'].get('controller_seconds',0),'actions_per_assessed_requirement':r['usage'].get('actions',0)/sum(x['status']!='UNKNOWN' for x in r['requirements']) if any(x['status']!='UNKNOWN' for x in r['requirements']) else None,'unknown':sum(x['status']=='UNKNOWN' for x in r['requirements']),'replans':r['usage'].get('replans',0),'estimated_llm_cost_usd':sum(x.get('estimated_cost_usd',0) for x in r['llm_calls']),'status':r['status']} for r in runs]}
@app.get('/api/architecture')
def architecture(): return {'nodes':['PR snapshot','Requirements','Impact mapping','Planner','External execution controller','Browser / HTTP agents','Independent assertion verifier','Evidence ledger','Progress evaluator','PostgreSQL + leased worker','Release report'],'principles':['Models propose; deterministic assertions establish evidence.','The controller validates permissions and reserves budgets before every action.','UNKNOWN is never counted as PASS.','Every artifact carries a SHA-256 content hash.'],'external_execution':False}
class SettingsInput(BaseModel):
    model:str=Field(min_length=3,max_length=150,pattern=r'^[a-zA-Z0-9_./:-]+$')
@app.get('/api/settings')
def settings():
    c=OpenRouterClient(); return {'model':c.model,'configured':bool(c.key),'base_url':c.base,'secrets':'API key is read only from server environment'}
@app.put('/api/settings')
def update_settings(body:SettingsInput):
    with Session.begin() as s:
        item=s.get(Setting,'model')
        if item: item.value=body.model
        else: s.add(Setting(id='model',value=body.model))
    return settings()
