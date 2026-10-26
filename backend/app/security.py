import hashlib, json, os
from urllib.parse import urlsplit, urljoin
from pathlib import Path
DEMO=os.getenv('DEMO_URL','http://demo:8001')
def fingerprint(payload):
    d={k:v for k,v in payload.items() if k not in ('check_id','estimated_cost','priority','expected_outcome')}
    return hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def destination(base, target):
    expected=urlsplit(DEMO); supplied=urlsplit(base)
    if supplied != expected: raise ValueError('External execution disabled: only the configured bundled demo is authorized')
    url=urljoin(base,target); p=urlsplit(url)
    if p.scheme!='http' or p.netloc!=expected.netloc or p.username or p.password or p.fragment:
        raise ValueError('Destination outside demo allowlist')
    if p.path not in ('/orders','/orders/1001','/api/orders','/api/orders/1001','/api/slow','/api/ack','/'):
        raise ValueError('Unsupported demo route')
    return url
def artifact_path(name):
    root=Path(os.getenv('ARTIFACT_ROOT','/tmp/shipproof-evidence')).resolve()
    root.mkdir(parents=True,exist_ok=True)
    p=(root/name).resolve()
    if p.parent!=root or p.suffix not in ('.json','.png'): raise ValueError('Invalid artifact path')
    return p
def redact(value):
    text=str(value)
    for key in ('OPENROUTER_API_KEY','GITHUB_TOKEN'):
        secret=os.getenv(key)
        if secret: text=text.replace(secret,'[REDACTED]')
    return text
