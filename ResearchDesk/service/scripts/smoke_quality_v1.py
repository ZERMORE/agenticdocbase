"""Real offline tools in an isolated data directory. No external model calls."""
import hashlib
import json
from pathlib import Path
import time
from fastapi.testclient import TestClient
from researchdesk.store import Store,ROOT,WORKSPACE,uid,atomic,dump
from researchdesk.api import create_app
from researchdesk.worker import Worker
from researchdesk.processes import invoke
from researchdesk.manage import backup,restore

folder=ROOT/'data'/'validation'/uid('quality_v1')
s=Store(folder)
(s.data/'docbase_storage').mkdir()
c=TestClient(create_app(s,seed=False),base_url='http://127.0.0.1')
c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf']
def request(method,url,**kwargs):
 r=c.request(method,url,**kwargs);r.raise_for_status();return r.json()
def drain():
 for _ in range(30):
  if not Worker(s).once():break
 else:raise AssertionError('queue did not finish')
 bad=s.all("SELECT id,state,error FROM jobs WHERE state NOT IN ('succeeded','cancelled')")
 assert not bad,bad

def hashes(path):
 return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
report={'model_api_requests':0,'steps':[],'data':str(folder)}
k=request('POST','/api/knowledge-bases',json={'name':'isolated quality validation'})
drain()
with (WORKSPACE/'doc2md/examples/sample.pdf').open('rb') as f:
 upload=request('POST',f"/api/knowledge-bases/{k['id']}/uploads",files={'file':('sample.pdf',f,'application/pdf')},data={'offline':'true'})
drain();doc=upload['document_id'];baseurl=f"/api/knowledge-bases/{k['id']}/documents/{doc}"
quality=request('GET',baseurl+'/quality')
assert quality['pending_blocks']>0
member=s.one('SELECT * FROM members WHERE kb=?',(k['id'],));base=Path(member['artifact']);before=hashes(base)
block=next(b for b in quality['blocks'] if '1729' in b['text'])
# Record one synthetic historical citation from the real retrieval result, never a model result.
library=s.one('SELECT * FROM kbs WHERE id=?',(k['id'],))
raw=invoke(WORKSPACE/'DocBase/.conda/python.exe','docbase',['query','--storage',s.docbase_storage,'--collection',library['collection'],
 '--index-version',library['index_version'],'--profile','bm25-only','--question','1729','--top-k','5'],WORKSPACE/'DocBase',folder/'raw-query')
evidence=next(r for r in raw['results'] if '1729' in r['text'])
evidence.update(evidence_id='E1',quote='1729')
chat=request('POST','/api/conversations',json={'kb':k['id'],'title':'synthetic offline citation'})
s.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',('synthetic_citation',chat['id'],'assistant','Synthetic fixture only','answered',None,
 dump({'status':'answered','citations':[evidence],'claims':[]}),time.time()))
request('POST',baseurl+'/revisions',json={'action':'edit','reason':'isolated automated fixture',
 'block_id':block['block_id'],'text':block['text'].replace('1729','1730'),'expected_revision':quality['artifact_revision'],'request_id':'edit-1'})
drain()
assert hashes(base)==before
assert '1730' in request('GET',baseurl+'/preview')['markdown']
search=request('POST',f"/api/knowledge-bases/{k['id']}/search",json={'question':'1730','request_id':'search'})
assert any('1730' in r['text'] for r in search['results'])
assert not any('1729' in r['text'] for r in search['results'])
current=s.one('SELECT * FROM kbs WHERE id=?',(k['id'],))
raw2=invoke(WORKSPACE/'DocBase/.conda/python.exe','docbase',['query','--storage',s.docbase_storage,'--collection',current['collection'],
 '--index-version',current['index_version'],'--profile','bm25-only','--question','1730','--top-k','5'],WORKSPACE/'DocBase',folder/'corrected-query')
assert any('human_correction' in r['content_origin'] for r in raw2['results'] if '1730' in r['text'])
report['steps'].append('real offline parse → copy-on-write correction → new index → corrected retrieval with human origin; old artifact hash unchanged')
locator=request('GET','/api/messages/synthetic_citation/citations/0/locator')
assert locator['display_page']==1 and locator['kind']=='text_matches'
assert c.get('/api/sources/'+locator['image_asset_id']).status_code==200
report['steps'].append('old synthetic citation still renders original page after correction')
backup_folder=backup(s)
restored=restore(backup_folder,folder.parent/(folder.name+'_restored'))
rs=Store(restored);rc=TestClient(create_app(rs,seed=False),base_url='http://127.0.0.1')
rc.headers['X-CSRF-Token']=rc.get('/api/bootstrap').json()['csrf']
r=rc.get(baseurl+'/preview');assert r.status_code==200 and '1730' in r.json()['markdown']
res=rc.post(f"/api/knowledge-bases/{k['id']}/search",json={'question':'1730','request_id':'restored'})
assert res.status_code==200 and any('1730' in r['text'] for r in res.json()['results'])
r=rc.get('/api/messages/synthetic_citation/citations/0/locator');assert r.status_code==200,r.text
assert rs.setting('session')!=s.setting('session')
assert not rs.all('SELECT * FROM watches WHERE enabled=1')
report['steps'].append('backup database/file hashes → restore to new directory → corrected preview/retrieval and old citation; session rotated')
# Explicit page reparse exercises original page restoration without rewriting either earlier artifact.
q=request('GET',baseurl+'/quality');corrected=Path(s.one('SELECT artifact FROM members')['artifact']);corrected_hash=hashes(corrected)
request('POST',baseurl+'/revisions',json={'action':'page','page_index':0,'reason':'local selected-page reparse',
 'expected_revision':q['artifact_revision'],'request_id':'page-1','offline':True})
drain();assert '1729' in request('GET',baseurl+'/preview')['markdown']
assert hashes(base)==before and hashes(corrected)==corrected_hash
report['steps'].append('selected-page reparse → third artifact; original and corrected artifact bytes retained')
report['status']='passed'
atomic(ROOT/'design/quality-v1/workflow-report.json',report)
print(json.dumps(report,ensure_ascii=False,indent=2))
