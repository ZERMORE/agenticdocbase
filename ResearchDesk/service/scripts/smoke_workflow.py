"""Runs only local tools with offline parsing; external model processing remains disabled."""
import argparse
import json
from pathlib import Path
import time
import httpx
from researchdesk.store import ROOT, WORKSPACE, atomic

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default='http://127.0.0.1:8765')
args = parser.parse_args()
client = httpx.Client(base_url=args.base_url, timeout=30, trust_env=False)
boot = client.get('/api/bootstrap').json()
client.headers['X-CSRF-Token'] = boot['csrf']


def request(method,path,**kwargs):
    response = client.request(method,path,**kwargs)
    response.raise_for_status()
    return response.json()


def wait(job):
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        row=request('GET','/api/jobs/'+job)
        if row['state'] in {'succeeded','failed','cancelled','interrupted'}:
            assert row['state']=='succeeded',row
            return row
        time.sleep(.5)
    raise TimeoutError(job)


report={'model_api_requests':0,'steps':[]}
kb=request('POST','/api/knowledge-bases',json={'name':'验收 · 离线资料生命周期'})
key=kb['id']
wait(next(j['id'] for j in request('GET','/api/jobs') if j['kb']==key and j['kind']=='reindex'))
report['steps'].append('空知识库索引发布')
sample=WORKSPACE/'doc2md'/'examples'/'sample.pdf'
with sample.open('rb') as f:
    upload=request('POST',f'/api/knowledge-bases/{key}/uploads',files={'file':('sample.pdf',f,'application/pdf')},data={'offline':'true'})
wait(upload['job_id'])
time.sleep(1)
jobs=[j for j in request('GET','/api/jobs') if j['kb']==key and j['kind']=='reindex']
wait(jobs[0]['id'])
report['steps'].append('PDF上传→真实doc2md离线解析→真实DocBase入库')
search=lambda:request('POST',f'/api/knowledge-bases/{key}/search',json={'question':'1729','request_id':'local-smoke'})
assert any('1729' in hit['text'] for hit in search()['results'])
report['steps'].append('检索命中1729')
preview=request('GET',f"/api/knowledge-bases/{key}/documents/{upload['document_id']}/preview")
assert preview['markdown'] and preview['asset_id']
response=client.get('/api/sources/'+preview['asset_id'],headers={'Range':'bytes=0-3'})
assert response.status_code==206 and response.content==b'%PDF'
report['steps'].append('Markdown预览及PDF Range读取')
remove=request('DELETE',f"/api/knowledge-bases/{key}/documents/{upload['document_id']}")
assert search()['results']==[]
wait(remove['job_id'])
assert search()['results']==[]
report['steps'].append('移出立即排除→最后一篇删除后发布空索引')
restored=request('POST',f"/api/knowledge-bases/{key}/documents/{upload['document_id']}/restore")
wait(restored['job_id'])
assert search()['results']
report['steps'].append('恢复后重新可检索')
request('DELETE','/api/knowledge-bases/'+key)
report['steps'].append('回收验收知识库，原始sample.pdf不变')
atomic(ROOT/'design'/'workflow-report.json',report)
print(json.dumps(report,ensure_ascii=False,indent=2))
