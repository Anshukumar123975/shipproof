from .db import now
TERMINAL={'FINISH_VERIFICATION','STOP_INCOMPLETE','CANCELLED','FAILED_TO_RUN'}
def before(run,check,approved=False):
    u=run.usage; b=run.data['budget']
    if now()>run.created+b['deadline_seconds']: return 'STOP_INCOMPLETE','Overall deadline exhausted'
    kind='browser' if check['action_type']=='browser' else 'api'
    if check.get('approval_requirement') and not approved: return 'WAITING_FOR_APPROVAL','Single-use action authorization required'
    if u.get('actions',0)>=b['actions'] or u.get(kind,0)>=b[kind] or u.get('active_seconds',0)>=b['active_seconds']:
        return 'STOP_INCOMPLETE','Hard execution budget exhausted'
    return 'CONTINUE','Action authorized; resources reserved before dispatch'
def after(policy,remaining,stagnant,replans):
    if not remaining: return 'FINISH_VERIFICATION','All planned checks reached terminal assessments'
    if stagnant>=2 and policy!='fixed':
        if policy=='progress' and replans<1: return 'REPLAN','Two inconclusive equivalent observations; use alternate permitted evidence strategy'
        return 'STOP_INCOMPLETE','Stagnation threshold reached'
    return 'CONTINUE','Permitted verification work remains'
def requirement_status(requirement,evidence):
    checks=requirement['checks']
    if not checks: return 'UNKNOWN'
    results={}
    for e in evidence:
        if e['requirement_id']!=requirement['id']: continue
        result=e['assertion_result']
        if result in ('PASS','FAIL') and not (e.get('action_id') and e.get('artifacts') and all(a.get('sha256') and a.get('name') for a in e['artifacts'])):
            result='UNKNOWN'
        results[e['check_id']]=result
    if any(v=='FAIL' for v in results.values()): return 'FAIL'
    if all(results.get(c['check_id'])=='PASS' for c in checks): return 'PASS'
    return 'UNKNOWN'
def recommendation(requirements,evidence,status):
    matrix=[{**r,'status':requirement_status(r,evidence)} for r in requirements]
    if any(r['status']=='FAIL' and r['priority']=='critical' for r in matrix): ready='BLOCKED'
    elif status=='FINISH_VERIFICATION' and all(r['status']=='PASS' for r in matrix): ready='READY'
    else: ready='NEEDS_REVIEW'
    return matrix,ready
