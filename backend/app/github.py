import re
import httpx
async def analyze(url):
    match=re.fullmatch(r'https://github.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?',url)
    if not match: raise ValueError('Expected a public GitHub pull request URL')
    owner,repo,number=match.groups(); path=f'https://api.github.com/repos/{owner}/{repo}/pulls/{number}'
    async with httpx.AsyncClient(timeout=20,follow_redirects=False,headers={'Accept':'application/vnd.github+json'}) as c:
        response=await c.get(path)
        if response.status_code in (403,429): raise ValueError('GitHub rate limit or access restriction; use a fixture or retry later')
        response.raise_for_status(); pr=response.json(); files=[]; incomplete=False
        for page in range(1,11):
            r=await c.get(path+'/files',params={'per_page':100,'page':page}); r.raise_for_status()
            batch=r.json(); files.extend([{k:f.get(k) for k in ('filename','status','additions','deletions','patch')} for f in batch])
            if len(batch)<100: break
        incomplete=len(files)<pr['changed_files'] or any(not f['patch'] for f in files)
    return {'title':pr['title'],'body':(pr.get('body') or '')[:16000],'base':pr['base']['sha'],'head':pr['head']['sha'],'files':files,'incomplete':incomplete,'fixture':False,'url':url}
def impact(snapshot,requirements):
    return [{'file':f['filename'],'component':f['filename'].split('/')[0], 'requirement_id':r['id'],'basis':'Path match' if any(p in f['filename'] for p in r['paths']) else 'Conservative scope inclusion','relationship':'heuristic','checks':[c['check_id'] for c in r['checks']]} for f in snapshot['files'] for r in requirements][:1000]
