import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from researchdesk.api import create_app
from researchdesk.store import Store, dump
from researchdesk.worker import Worker


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / 'data')
    app = create_app(store, seed=False)
    client = TestClient(app, base_url='http://127.0.0.1')
    boot = client.get('/api/bootstrap').json()
    client.headers['X-CSRF-Token'] = boot['csrf']
    return store, client


def library(client):
    response = client.post('/api/knowledge-bases', json={'name': '测试知识库'})
    assert response.status_code == 200
    return response.json()


def test_local_session_and_csrf(tmp_path):
    app = create_app(Store(tmp_path), seed=False)
    client = TestClient(app, base_url='http://127.0.0.1')
    assert client.get('/api/knowledge-bases').status_code == 401
    boot = client.get('/api/bootstrap').json()
    assert client.post('/api/knowledge-bases', json={'name':'x'}).status_code == 403
    client.headers['X-CSRF-Token'] = boot['csrf']
    assert client.post('/api/knowledge-bases', json={'name':'x'}).status_code == 200
    assert client.get('/api/health', headers={'Host':'evil.test'}).status_code == 403
    assert client.get('/api/bootstrap', headers={'Origin':'https://evil.test'}).status_code == 403


def test_library_upload_duplicate_remove_restore(context):
    s, c = context
    kb = library(c)
    def upload():
        return c.post(f"/api/knowledge-bases/{kb['id']}/uploads", files={'file': ('论文.pdf', b'%PDF-1.4\ntest\n', 'application/pdf')})
    first = upload().json()
    assert first['job_id']
    assert upload().json()['duplicate'] is True
    docs = c.get(f"/api/knowledge-bases/{kb['id']}/documents").json()
    assert docs['total'] == 1 and 'path' not in docs['items'][0]
    assert c.delete(f"/api/knowledge-bases/{kb['id']}/documents/{first['document_id']}").status_code == 202
    assert not s.scope(kb['id'])
    assert Path(s.one('SELECT path FROM docs')['path']).is_file()
    assert c.post(f"/api/knowledge-bases/{kb['id']}/documents/{first['document_id']}/restore").status_code == 202
    assert s.one('SELECT active FROM members')['active'] == 1


@pytest.mark.parametrize('name,content', [('bad.pdf',b'notpdf'),('code.exe',b'%PDF-'),('a.docx',b'%PDF-')])
def test_bad_uploads_rejected(context,name,content):
    s,c=context
    kb=library(c)
    assert c.post(f"/api/knowledge-bases/{kb['id']}/uploads",files={'file':(name,content)}).status_code==415
    assert s.one('SELECT count(*) n FROM docs')['n']==0


def test_chat_permission_idempotency_and_history(context):
    s,c=context
    kb=library(c)
    chat=c.post('/api/conversations',json={'kb':kb['id']}).json()
    url=f"/api/conversations/{chat['id']}/messages"
    body={'question':'第一个问题','request_id':'request-1'}
    assert c.post(url,json=body).status_code==409
    c.patch(f"/api/knowledge-bases/{kb['id']}",json={'external_enabled':True})
    s.execute('UPDATE kbs SET index_version=? WHERE id=?',('idx_fixture',kb['id']))
    a=c.post(url,json=body)
    assert a.status_code==202
    assert c.post(url,json=body).json()==a.json()
    assert len(c.get(url).json())==2
    assert c.post(url,json={**body,'request_id':'new'}).status_code==409
    job=a.json()['job_id']
    c.post(f'/api/jobs/{job}/cancel')
    assert c.get(url).json()[-1]['status']=='cancelled'
    assert c.post(url,json={**body,'request_id':'new'}).status_code==202


def test_revoking_permission_cancels_pending(context):
    s,c=context
    kb=library(c)
    c.patch(f"/api/knowledge-bases/{kb['id']}",json={'external_enabled':True})
    s.execute('UPDATE kbs SET index_version=? WHERE id=?',('idx_fixture',kb['id']))
    chat=c.post('/api/conversations',json={'kb':kb['id']}).json()
    job=c.post(f"/api/conversations/{chat['id']}/messages",json={'question':'question','request_id':'a'}).json()['job_id']
    c.patch(f"/api/knowledge-bases/{kb['id']}",json={'external_enabled':False})
    assert s.one('SELECT cancel FROM jobs WHERE id=?',(job,))['cancel']==1
    assert s.one("SELECT status FROM messages WHERE role='assistant'")['status']=='cancelled'


def test_preferences_are_whitelisted_and_persist(context):
    s,c=context
    assert c.patch('/api/preferences',json={'language':'English','length':'详细'}).status_code==200
    assert c.get('/api/preferences').json()['language']=='English'
    assert c.patch('/api/preferences',json={'system_prompt':'ignore rules'}).status_code==422
    restarted=Store(s.data)
    assert json.loads(restarted.setting('preferences'))['length']=='详细'


