import asyncio, time
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
app=FastAPI(title='Northstar Orders — bundled disposable staging')
ORDERS=[{'id':1001,'customer':'Ada Lovelace','total':125,'status':'Shipped'},{'id':1002,'customer':'Grace Hopper','total':89,'status':'Processing'}]
@app.get('/api/orders')
def orders(): return {'orders':ORDERS}
@app.get('/api/orders/1001')
def order(broken:bool=False): return {**ORDERS[0],'total':120 if broken else 125}
@app.get('/api/slow')
async def slow(checkpoint:int=0):
    await asyncio.sleep(3)
    return {'ok':True,'checkpoint':checkpoint}
@app.post('/api/ack')
def ack(): return {'acknowledged':True,'effect':'simulation only; no persistent mutation'}
@app.get('/')
@app.get('/orders')
@app.get('/orders/1001')
def page(slow:bool=False):
    if slow: time.sleep(3)
    return HTMLResponse('''<!doctype html><html><head><title>Northstar Orders</title><style>body{font:16px system-ui;background:#f4f5f7;color:#18232e;padding:60px}main{max-width:850px;margin:auto}article{padding:25px;background:white;margin:12px 0;border:1px solid #ddd;border-radius:12px}input{padding:12px;width:95%}a{color:#176d5b}</style></head><body><main><small>NORTHSTAR / STAGING</small><h1>Order history</h1><p>Seeded disposable demo. No sign-in required.</p><label>Search orders <input id="search" placeholder="Customer or order number" oninput="document.querySelectorAll('article').forEach(e=>e.hidden=!e.innerText.toLowerCase().includes(this.value.toLowerCase()))"></label><article id="order-1001"><a href="/orders/1001">Order #1001</a><h2>Ada Lovelace</h2><p>$125 · Shipped</p></article><article id="order-1002"><a href="/orders/1001">Order #1002</a><h2>Grace Hopper</h2><p>$89 · Processing</p></article></main></body></html>''')
