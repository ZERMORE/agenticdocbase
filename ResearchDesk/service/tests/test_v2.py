import json,time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from researchdesk.api import create_app
from researchdesk.store import Store,dump
from researchdesk.v2_store import ancestors,ensure_branch,fork,metadata,set_metadata
from researchdesk.model_profiles import ModelProfiles
from researchdesk.zotero import preview,apply,sha

@pytest.fixture
def ctx(tmp_path):
    s=Store(tmp_path/'data');c=TestClient(create_app(s,seed=False),base_url='http://127.0.0.1')
    c.headers['X-CSRF-Token']=c.get('/api/bootstrap').json()['csrf']
    kb=c.post('/api/knowledge-bases',json={'name':'V2 test'}).json()['id']
    chat=c.post('/api/conversations',json={'kb':kb}).json()['id']
    s.execute('DELETE FROM jobs')
    return s,c,kb,chat

def seed_messages(s,chat):
    for i,(role,text,status) in enumerate([('user','First','sent'),('assistant','Answer A','answered'),('user','Follow up','sent'),('assistant','Answer B','answered')]):
        s.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(str(i),chat,role,text,status,None,None,time.time()+i))
    ensure_branch(s,chat)

def test_branches_fork_preserve_and_export(ctx):
    s,c,kb,chat=ctx;seed_messages(s,chat)
    main=ensure_branch(s,chat);branch=fork(s,chat,'2','edit','Different idea')
    assert branch['seed_question']=='Follow up'
    assert [m['content'] for m in ancestors(s,chat)]==['First','Answer A']
    regenerate=fork(s,chat,'1','regenerate','Retry')
    assert regenerate['seed_question']=='First' and ancestors(s,chat)==[]
    c.post(f'/api/conversations/{chat}/branches/{main}/activate')
    assert len(c.get(f'/api/conversations/{chat}/messages').json())==4
    assert s.one('SELECT COUNT(*) n FROM messages')['n']==4
    result=c.get(f'/api/conversations/{chat}/export?format=json').json()
    assert len(result['messages'])==4
    c.post('/api/saved-items',json={'message_id':'1'})
    assert len(c.get('/api/saved-items').json())==1
    c.post('/api/saved-items',json={'message_id':'1'})
    assert len(c.get('/api/saved-items').json())==1
    c.post(f'/api/conversations/{chat}/branches/{regenerate["id"]}/activate')
    assert c.post(f'/api/conversations/{chat}/reveal/1').status_code==200
    assert len(ancestors(s,chat))==4

def test_metadata_filter_empty_frozen_and_manual_precedence(ctx):
    s,c,kb,chat=ctx
    doc=c.post(f'/api/knowledge-bases/{kb}/uploads',files={'file':('a.pdf',b'%PDF-1.4\nfixture')}).json()['document_id']
    url=f'/api/knowledge-bases/{kb}/documents/{doc}/metadata'
    assert c.patch(url,json={'authors':['Alice'],'year':2024,'tags':['CLIP'],'title':'Reviewed title'}).status_code==200
    set_metadata(s,kb,doc,{'title':'Unreviewed','venue':'Zotero journal'},'zotero')
    assert metadata(s,kb,doc)['title']=='Reviewed title'
    assert metadata(s,kb,doc)['venue']=='Zotero journal'
    assert c.post(f'/api/knowledge-bases/{kb}/filter',json={'author':'bob'}).json()['total']==0
    assert c.get(f'/api/knowledge-bases/{kb}/documents',params={'metadata_filters':json.dumps({'tag':'unknown'})}).json()['total']==0
    assert c.patch(f'/api/conversations/{chat}/scope',json={'filters':{'year_from':'2020'}}).status_code==422
    assert c.post(f'/api/knowledge-bases/{kb}/filter',json={'unknown':'x'}).status_code==422
    c.patch(f'/api/conversations/{chat}/scope',json={'filters':{'tag':'unknown'}})
    s.execute('UPDATE members SET docbase_id=? WHERE doc=?',('doc_fixture',doc))
    s.execute('UPDATE kbs SET index_version=?,external_enabled=1 WHERE id=?',('idx_fixture',kb))
    response=c.post(f'/api/conversations/{chat}/messages',json={'question':'Test','request_id':'one'}).json()
    payload=json.loads(s.one('SELECT payload FROM jobs WHERE id=?',(response['job_id'],))['payload'])
    assert payload['snapshot']['allowed_doc_ids']==[]
    c.patch(url,json={'tags':['unknown']})
    assert s.one('SELECT cancel FROM jobs WHERE id=?',(response['job_id'],))['cancel']==1
    assert payload['snapshot']['allowed_doc_ids']==[]

