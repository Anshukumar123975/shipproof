from .schemas import Requirement, Check
SCENARIOS = {
 'verified': ('Order history is visible', 'Clean order list and API agree.'),
 'broken': ('Order totals regression', 'A seeded order total is incorrect.'),
 'incomplete': ('Accessibility audit gap', 'A manual assistive-technology check remains unknown.'),
 'repetitive': ('Stagnating selector', 'Repeated inconclusive observations trigger replanning.'),
 'approval': ('Disposable staging interaction', 'A simulated staging acknowledgement needs approval.'),
 'replanning_cost': ('Replanning without coverage gain', 'An alternate strategy consumes an extra action without resolving unsupported assertions.'),
 'interrupted': ('Worker recovery', 'Several slow checks permit controlled restart testing.'),
}
def fixture(name):
    if name not in SCENARIOS: raise ValueError('Unknown scenario')
    def req(i, desc, checks, priority='critical'):
        return Requirement(id=i,description=desc,priority=priority,paths=['src/orders.tsx'],checks=[Check(requirement_id=i,**c) for c in checks]).model_dump()
    browser={'check_id':'orders-visible','action_type':'browser','target':'/orders','selector':'#order-1001','text':'Order #1001'}
    api={'check_id':'order-api','action_type':'api','target':'/api/orders/1001','json_path':'total','expected_value':125}
    items=[req('R1','Seeded order #1001 appears in order history',[browser]), req('R2','Order #1001 has the correct total of 125',[api])]
    if name=='broken': items[1]['checks'][0]['target']+='?broken=true'
    if name=='incomplete': items.append(req('R3','A screen reader announces order changes',[], 'high'))
    if name=='repetitive':
        items=[req('R1','Order details can be read',[{'check_id':f'poll-{i}','action_type':'browser','target':'/orders','selector':'#never-present','text':'Order #1001','expected_outcome':'Wait for selector'} for i in range(6)])]+items[1:]
    if name=='replanning_cost':
        items=[req('R1','Unsupported observations remain explicit unknowns',[{'check_id':f'unsupported-{i}','action_type':'unsupported','target':'/orders'} for i in range(2)])]+items[1:]
    if name=='approval': items.append(req('R3','Acknowledge disposable test interaction',[{'check_id':'ack','action_type':'interaction','target':'/api/ack','approval_requirement':True,'json_path':'acknowledged','expected_value':True}]))
    if name=='interrupted':
        items += [req(f'R{i}',f'Recovery checkpoint {i}',[{'check_id':f'slow-{i}','action_type':'api','target':f'/api/slow?checkpoint={i}','json_path':'ok','expected_value':True}]) for i in range(3,8)]
    return {'title':SCENARIOS[name][0], 'body':SCENARIOS[name][1], 'base':'demo-base-001','head':f'demo-{name}-002','files':[{'filename':'src/orders.tsx','status':'modified','additions':12,'deletions':3,'patch':'@@ orders @@\n+ return orders.map(renderOrder)'}], 'incomplete':False, 'fixture':True, 'requirements':items}
def plan(requirements):
    checks=[]; seen=set()
    rank={'critical':0,'high':1,'medium':2,'low':3}
    for r in sorted(requirements,key=lambda r:rank[r['priority']]):
        for c in r['checks']:
            c=Check.model_validate(c).model_dump()
            c['requirement_id']=r['id']; c['priority']=r['priority']
            # Different check IDs can intentionally represent bounded polling in the repetitive fixture.
            key=c['check_id']
            if key not in seen: checks.append(c); seen.add(key)
    return checks
