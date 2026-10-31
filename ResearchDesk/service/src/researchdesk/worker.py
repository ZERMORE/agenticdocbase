import json
import mimetypes
from pathlib import Path
import time

from .processes import invoke
from .store import Store, WORKSPACE, atomic, dump


class Cancelled(Exception):
    pass


class Worker:
    def __init__(self, store=None):
        self.store = store or Store()

    def once(self):
        s = self.store
        job = s.claim()
        if not job:
            return False
        key, token = job['id'], job['token']
        folder = s.data / 'runs' / key
        folder.mkdir(parents=True, exist_ok=True)
        payload = json.loads(job['payload'])
        kb = s.one('SELECT * FROM kbs WHERE id=?', (job['kb'],))
        last_renew = 0

        def check():
            nonlocal last_renew
            row = s.one('SELECT state,token,cancel FROM jobs WHERE id=?', (key,))
            if not row or row['token'] != token or row['cancel'] or row['state'] != 'running':
                raise Cancelled('任务已停止')
            if job['kind'] in {'parse', 'revise'}:
                live = s.one('SELECT external_enabled,deleted FROM kbs WHERE id=?', (job['kb'],))
                member = s.one('SELECT active FROM members WHERE kb=? AND doc=?', (job['kb'], payload.get('doc')))
                if not live or live['deleted'] or not member or not member['active']:
                    raise Cancelled('文档或知识库已移出')
                if not payload.get('offline', True) and not live['external_enabled']:
                    raise Cancelled('外部模型授权已撤销')
            if time.time() - last_renew > 3:
                s.execute('UPDATE jobs SET lease=?,updated=? WHERE id=? AND token=?', (time.time() + 30, time.time(), key, token))
                s.set_setting('worker_heartbeat', str(time.time()))
                last_renew = time.time()

        def stage(name, **data):
            check()
            s.execute('UPDATE jobs SET stage=?,updated=? WHERE id=? AND token=?', (name, time.time(), key, token))
            s.event(key, name, data)

        def relay(event):
            name=event.get('event','working')
            if name in {'draft','draft_reset'}:
                check();text=event.get('text','')[:100000]
                s.execute('INSERT INTO message_drafts VALUES(?,?,1,?) ON CONFLICT(job) DO UPDATE SET text=excluded.text,sequence=message_drafts.sequence+1,updated=excluded.updated', (key,text,time.time()))
                s.event(key,name,{'text':text,'verified':False,'run_id':event.get('run_id')})
            elif name=='claim_verified':
                check();s.event(key,name,{'claim_index':event.get('claim_index'),'text':event.get('text',''),'verification':'quotation'})
            else:stage(name)

        def call(project, module, args, label, timeout=600, events=None):
            if project == 'doc2md' and 'parser' in payload.get('model_snapshot',{}):
                from .model_profiles import ModelProfiles
                config=ModelProfiles(s).get('parser',revision=payload['model_snapshot']['parser'])
                args=[*args,'--timeout',str(config['timeout']),'--max-tokens',str(config['max_tokens'])]
            if project == 'DocBase':
                args = [*args, '--storage', s.docbase_storage]
            return invoke(WORKSPACE / project / '.conda' / 'python.exe', module, args,
                WORKSPACE / project, folder / label, check, timeout, events,
                relay, env_overrides=__import__('researchdesk.model_profiles',fromlist=['ModelProfiles']).ModelProfiles(s).environment(payload.get('model_snapshot',{}),project))

        try:
            check()
            if job['kind'] == 'parse':
                from .reliability import ensure_space
                ensure_space(s.data)
                doc = s.one('SELECT * FROM docs WHERE id=?', (payload['doc'],))
                member = s.one('SELECT * FROM members WHERE kb=? AND doc=?', (kb['id'], doc['id']))
                if not member or not member['active']:
                    raise Cancelled('文档已移出')
                if not payload.get('offline', True) and not kb['external_enabled']:
                    raise RuntimeError('请先为此知识库启用外部模型处理')
                stage('parsing')
                output = Path(payload.get('resume_output') or (s.data / 'parsed' / key)).resolve()
                if not output.is_relative_to((s.data / 'parsed').resolve()):
                    raise RuntimeError('输入断点目录越界')
                output.mkdir(parents=True, exist_ok=True)
                # Only our own parse-output locks, and only proven dead PIDs, are reclaimed.
                import psutil, re
                for lock in output.glob('doc_*.lock'):
                    match = re.fullmatch(r'pid=(\d+)', lock.read_text(encoding='utf-8').strip())
                    if match and not psutil.pid_exists(int(match[1])): lock.unlink()
                args = ['parse', doc['path'], '--output', str(output)] + (['--offline'] if payload.get('offline', True) else [])
                call('doc2md', 'doc2md', args, 'parse', 3600)
                report = json.loads((output / 'batch_report.json').read_text(encoding='utf-8'))
                item = report['results'][0]
                if item['status'] == 'failed':
                    raise RuntimeError(item.get('error', '文档解析失败'))
                artifact = Path(item['output'])
                manifest = json.loads((artifact / 'manifest.json').read_text(encoding='utf-8'))
                stage('parsed')
                with s.connect() as c:
                    c.execute('UPDATE members SET docbase_id=?,artifact=?,quality=?,status=?,pages=? WHERE kb=? AND doc=? AND active=1',
                        (manifest['doc_id'], str(artifact), manifest['status'], 'indexing', manifest.get('page_count'), kb['id'], doc['id']))
                    c.execute('UPDATE kbs SET generation=generation+1 WHERE id=?', (kb['id'],))
                s.enqueue(kb['id'], 'reindex', {})
                result = {'document_id': doc['id'], 'quality': manifest['status']}
            elif job['kind'] == 'revise':
                from .quality import revise_job
                result = revise_job(s, kb, payload, folder, call, check, stage)
            elif job['kind'] == 'zotero_sync':
                from .zotero import apply
                stage('synchronizing')
                result=apply(s,payload['sync_run'],check)
            elif job['kind'] == 'model_probe':
                from .model_probe import probe
                stage('testing_connection')
                result=probe(s,payload)
            elif job['kind'] == 'backup':
                from .manage import backup
                stage('backing_up')
                result = {'backup_name': backup(s, check=check).name}
            elif job['kind'] == 'reindex':
                stage('indexing')
                # Only reclaim this service's own stale collection lock after proving its writer is gone.
                lock = s.docbase_storage / 'collections' / kb['collection'] / 'build.lock'
                if lock.is_file() and kb['collection'].startswith('rd_kb_'):
                    import re
                    import psutil
                    match = re.fullmatch(r'pid=(\d+)', lock.read_text(encoding='utf-8').strip())
                    if match:
                        alive = False
                        try:
                            command = psutil.Process(int(match[1])).cmdline()
                            alive = 'docbase' in command and kb['collection'] in command
                        except psutil.NoSuchProcess:
                            pass
                        if not alive:
                            lock.unlink()
                members = s.all('SELECT m.*,d.name FROM members m JOIN docs d ON d.id=m.doc WHERE m.kb=? AND m.active=1 AND m.artifact IS NOT NULL', (kb['id'],))
                catalog = s.data / 'catalogs' / (kb['id'] + '.json')
                entries = []
                seen = set()
                for m in members:
                    if m['docbase_id'] in seen:
                        continue
                    seen.add(m['docbase_id'])
                    entries.append({'doc_id': m['docbase_id'], 'artifact_path': m['artifact'], 'name': m['name'], 'active': True})
                atomic(catalog, {'schema_version': '1', 'entries': entries})
                result = call('DocBase', 'docbase', ['build', '--profile', 'bm25-only', '--collection', kb['collection'],
                    '--catalog', catalog, '--env-file', WORKSPACE / 'DocAgent' / 'configs' / 'docbase-context.env',
                    '--incremental', '--allow-empty'], 'index', 1800)
                stage('published', index_version=result['index_version'])
                with s.connect() as c:
                    c.execute('UPDATE kbs SET index_version=?,published_generation=? WHERE id=?', (result['index_version'], kb['generation'], kb['id']))
                    for member in members:
                        c.execute("UPDATE members SET status='ready' WHERE kb=? AND doc=? AND active=1 AND artifact=?", (kb['id'], member['doc'], member['artifact']))
                current = s.one('SELECT generation FROM kbs WHERE id=?', (kb['id'],))
                if current['generation'] != kb['generation']:
                    s.enqueue(kb['id'], 'reindex', {})
            elif job['kind'] == 'chat':
                if not kb['external_enabled']:
                    raise RuntimeError('此知识库尚未允许外部模型处理；请在知识库设置中启用')
                if not kb['index_version']:
                    raise RuntimeError('知识库正在首次建库，请稍后重试')
                conversation = s.one('SELECT * FROM conversations WHERE id=?', (payload['conversation'],))
                from .v2_store import ancestors
                chain=ancestors(s,conversation['id'],payload.get('branch'))
                prior=[r for r in chain if r['created']<payload['message_time'] and r['status'] in {'sent','answered','insufficient_evidence'}]
                history=[{'role':r['role'],'content':r['content']} for r in prior[-8:]]
                preferences = json.loads(s.setting('preferences')) | json.loads(conversation['preferences'])
                selected = json.loads(s.setting('scope:' + conversation['id']) or 'null')
                allowed = s.scope(kb['id'])
                if selected is not None:
                    allowed = [r['docbase_id'] for r in s.all('SELECT doc,docbase_id FROM members WHERE kb=? AND active=1',(kb['id'],))
                               if r['doc'] in selected and r['docbase_id']]
                summary = '\n'.join('- '+r['content'][:150] for r in prior[:-8] if r['role']=='user')[-4000:]
                request = {'question': payload['question'], 'collection': kb['collection'], 'index_version': kb['index_version'],
                    'recent_messages': history, 'preferences': preferences, 'allowed_doc_ids': allowed,
                    'conversation_summary': summary}
                snapshot=payload.get('snapshot')
                if snapshot:
                    if snapshot['generation']!=kb['generation']:raise Cancelled('排队期间资料范围已变化，请重新提问')
                    request.update(allowed_doc_ids=snapshot['allowed_doc_ids'],index_version=snapshot['index_version'],preferences=snapshot['preferences'])
                    preferences=snapshot['preferences']
                    request['conversation_summary']='\n'.join('- '+r['content'][:150] for r in prior[:-8] if r['role']=='user')[-4000:]
                request['docbase_storage'] = str(s.docbase_storage)
                request['model_call_budget'] = payload.get('model_call_budget')
                request['support_check_level'] = preferences.get('support_check_level','advisory')
                atomic(folder / 'request.json', request)
                stage('retrieving')
                call('DocAgent', 'docagent.service_bridge', ['--request-file', folder / 'request.json',
                    '--result-file', folder / 'result.json', '--events-file', folder / 'events.jsonl'], 'agent', 240, folder / 'events.jsonl')
                result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
                if result['status'] in {'failed', 'cancelled'}:
                    raise RuntimeError(result.get('error') or 'Agent未完成问答')
                current = s.one('SELECT generation,external_enabled FROM kbs WHERE id=?', (kb['id'],))
                if current['generation'] != kb['generation'] or not current['external_enabled']:
                    raise Cancelled('资料范围或处理授权已变化，请重新提问')
                # Register assets while paths remain server-side. Never expose file:// URLs to the browser.
                for citation in result.get('citations', []):
                    for source in citation['sources']:
                        path = source.get('snapshot_path_absolute') or source.get('rendered_pdf_path_absolute')
                        if path:
                            source['asset_id'] = s.asset(path, mime=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                result['model_snapshot']=payload.get('model_snapshot',{})
                result['request_snapshot']=payload.get('snapshot',{})
                result['claims'] = result.get('claims', [])
                with s.connect() as c:
                    check_row = c.execute('SELECT token,cancel,state FROM jobs WHERE id=?', (key,)).fetchone()
                    if check_row['token'] != token or check_row['cancel'] or check_row['state'] != 'running':
                        raise Cancelled('结果提交前任务已取消')
                    c.execute('UPDATE messages SET content=?,status=?,result=? WHERE job=? AND role=\'assistant\'',
                        (result['answer'], result['status'], dump(result), key))
                stage('answer_committed')
            else:
                raise RuntimeError('未知任务类型')
            check()
            s.execute("UPDATE jobs SET state='succeeded',stage='finished',result=?,updated=? WHERE id=? AND token=?", (dump(result), time.time(), key, token))
            s.event(key, 'finished')
        except Cancelled as exc:
            s.execute("UPDATE jobs SET state='cancelled',error=?,updated=? WHERE id=? AND token=?", (str(exc), time.time(), key, token))
            s.execute("UPDATE messages SET status='cancelled',content='本次回答已停止' WHERE job=? AND role='assistant' AND status='pending'", (key,))
            s.event(key, 'cancelled')
        except Exception as exc:
            # Tool failures are concise; secrets are never included in exception payloads by adapters.
            from .reliability import failure
            message = failure(exc)['message']
            s.execute("UPDATE jobs SET state='failed',error=?,updated=? WHERE id=? AND token=?", (message, time.time(), key, token))
            s.execute("UPDATE messages SET status='failed',content=? WHERE job=? AND role='assistant' AND status='pending'", (message, key))
            if job['kind'] == 'parse':
                s.execute("UPDATE members SET status='failed' WHERE kb=? AND doc=?", (job['kb'], payload.get('doc')))
            from .reliability import failure
            detail = failure(exc)
            s.event(key, 'failed', detail)
        return True

    def run(self):
        self.store.init()
        while True:
            self.store.set_setting('worker_heartbeat', str(time.time()))
            if not self.once():
                if time.time()-float(self.store.setting('v2_retention') or 0)>3600:
                    cutoff=time.time()-7*86400
                    self.store.execute("DELETE FROM events WHERE created<? AND type IN ('draft','draft_reset','claim_verified') AND job IN(SELECT id FROM jobs WHERE state NOT IN ('queued','running'))",(cutoff,))
                    self.store.execute("DELETE FROM message_drafts WHERE updated<? AND job IN(SELECT id FROM jobs WHERE state NOT IN ('queued','running'))",(cutoff,))
                    self.store.execute("DELETE FROM sync_runs WHERE created<? AND state='preview' AND id NOT IN(SELECT json_extract(payload,'$.sync_run') FROM jobs WHERE kind='zotero_sync' AND state IN ('queued','running'))",(cutoff,))
                    self.store.set_setting('v2_retention',str(time.time()))
                from .watches import scan_watches
                scan_watches(self.store)
                from .zotero import scan
                scan(self.store)
                from .reliability import schedule_backup
                schedule_backup(self.store)
                time.sleep(.7)
