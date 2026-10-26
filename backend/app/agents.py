import json, time, hashlib, uuid
import httpx
from playwright.async_api import async_playwright
from .security import destination, artifact_path, redact
async def execute(base,check):
    url=destination(base,check['target']); start=time.monotonic(); artifacts=[]
    observation={'url':url,'expected_status':check['expected_status'],'errors':[],'network_failures':[]}
    if check['action_type']=='unsupported': return {'inconclusive':'Unsupported check'},[]
    if check['action_type'] in ('api','interaction'):
        method='POST' if check['action_type']=='interaction' else check.get('method','GET')
        if method=='POST' and check['target']!='/api/ack': raise ValueError('Mutating method prohibited')
        async with httpx.AsyncClient(timeout=12,follow_redirects=False,trust_env=False) as c:
            async with c.stream(method,url) as response:
                content=b''
                async for chunk in response.aiter_bytes():
                    content+=chunk
                    if len(content)>1024*1024: raise ValueError('Response size limit')
                observation['status']=response.status_code
                if 300<=response.status_code<400: raise ValueError('Redirect prohibited')
                try: observation['json']=json.loads(content)
                except ValueError: observation['body']=content.decode(errors='replace')[:10000]
        observation['method']=method
    else:
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
            context=await browser.new_context(service_workers='block',accept_downloads=False)
            async def route(r):
                try:
                    destination(base,r.request.url)
                    if r.request.method not in ('GET','HEAD'): raise ValueError('Browser mutation blocked')
                    await r.continue_()
                except ValueError: await r.abort()
            await context.route('**/*',route)
            page=await context.new_page(); page.on('pageerror',lambda e:observation['errors'].append(redact(e)[:500])); page.on('requestfailed',lambda r:observation['network_failures'].append(r.url[:500]))
            try:
                response=await page.goto(url,wait_until='domcontentloaded',timeout=12000)
                observation['status']=response.status if response else None
                if check.get('navigation_selector'):
                    await page.locator(check['navigation_selector']).first.click(timeout=3000)
                    await page.wait_for_load_state('domcontentloaded')
                observation['page_url']=page.url
                if check.get('selector'):
                    locator=page.locator(check['selector']); observation['visible']=await locator.count()>0 and await locator.first.is_visible()
                    observation['text']=await locator.first.inner_text(timeout=2000) if observation['visible'] else ''
                    if not observation['visible']: observation['inconclusive']='Selector absent; intended behavior could not be observed'
                else: observation['text']=(await page.locator('body').inner_text())[:20000]
                name=str(uuid.uuid4())+'.png'; await page.screenshot(path=str(artifact_path(name)),full_page=True); artifacts.append(name)
            finally: await context.close(); await browser.close()
    observation['latency_ms']=round((time.monotonic()-start)*1000)
    return observation,artifacts

def verify(check,observation):
    if observation.get('inconclusive') or observation.get('error'): return 'UNKNOWN'
    assertions=[observation.get('status')==check['expected_status']]
    if check['action_type']=='browser':
        if check.get('selector'): assertions.append(observation.get('visible') is True)
        if check.get('text'): assertions.append(check['text'] in observation.get('text',''))
    if check.get('expected_path'):
        from urllib.parse import urlsplit
        assertions.append(urlsplit(observation.get('page_url','')).path==check['expected_path'])
    types={'string':str,'number':(int,float),'boolean':bool,'array':list,'object':dict,'null':type(None)}
    for key,kind in check.get('json_shape',{}).items():
        payload=observation.get('json',{})
        assertions.append(isinstance(payload,dict) and key in payload and isinstance(payload[key],types[kind]) and not(kind=='number' and isinstance(payload[key],bool)))
    if check.get('json_path'):
        value=observation.get('json')
        try:
            for part in check['json_path'].split('.'): value=value[int(part)] if isinstance(value,list) else value[part]
            # JSON booleans are not numbers, despite Python's bool/int equality.
            expected=check['expected_value']
            assertions.append(json.dumps(value,sort_keys=True)==json.dumps(expected,sort_keys=True))
        except (KeyError,TypeError,IndexError,ValueError): assertions.append(False)
    return 'PASS' if all(assertions) else 'FAIL'

def write_artifact(observation):
    name=str(uuid.uuid4())+'.json'; artifact_path(name).write_text(json.dumps(observation,sort_keys=True,default=str)); return name

def artifact_meta(name):
    p=artifact_path(name)
    return {'name':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
