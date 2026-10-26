import asyncio,json
import httpx
from app.github import analyze
async def main():
    async with httpx.AsyncClient(timeout=20) as c:
        response=await c.get('https://api.github.com/repos/pydantic/pydantic/pulls',params={'state':'closed','per_page':1});response.raise_for_status()
        url=response.json()[0]['html_url']
    result=await analyze(url)
    summary={'url':url,'title':result['title'],'changed_files':len(result['files']),'incomplete':result['incomplete'],'head':result['head']}
    open('/evidence/github-results.json','w').write(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
asyncio.run(main())