def test_dpapi_revision_snapshots_and_missing_credentials(ctx,tmp_path):
    s,c,kb,chat=ctx;m=ModelProfiles(s,workspace=tmp_path)
    cfg={'base_url':'https://example.invalid/v1','model':'synthetic','api_key':'fake-secret-NEVER-SEND','timeout':30,'max_tokens':1000,'max_calls':3}
    first=m.save('agent',cfg);snapshot={'agent':first['id']}
    assert 'api_key' not in first and first['key_hint'].endswith('SEND')
    assert 'fake-secret' not in s.path.read_bytes().decode('latin1')
    m.save('agent',dict(cfg,model='new',api_key='different-fake'))
    env=m.environment(snapshot,'DocAgent');assert env['DOCAGENT_MODEL']=='synthetic'
    assert env['DOCAGENT_API_KEY']=='fake-secret-NEVER-SEND'
    with pytest.raises(ValueError):m.save('agent',{'base_url':'https://other.invalid'})
    path=next((s.data/'secrets').glob('*.dpapi'));assert 'fake-secret' not in path.read_text()
    current=s.one('SELECT credential FROM model_profile_revisions WHERE id=?',(m.get('agent')['id'],))
    (s.data/'secrets'/(current['credential']+'.dpapi')).unlink()
    assert m.get('agent')['key_error']
    assert m.save('agent',{'api_key':'replacement'})['key_configured']

