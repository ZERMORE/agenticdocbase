import json
from pathlib import Path
import shutil
import time
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from researchdesk.api import create_app
from researchdesk.store import Store
from researchdesk.reliability import failure,ensure_space,schedule_backup
from researchdesk.worker import Worker

@pytest.fixture
def ctx(tmp_path):
    s=Store(tmp_path/'data');c=TestClient(create_app(s,seed=False),base_url='http://127.0.0.1')
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf']
    kb=s.create_kb('quality')
    root=s.data/'parsed'/'fixture';root.mkdir(parents=True)
    (root/'source.pdf').write_bytes(b'%PDF-fixture')
    manifest={'schema_version':'1','doc_id':'doc_fixture','revision':'a'*64,'status':'ok','page_count':1,
              'block_count':1,'config_fingerprint':'fixture_v1','source_snapshot':'source.pdf','warnings':[]}
    (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    block={'block_id':'b1','type':'paragraph','text':'Version is 1729.','status':'ok',
           'source':{'page_index':1,'bbox':[20,20,100,60]}}
    (root/'blocks.jsonl').write_text(json.dumps(block)+'\n',encoding='utf-8')
    doc=s.register_artifact(kb['id'],{'artifact_path':str(root),'name':'fixture.pdf'})
    return s,c,kb,doc,root

def test_legacy_quality_read_does_not_mutate(ctx):
    s,c,k,d,root=ctx
    before={p.name:p.read_bytes() for p in root.iterdir()}
    response=c.get(f"/api/knowledge-bases/{k['id']}/documents/{d}/quality")
    assert response.status_code==200
    data=response.json();assert data['blocks'][0]['page_index']==0
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}
    assert not (root/'quality.json').exists()

def test_revision_validation_idempotency_and_external_consent(ctx):
    s,c,k,d,root=ctx;url=f"/api/knowledge-bases/{k['id']}/documents/{d}/revisions"
    body={'action':'edit','reason':'核对','block_id':'b1','text':'corrected','expected_revision':c.get(f"/api/knowledge-bases/{k['id']}/documents/{d}/quality").json()['artifact_revision'],'request_id':'one'}
    a=c.post(url,json=body);assert a.status_code==202
    assert c.post(url,json=body).json()==a.json()
    assert c.post(url,json={**body,'expected_revision':'old','request_id':'two'}).status_code==409
    assert c.post(url,json={**body,'offline':False,'request_id':'two'}).status_code==409
    assert c.post(url,json={**body,'block_id':'unknown','request_id':'two'}).status_code==422
    c.delete(f"/api/knowledge-bases/{k['id']}/documents/{d}")
    assert c.post(url,json={**body,'request_id':'two'}).status_code==409

def test_backup_policy_bounds_and_manual_queue(ctx):
    s,c,k,d,root=ctx
    assert c.patch('/api/operations/backup-policy',json={'enabled':True,'keep':0}).status_code==422
    assert c.patch('/api/operations/backup-policy',json={'enabled':True,'keep':2,'interval_hours':6}).status_code==200
    response=c.post('/api/operations/backup');assert response.status_code==202
    assert s.one('SELECT kind FROM jobs')['kind']=='backup'
    assert c.get('/api/operations').json()['backup_policy']['keep']==2

def test_disk_guard(ctx):
    s,*_=ctx
    with patch('researchdesk.reliability.shutil.disk_usage',return_value=shutil._ntuple_diskusage(100,99,1)):
        with pytest.raises(RuntimeError,match='DISK_LOW'):ensure_space(s.data)

@pytest.mark.parametrize('message,category',[('MODEL_QUOTA HTTP 402','quota'),('MODEL_AUTH 401','authentication'),
    ('MODEL_NETWORK 网络','network'),('VERSION_CONFLICT','version_conflict'),('DISK_LOW','disk')])
def test_failure_taxonomy(message,category):
    result=failure(RuntimeError(message+' api_key=secret123'))
    assert result['category']==category and 'secret123' not in result['message']
    assert not result['automatic_retry']

def test_worker_failure_exposes_actionable_redacted_diagnostics(ctx,monkeypatch):
    s,c,k,d,root=ctx;s.enqueue(k['id'],'reindex',{})
    monkeypatch.setattr('researchdesk.worker.invoke',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('MODEL_AUTH api_key=secret123')))
    Worker(s).once()
    row=c.get('/api/jobs').json()[0]
    assert 'secret123' not in row['error'] and row['failure']['category']=='authentication'
    assert row['failure']['advice']

def test_retry_keeps_parse_checkpoint_output(ctx):
    s,c,k,d,root=ctx;job=s.enqueue(k['id'],'parse',{'doc':d,'offline':True})
    s.execute("UPDATE jobs SET state='interrupted' WHERE id=?",(job,))
    retry=c.post(f'/api/jobs/{job}/retry').json()['job_id']
    payload=json.loads(s.one('SELECT payload FROM jobs WHERE id=?',(retry,))['payload'])
    assert Path(payload['resume_output'])==s.data/'parsed'/job

def test_model_checks_public_but_source_paths_private(ctx):
    from researchdesk.api import public_result
    result=public_result({'claims':[],'citations':[],'claim_checks':[{'claim_index':0,'status':'supported'}],
                          'support_check':'model_reviewed','api_key':'private'})
    assert result['support_check']=='model_reviewed' and 'api_key' not in result