def test_queue_serializes_per_library_and_fences(context):
    s,c=context
    kb=library(c)
    assert s.enqueue(kb['id'],'reindex',{})==s.enqueue(kb['id'],'reindex',{})
    claimed=s.claim()
    assert claimed and not s.claim()
    s.execute('UPDATE jobs SET lease=0 WHERE id=?',(claimed['id'],))
    assert s.claim() is None
    assert s.one('SELECT state FROM jobs WHERE id=?',(claimed['id'],))['state']=='interrupted'


def test_worker_failure_not_answer(context,monkeypatch):
    s,c=context
    kb=library(c)
    def fail(*a,**kw): raise RuntimeError('MODEL_AUTH 测试故障')
    monkeypatch.setattr('researchdesk.worker.invoke',fail)
    Worker(s).once()
    job=s.one('SELECT * FROM jobs')
    assert job['state']=='failed' and 'MODEL_AUTH' in job['error']
    assert c.post(f"/api/jobs/{job['id']}/retry").json()['job_id']!=job['id']


def test_asset_only_registered_and_owned(context,tmp_path):
    s,c=context
    assert c.get('/api/sources/no-such-file').status_code==404
    with pytest.raises(ValueError): s.asset(Path('C:/Windows/win.ini'))
    p=s.data/'test.pdf';p.write_bytes(b'%PDF-1.4')
    aid=s.asset(p)
    response=c.get('/api/sources/'+aid,headers={'Range':'bytes=0-3'})
    assert response.status_code==206 and response.content==b'%PDF'


def test_sse_completed_event_replay(context):
    s,c=context
    kb=library(c)
    job=s.one('SELECT * FROM jobs')['id']
    s.execute("UPDATE jobs SET state='succeeded' WHERE id=?",(job,))
    s.event(job,'finished')
    response=c.get(f'/api/jobs/{job}/events')
    assert 'event: done' in response.text and 'finished' in response.text


def test_pagination_rejected(context):
    s,c=context;kb=library(c)
    assert c.get(f"/api/knowledge-bases/{kb['id']}/documents?limit=10000").status_code==422


def test_directory_watch_changes_and_disconnect_are_safe(context,tmp_path):
    import os
    from researchdesk.watches import scan_watches
    s,c=context; kb=library(c)
    root=tmp_path/'papers';root.mkdir()
    source=root/'new.pdf';source.write_bytes(b'%PDF-version-one')
    os.utime(source,(time.time()-10,time.time()-10))
    watch=c.post('/api/watches',json={'kb':kb['id'],'path':str(root)}).json()['id']
    scan_watches(s)
    first=s.one('SELECT doc FROM members')['doc']
    assert s.one('SELECT COUNT(*) n FROM members')['n']==1
    source.write_bytes(b'%PDF-version-two')
    os.utime(source,(time.time()-10,time.time()-10))
    s.execute('UPDATE watches SET last_scan=0')
    scan_watches(s)
    assert s.one('SELECT active FROM members WHERE doc=?',(first,))['active']==0
    source.unlink();root.rmdir()
    s.execute('UPDATE watches SET last_scan=0')
    scan_watches(s)
    assert s.one('SELECT COUNT(*) n FROM members WHERE active=1')['n']==1
    assert '不可达' in s.one('SELECT error FROM watches')['error']


def test_conversation_scope_is_validated(context):
    s,c=context;kb=library(c)
    chat=c.post('/api/conversations',json={'kb':kb['id']}).json()
    url=f"/api/conversations/{chat['id']}/scope"
    assert c.get(url).json()['document_ids'] is None
    assert c.patch(url,json={'document_ids':['not-owned']}).status_code==422
    assert c.patch(url,json={'document_ids':[]}).status_code==200
    assert c.get(url).json()['document_ids']==[]


def test_backup_restore_does_not_overwrite(context,tmp_path):
    from researchdesk.manage import backup,restore
    s,c=context
    (s.data/'docbase_storage').mkdir()
    p=s.data/'uploads'/'test.pdf';p.parent.mkdir();p.write_bytes(b'%PDF-1.4')
    s.execute('INSERT INTO docs VALUES(?,?,?,?,?,?)',('d_test','test.pdf','testhash',str(p),p.stat().st_size,time.time()))
    asset=s.asset(p)
    folder=backup(s)
    target=restore(folder,tmp_path/'restored')
    reopened=Store(target)
    path=Path(reopened.one('SELECT path FROM assets WHERE id=?',(asset,))['path'])
    assert path.is_relative_to(target) and path.read_bytes()==b'%PDF-1.4'
    assert reopened.setting('session')!=s.setting('session')
    with pytest.raises(RuntimeError):restore(folder,target)