def test_sse_twenty_reconnections_do_not_requeue(ctx):
    s,c,kb,chat=ctx;job=s.enqueue(kb,'chat',{})
    s.execute("UPDATE jobs SET state='succeeded' WHERE id=?",(job,))
    s.event(job,'draft',{'text':'Draft','verified':False});s.event(job,'answer_committed',{})
    cursor=0
    for i in range(20):
        response=c.get(f'/api/jobs/{job}/events',headers={'Last-Event-ID':str(cursor)})
        data=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {') and 'event_id' in line]
        if i==0:assert len(data)==3
        else:assert not data
        if data:cursor=data[-1]['sequence']
        assert 'event: done' in response.text
    assert s.one('SELECT COUNT(*) n FROM jobs')['n']==1

def test_zotero_ten_attachments_metadata_content_missing_and_offline(ctx,tmp_path):
    s,c,kb,chat=ctx;storage=tmp_path/'storage';storage.mkdir();export=tmp_path/'library.json';items=[]
    for i in range(10):
        folder=storage/str(i);folder.mkdir();pdf=folder/'paper.pdf';pdf.write_bytes(b'%PDF-1.4\n'+str(i).encode())
        items.append({'key':'item'+str(i),'title':'Paper '+str(i),'date':'2020','creators':[{'lastName':'Author'}],'attachments':[{'key':str(i),'path':str(pdf)}]})
    export.write_text(dump(items),encoding='utf-8');before={str(p):sha(p) for p in storage.rglob('*.pdf')}
    source=c.post('/api/zotero-sources',json={'kb':kb,'path':str(export),'storage_root':str(storage)}).json()['id']
    plan=preview(s,source);assert plan['counts']=={'new':10}
    assert apply(s,plan['id'])['new_model_requests']==0
    assert s.one('SELECT count(*) n FROM members')['n']==10
    assert s.one("SELECT count(*) n FROM jobs WHERE kind='parse'")['n']==10
    assert before=={str(p):sha(p) for p in storage.rglob('*.pdf')}
    # Mark all parsed: metadata-only/rename/duplicate must not schedule parsing again.
    s.execute("UPDATE jobs SET state='succeeded'");s.execute("UPDATE members SET artifact='synthetic-artifact'")
    items[0]['title']='New metadata';items[1]['attachments'][0]['path']=str(storage/'1'/'renamed.pdf');(storage/'1'/'paper.pdf').rename(storage/'1'/'renamed.pdf')
    items.append({'key':'duplicate','title':'Duplicate','attachments':[{'key':'dup','path':str(storage/'2'/'paper.pdf')}]})
    export.write_text(dump(items),encoding='utf-8');p=preview(s,source)
    assert p['counts']['metadata']==1 and p['counts']['duplicate']==1
    apply(s,p['id']);assert s.one("SELECT count(*) n FROM jobs WHERE kind='parse'")['n']==10
    (storage/'3'/'paper.pdf').write_bytes(b'%PDF-1.4\nchanged')
    (storage/'4'/'paper.pdf').unlink();p=preview(s,source);assert p['counts']['changed']==1 and p['counts']['missing']==1
    apply(s,p['id']);assert s.one('SELECT COUNT(*) n FROM members WHERE active=1')['n']==10
    doc=s.one("SELECT doc FROM zotero_links WHERE attachment_key='5'")['doc'];s.execute('UPDATE members SET active=0 WHERE doc=?',(doc,))
    assert preview(s,source)['counts']['removed']==1
    export.rename(export.with_suffix('.offline'))
    with pytest.raises(ValueError):preview(s,source)
    assert Path(s.one('SELECT path FROM docs')['path']).is_file()


def test_worker_history_is_branch_ancestry_not_siblings(ctx,monkeypatch):
    from researchdesk.worker import Worker
    s,c,kb,chat=ctx;seed_messages(s,chat);branch=fork(s,chat,'2','edit','branch')
    s.execute('UPDATE kbs SET index_version=?,external_enabled=1 WHERE id=?',('idx_fixture',kb))
    # Existing seed timestamps are slightly future-dated; move them before the new question.
    s.execute('UPDATE messages SET created=created-100')
    reply=c.post(f'/api/conversations/{chat}/messages',json={'question':'Different followup','request_id':'forked'}).json()
    seen=[]
    def invoke(executable,module,args,cwd,folder,check,timeout,events,relay,env_overrides=None):
        request=json.loads((s.data/'runs'/reply['job_id']/'request.json').read_text(encoding='utf-8'));seen.append(request)
        result={'status':'answered','answer':'Synthetic','claims':[],'citations':[]}
        (s.data/'runs'/reply['job_id']/'result.json').write_text(dump(result),encoding='utf-8')
        return result
    monkeypatch.setattr('researchdesk.worker.invoke',invoke)
    assert Worker(s).once()
    assert [r['content'] for r in seen[0]['recent_messages']]==['First','Answer A']
    assert 'Answer B' not in dump(seen)
    assert s.one('SELECT content FROM messages WHERE id=?',('3',))['content']=='Answer B'

def test_legacy_export_redacts_file_urls_in_answer(ctx):
    s,c,kb,chat=ctx
    result={'claims':[{'text':'Supported finding','supports':[{'evidence_id':'E1'}]}],'citations':[{'evidence_id':'E1','quote':'source','sources':[{'original_path':'C:/private/paper.pdf','asset_id':'asset_safe','page_index':2}]}]}
    s.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',('a',chat,'assistant','file:///C:/private/paper.pdf','answered',None,dump(result),time.time()))
    text=c.get(f'/api/conversations/{chat}/export').text
    assert 'file:///' not in text and 'C:/private' not in text and '/api/sources/asset_safe#page=2' in text


def test_v2_backup_restore_keeps_branches_and_encrypted_profile_but_pauses_sync(ctx,tmp_path):
    from researchdesk.manage import backup,restore
    s,c,kb,chat=ctx;seed_messages(s,chat);fork(s,chat,'2','edit','Saved fork')
    manager=ModelProfiles(s,workspace=tmp_path);manager.save('agent',{'base_url':'https://example.invalid','model':'synthetic','api_key':'fake-backup-key'})
    s.execute('INSERT INTO zotero_sources(id,kb,path,storage_root,enabled) VALUES(?,?,?,?,1)',('source',kb,str(tmp_path/'export.json'),str(tmp_path)))
    # Avoid using global real indexes in the synthetic backup.
    (s.data/'docbase_storage').mkdir()
    copy=Store(restore(backup(s),tmp_path/'restored'))
    assert copy.one('SELECT count(*) n FROM branches')['n']==2
    assert len(ancestors(copy,chat))==2
    assert copy.one('SELECT enabled FROM zotero_sources')['enabled']==0
    assert ModelProfiles(copy,workspace=tmp_path).get('agent',private=True)['api_key']=='fake-backup-key'


def test_web_profiles_load_in_real_agent_subprocess(ctx,tmp_path):
    import os,subprocess,sys
    from researchdesk.store import WORKSPACE
    s,c,kb,chat=ctx;manager=ModelProfiles(s,workspace=tmp_path)
    common={'base_url':'https://example.invalid/v1','model':'synthetic','api_key':'fake-regression-key','timeout':60.0}
    agent=manager.save('agent',common)
    support=manager.save('support',dict(common,timeout=45.25,inherit=False))
    env=os.environ.copy();env={k:v for k,v in env.items() if not k.startswith(('DOCAGENT_','DOCBASE_','DOC2MD_'))}
    env.update(manager.environment({'agent':agent['id'],'support':support['id']},'DocAgent'));env['PYTHONIOENCODING']='utf-8'
    empty=tmp_path/'empty.env';empty.write_text('',encoding='utf-8')
    code="from docagent.config import Settings; import sys,json; c=Settings.load(sys.argv[1]); print(json.dumps([c.request_timeout_seconds,c.support_request_timeout_seconds,c.max_model_calls]))"
    process=subprocess.run([str(WORKSPACE/'DocAgent/.conda/python.exe'),'-c',code,str(empty)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert process.returncode==0,process.stderr
    assert json.loads(process.stdout)==[60.0,45.25,6]


def test_bridge_reports_configuration_error_without_model_call(tmp_path):
    import os,subprocess
    from researchdesk.store import WORKSPACE
    request=tmp_path/'request.json';request.write_text('{}',encoding='utf-8');result=tmp_path/'result.json'
    env=os.environ.copy();env['DOCAGENT_REQUEST_TIMEOUT_SECONDS']='invalid-timeout'
    process=subprocess.run([str(WORKSPACE/'DocAgent/.conda/python.exe'),'-m','docagent.service_bridge','--request-file',str(request),'--result-file',str(result),'--events-file',str(tmp_path/'events.jsonl')],env=env,capture_output=True,timeout=20)
    assert process.returncode==0
    data=json.loads(result.read_text(encoding='utf-8'))
    assert data['status']=='failed' and data['code']=='CONFIG'
    assert 'DOCAGENT_REQUEST_TIMEOUT_SECONDS' in data['error']
    assert 'Traceback' not in process.stderr.decode(errors='replace')


def test_archive_restore_and_delete_preserve_library(ctx):
    s,c,kb,chat=ctx;seed_messages(s,chat)
    assert c.post(f'/api/conversations/{chat}/archive').status_code==200
    assert c.get('/api/conversations').json()==[]
    rows=c.get('/api/conversations?include_archived=true').json();assert rows[0]['archived']==1
    assert len(c.get(f'/api/conversations/{chat}/messages').json())==4
    assert c.post(f'/api/conversations/{chat}/unarchive').status_code==200
    assert c.get('/api/conversations').json()[0]['archived']==0
    c.post(f'/api/conversations/{chat}/archive')
    assert c.delete(f'/api/conversations/{chat}').status_code==200
    assert not s.one('SELECT 1 FROM conversation_archives WHERE conversation=?',(chat,))
    assert not s.one('SELECT 1 FROM messages WHERE conversation=?',(chat,))
    assert s.one('SELECT 1 FROM kbs WHERE id=?',(kb,))


def test_pending_chat_cannot_be_archived(ctx):
    s,c,kb,chat=ctx
    s.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',('pending',chat,'assistant','','pending',None,None,time.time()))
    assert c.post(f'/api/conversations/{chat}/archive').status_code==409
    assert not s.one('SELECT 1 FROM conversation_archives')
