"""Measured, reproducible policy suite. No simulated outputs or costs."""
import httpx,time,json
from pathlib import Path
c=httpx.Client(base_url='http://api:8000',timeout=30)
def main():
    output=[]
    for scenario,ceiling in [('verified',12),('broken',12),('incomplete',12),('repetitive',12),('replanning_cost',12),('verified',1)]:
        r=c.post('/api/experiments/compare',json={'scenario':scenario,'actions':ceiling});r.raise_for_status();id=r.json()['id']
        until=time.monotonic()+180
        while time.monotonic()<until:
            result=c.get('/api/experiments/'+id).json()
            if all(m['status'] in ('FINISH_VERIFICATION','STOP_INCOMPLETE','FAILED_TO_RUN','CANCELLED') for m in result['metrics']):break
            time.sleep(.5)
        else:raise AssertionError('Benchmark timeout')
        output.append({'scenario':scenario,'action_ceiling':ceiling,'experiment_id':id,'metrics':result['metrics']})
    Path('/evidence/benchmark-results.json').write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))
if __name__=='__main__':main()
