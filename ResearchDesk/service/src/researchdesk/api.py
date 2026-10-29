import asyncio
import hashlib
import hmac
import json
import mimetypes
from pathlib import Path
import time
from urllib.parse import urlsplit
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .store import Store, ROOT, WORKSPACE, atomic, uid, dump
from .processes import invoke


class Name(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)


class LibraryPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=100)
    external_enabled: bool | None = None


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=12000)
    request_id: str = Field(min_length=1, max_length=100)
    model_call_budget: int | None = Field(default=None, ge=1, le=6)


class Conversation(BaseModel):
    kb: str
    title: str = Field(default='新的研究问题', min_length=1, max_length=100)


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: str = Field(default='中文', max_length=40)
    length: str = Field(default='适中', max_length=40)
    research_focus: str = Field(default='', max_length=300)
    background: str = Field(default='研究生', max_length=100)
    structure: str = Field(default='先结论', max_length=80)
    name: str = Field(default='', max_length=80)
    support_check_level: Literal['off','advisory','balanced','strict'] = 'advisory'


class WatchRequest(BaseModel):
    kb: str
    path: str = Field(min_length=1,max_length=1000)


class Enabled(BaseModel):
    enabled: bool


class ScopeRequest(BaseModel):
    document_ids: list[str] | None = Field(default=None, max_length=200)


def public_result(value):
    if not value:
        return None
    result = json.loads(value) if isinstance(value, str) else value
    return {k: result.get(k) for k in ['status', 'claims', 'limitations', 'index_version', 'model_returned',
        'usage', 'timings', 'visual_verified', 'claim_checks', 'support_check', 'retrieval_coverage', 'revalidation', 'answer_completeness', 'support_check_level', 'support_warnings']} | {'citations': [
            {'evidence_id': c['evidence_id'], 'quote': c['quote'], 'type': c.get('type'),
             'content_origin': c.get('content_origin'), 'sources': [
                 {'asset_id': s.get('asset_id'), 'name': Path(s.get('original_path', '')).name,
                  'page_index': s.get('page_index')} for s in c['sources']]} for c in result.get('citations', [])]}


def create_app(store=None, seed=True):
    s = store or Store()
    s.init(seed=seed)
    app = FastAPI(title='研知 Research Desk', docs_url='/api/docs', openapi_url='/api/openapi.json')
    app.state.store = s
    secret = s.setting('session')

    @app.middleware('http')
    async def local_session(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0]
        if host not in {'127.0.0.1', 'localhost'}:
            return JSONResponse({'detail': '仅允许本机访问'}, 403)
        origin = request.headers.get('origin')
        if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin != str(request.base_url).rstrip('/')):
            return JSONResponse({'detail': '跨站请求已拒绝'}, 403)
        if request.url.path.startswith('/api/') and request.url.path != '/api/bootstrap':
            if not hmac.compare_digest(request.cookies.get('rd_session', ''), secret):
                return JSONResponse({'detail': '请从应用首页建立本机会话'}, 401)
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), secret):
                return JSONResponse({'detail': '请求校验失败，请刷新页面'}, 403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    def kb(key):
        row = s.one('SELECT * FROM kbs WHERE id=? AND deleted=0', (key,))
        if not row:
            raise HTTPException(404, '知识库不存在')
        return row

    def convo(key):
        row = s.one('SELECT * FROM conversations WHERE id=?', (key,))
        if not row:
            raise HTTPException(404, '会话不存在')
        kb(row['kb'])
        return row

    def cancel_chats(key):
        s.execute("UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END WHERE kb=? AND kind='chat' AND state IN ('queued','running')", (key,))
        s.execute("UPDATE messages SET status='cancelled',content='资料范围或模型授权已变化，请重新提问' WHERE role='assistant' AND status='pending' AND job IN (SELECT id FROM jobs WHERE kb=? AND cancel=1)", (key,))

    @app.get('/api/bootstrap')
    def bootstrap():
        response = JSONResponse({'csrf': secret, 'name': '研知', 'mode': 'local', 'preferences': json.loads(s.setting('preferences'))})
        response.set_cookie('rd_session', secret, httponly=True, samesite='strict', max_age=86400 * 30)
        return response

    @app.get('/api/health')
    def health():
        beat = float(s.setting('worker_heartbeat') or 0)
        return {'status': 'ok', 'worker_alive': time.time() - beat < 35,
                'queued': s.one("SELECT COUNT(*) n FROM jobs WHERE state='queued'")['n']}

    @app.get('/api/knowledge-bases')
    def libraries():
        return s.all('''SELECT k.*,COUNT(m.doc) total,SUM(CASE WHEN m.active=1 THEN 1 ELSE 0 END) active_count,
              SUM(CASE WHEN m.active=1 AND m.status='ready' THEN 1 ELSE 0 END) ready_count
              FROM kbs k LEFT JOIN members m ON m.kb=k.id WHERE k.deleted=0 GROUP BY k.id ORDER BY k.created''')

    @app.post('/api/knowledge-bases')
    def create_library(data: Name):
        row = s.create_kb(data.name.strip())
        s.enqueue(row['id'], 'reindex', {})
        return row

    @app.patch('/api/knowledge-bases/{key}')
    def edit_library(key: str, data: LibraryPatch):
        row = kb(key)
        if data.name is not None:
            s.execute('UPDATE kbs SET name=? WHERE id=?', (data.name.strip(), key))
        if data.external_enabled is not None:
            s.execute('UPDATE kbs SET external_enabled=? WHERE id=?', (int(data.external_enabled), key))
            if not data.external_enabled:
                cancel_chats(key)
        return kb(key)

    @app.delete('/api/knowledge-bases/{key}')
    def remove_library(key: str):
        kb(key)
        cancel_chats(key)
        with s.connect() as c:
            c.execute('UPDATE kbs SET deleted=1,external_enabled=0 WHERE id=?', (key,))
            c.execute("UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END WHERE kb=? AND state IN ('queued','running')", (key,))
            c.execute('UPDATE watches SET enabled=0 WHERE kb=?',(key,))
        return {'status': 'recycled', 'note': '已回收知识库，原始文件和历史快照保留'}

    @app.get('/api/knowledge-bases/{key}/documents')
    def documents(key: str, q: str = '', state: str = 'all', offset: int = 0, limit: int = 50, quality: str = 'all'):
        kb(key)
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, '分页参数不正确')
        condition = 'm.kb=? AND d.name LIKE ?'
        args = [key, '%' + q + '%']
        if state in {'active', 'removed'}:
            condition += ' AND m.active=?'
            args.append(int(state == 'active'))
        if quality not in {'all','ok','partial','failed','pending'}:
            raise HTTPException(422, '解析质量筛选参数不正确')
        if quality == 'failed':
            condition += " AND m.status='failed'"
        elif quality != 'all':
            condition += ' AND m.quality=?'
            args.append(quality)
        total = s.one('SELECT COUNT(*) n FROM members m JOIN docs d ON d.id=m.doc WHERE ' + condition, args)['n']
        rows = s.all('SELECT m.*,d.name,d.size,d.created FROM members m JOIN docs d ON d.id=m.doc WHERE ' + condition + ' ORDER BY d.created DESC LIMIT ? OFFSET ?', [*args, limit, offset])
        return {'total': total, 'items': [{k: v for k, v in r.items() if k != 'artifact'} for r in rows]}

    @app.post('/api/knowledge-bases/{key}/uploads', status_code=202)
    async def upload(key: str, file: UploadFile = File(...), offline: bool = Form(True)):
        library = kb(key)
        if not offline and not library['external_enabled']:
            raise HTTPException(409, '完整解析需先启用此知识库的外部模型处理')
        name = Path((file.filename or 'document').replace('\\', '/')).name
        suffix = Path(name).suffix.lower()
        if suffix not in {'.pdf', '.docx', '.pptx', '.doc', '.ppt', '.png', '.jpg', '.jpeg'}:
            raise HTTPException(415, '不支持该文件类型')
        from .reliability import ensure_space
        try:
            ensure_space(s.data, 100 * 1024**2)
        except RuntimeError as exc:
            raise HTTPException(507, str(exc))
        upload_dir = s.data / 'uploads'
        upload_dir.mkdir(exist_ok=True)
        tmp = upload_dir / (uid('incoming') + '.tmp')
        size, digest, first = 0, hashlib.sha256(), b''
        try:
            with tmp.open('wb') as f:
                while piece := await file.read(1024 * 1024):
                    first = first or piece[:16]
                    size += len(piece)
                    if size > 100 * 1024 * 1024:
                        raise HTTPException(413, '单文件上限100MB')
                    digest.update(piece)
                    f.write(piece)
            valid = ((suffix == '.pdf' and first.startswith(b'%PDF-')) or
                     (suffix in {'.docx', '.pptx'} and first.startswith(b'PK')) or
                     (suffix in {'.doc', '.ppt'} and first.startswith(bytes.fromhex('D0CF11E0'))) or
                     (suffix == '.png' and first.startswith(b'\x89PNG')) or
                     (suffix in {'.jpg', '.jpeg'} and first.startswith(b'\xff\xd8')))
            if not valid:
                raise HTTPException(415, '文件内容与格式不符或文件为空')
            sha = digest.hexdigest()
            doc = 'd_' + sha[:24]
            existing = s.one('SELECT * FROM members WHERE kb=? AND doc=?', (key, doc))
            if existing and existing['active']:
                return {'document_id': doc, 'duplicate': True, 'job_id': None}
            destination = upload_dir / (sha + suffix)
            if destination.exists():
                tmp.unlink()
            else:
                tmp.replace(destination)
            with s.connect() as c:
                c.execute('INSERT OR IGNORE INTO docs VALUES (?,?,?,?,?,?)', (doc, name, sha, str(destination), size, time.time()))
                c.execute("INSERT INTO members(kb,doc,status) VALUES(?,?,'uploaded') ON CONFLICT(kb,doc) DO UPDATE SET active=1,status=CASE WHEN artifact IS NULL THEN 'uploaded' ELSE 'indexing' END", (key, doc))
                c.execute('UPDATE kbs SET generation=generation+1 WHERE id=?', (key,))
            job = s.enqueue(key, 'reindex' if existing and existing['artifact'] else 'parse', {'doc': doc, 'offline': offline})
            return {'document_id': doc, 'duplicate': False, 'job_id': job}
        finally:
            tmp.unlink(missing_ok=True)

    def change_membership(key, doc, active):
        kb(key)
        row = s.one('SELECT * FROM members WHERE kb=? AND doc=?', (key, doc))
        if not row:
            raise HTTPException(404, '文档不存在')
        with s.connect() as c:
            c.execute('UPDATE members SET active=?,status=? WHERE kb=? AND doc=?', (active, 'indexing' if active else 'removed', key, doc))
            c.execute('UPDATE kbs SET generation=generation+1 WHERE id=?', (key,))
        cancel_chats(key)
        job = s.enqueue(key, 'reindex' if row['artifact'] or not active else 'parse', {'doc': doc, 'offline': True})
        return {'job_id': job, 'active': bool(active)}

    @app.delete('/api/knowledge-bases/{key}/documents/{doc}', status_code=202)
    def remove_document(key: str, doc: str):
        return change_membership(key, doc, 0)

    @app.post('/api/knowledge-bases/{key}/documents/{doc}/restore', status_code=202)
    def restore_document(key: str, doc: str):
        return change_membership(key, doc, 1)

    @app.post('/api/knowledge-bases/{key}/documents/{doc}/reparse', status_code=202)
    def reparse(key: str, doc: str):
        kb(key)
        if not s.one('SELECT * FROM members WHERE kb=? AND doc=? AND active=1', (key, doc)):
            raise HTTPException(404)
        return {'job_id': s.enqueue(key, 'parse', {'doc': doc, 'offline': True})}

    @app.get('/api/knowledge-bases/{key}/documents/{doc}/preview')
    def preview(key: str, doc: str):
        kb(key)
        row = s.one('SELECT m.*,d.path,d.name FROM members m JOIN docs d ON d.id=m.doc WHERE m.kb=? AND m.doc=?', (key, doc))
        if not row:
            raise HTTPException(404)
        result = {'name': row['name'], 'quality': row['quality'], 'asset_id': s.asset(row['path'], mime=mimetypes.guess_type(row['path'])[0] or 'application/octet-stream'), 'markdown': '', 'images': []}
        if row['artifact']:
            artifact = Path(row['artifact'])
            md = artifact / 'document.md'
            if md.is_file():
                result['markdown'] = md.read_text(encoding='utf-8')[:1000000]
            images = artifact / 'images'
            if images.is_dir():
                for path in list(images.iterdir())[:100]:
                    if path.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
                        result['images'].append({'asset_id': s.asset(path, mime=mimetypes.guess_type(path)[0] or 'image/png'), 'name': path.name})
        return result

    @app.post('/api/knowledge-bases/{key}/reindex', status_code=202)
    def reindex(key: str):
        kb(key)
        return {'job_id': s.enqueue(key, 'reindex', {})}

    @app.post('/api/knowledge-bases/{key}/search')
    def search(key: str, data: Question):
        library = kb(key)
        if not library['index_version']:
            return {'results': [], 'note': '正在首次建库'}
        folder = s.data / 'runs' / uid('search')
        folder.mkdir(parents=True)
        atomic(folder / 'scope.json', s.scope(key))
        try:
            result = invoke(WORKSPACE / 'DocBase' / '.conda' / 'python.exe', 'docbase',
                ['query', '--storage', s.docbase_storage, '--collection', library['collection'], '--index-version', library['index_version'], '--profile', 'bm25-only',
                 '--prefer-body', '--allowed-doc-ids-file', folder / 'scope.json', '--question', data.question, '--top-k', '8'],
                WORKSPACE / 'DocBase', folder)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        rows = []
        for r in result['results']:
            source = r['sources'][0]
            path = source.get('snapshot_path_absolute')
            rows.append({'text': r['text'], 'name': Path(source['original_path']).name, 'page_index': source.get('page_index'),
                         'asset_id': s.asset(path, mime=mimetypes.guess_type(path)[0] or 'application/pdf') if path else None})
        return {'results': rows, 'index_version': result['index_version']}

    @app.get('/api/conversations')
    def conversations(q: str = ''):
        return s.all('SELECT c.* FROM conversations c JOIN kbs k ON k.id=c.kb WHERE k.deleted=0 AND c.title LIKE ? ORDER BY c.updated DESC', ('%' + q + '%',))

    @app.post('/api/conversations')
    def new_conversation(data: Conversation):
        kb(data.kb)
        key, now = uid('chat'), time.time()
        s.execute('INSERT INTO conversations(id,kb,title,created,updated) VALUES(?,?,?,?,?)', (key, data.kb, data.title, now, now))
        return convo(key)

    @app.patch('/api/conversations/{key}')
    def rename_conversation(key: str, data: Name):
        convo(key)
        s.execute('UPDATE conversations SET title=?,updated=? WHERE id=?', (data.name, time.time(), key))
        return convo(key)

    @app.delete('/api/conversations/{key}')
    def delete_conversation(key: str):
        convo(key)
        with s.connect() as c:
            c.execute("UPDATE jobs SET cancel=1 WHERE id IN(SELECT job FROM messages WHERE conversation=?)", (key,))
            c.execute('DELETE FROM messages WHERE conversation=?', (key,))
            c.execute('DELETE FROM conversations WHERE id=?', (key,))
        return {'status': 'deleted'}

    @app.patch('/api/conversations/{key}/preferences')
    def conversation_preferences(key: str, data: Preferences):
        convo(key)
        current=json.loads(convo(key)['preferences'])
        merged=current | data.model_dump(exclude_unset=True)
        s.execute('UPDATE conversations SET preferences=? WHERE id=?', (dump(merged),key))
        return merged

    @app.get('/api/conversations/{key}/messages')
    def messages(key: str):
        convo(key)
        rows = s.all('SELECT * FROM messages WHERE conversation=? ORDER BY created', (key,))
        for row in rows:
            row['result'] = public_result(row['result'])
            if row['result']:
                for i, citation in enumerate(row['result']['citations']):
                    citation['locator_url'] = f'/messages/{row["id"]}/citations/{i}/locator'
        return rows

    @app.get('/api/conversations/{key}/scope')
    def conversation_scope(key: str):
        convo(key)
        return {'document_ids':json.loads(s.setting('scope:' + key) or 'null')}

    @app.patch('/api/conversations/{key}/scope')
    def save_scope(key: str, data: ScopeRequest):
        row=convo(key)
        valid={r['doc'] for r in s.all('SELECT doc FROM members WHERE kb=? AND active=1',(row['kb'],))}
        if data.document_ids is not None and not set(data.document_ids).issubset(valid):
            raise HTTPException(422,'资料范围含有不存在或已移出的文档')
        s.set_setting('scope:' + key,dump(data.document_ids))
        s.execute("UPDATE jobs SET cancel=1 WHERE id IN (SELECT job FROM messages WHERE conversation=? AND status='pending')", (key,))
        s.execute("UPDATE messages SET status='cancelled',content='检索范围已变化，请重新提问' WHERE conversation=? AND role='assistant' AND status='pending'", (key,))
        return data

    @app.post('/api/conversations/{key}/messages', status_code=202)
    def send_message(key: str, data: Question):
        conversation = convo(key)
        library = kb(conversation['kb'])
        if not library['external_enabled']:
            raise HTTPException(409, '请在知识库页面启用外部模型处理：问题及选中证据将发送至DeepSeek；完整解析会使用Qwen。')
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            request_key = 'message:' + key + ':' + data.request_id
            previous = c.execute('SELECT value FROM settings WHERE key=?', (request_key,)).fetchone()
            if previous:
                return json.loads(previous['value'])
            if not library['index_version']:
                raise HTTPException(409, '资料正在首次建库，请稍后')
            if c.execute("SELECT 1 FROM messages WHERE conversation=? AND status='pending'", (key,)).fetchone():
                raise HTTPException(409, '请等待或停止上一轮回答')
            job, now = uid('job'), time.time()
            payload = {'question': data.question, 'conversation': key, 'message_time': now, 'model_call_budget': data.model_call_budget}
            c.execute('INSERT INTO jobs(id,kb,kind,payload,created,updated) VALUES(?,?,?,?,?,?)', (job, library['id'], 'chat', dump(payload), now, now))
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (uid('msg'), key, 'user', data.question, 'sent', job, None, now))
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (uid('msg'), key, 'assistant', '', 'pending', job, None, now + .0001))
            c.execute('UPDATE conversations SET title=CASE WHEN title=\'新的研究问题\' THEN ? ELSE title END,updated=? WHERE id=?', (data.question[:50], now, key))
            result = {'job_id': job}
            c.execute('INSERT INTO settings VALUES(?,?)', (request_key, dump(result)))
        return result

    @app.get('/api/jobs')
    def jobs():
        rows = s.all('SELECT id,kb,kind,state,stage,error,created,updated FROM jobs WHERE kb IS NULL OR kb IN (SELECT id FROM kbs WHERE deleted=0) ORDER BY created DESC LIMIT 100')
        for row in rows:
            event = s.one("SELECT payload FROM events WHERE job=? AND type='failed' ORDER BY id DESC LIMIT 1", (row['id'],))
            row['failure'] = json.loads(event['payload']) if event else None
        return rows

    @app.get('/api/jobs/{key}')
    def job_status(key: str):
        row = s.one('SELECT id,kb,kind,state,stage,error,created,updated FROM jobs WHERE id=?', (key,))
        if not row:
            raise HTTPException(404)
        event = s.one("SELECT payload FROM events WHERE job=? AND type='failed' ORDER BY id DESC LIMIT 1", (key,))
        row['failure'] = json.loads(event['payload']) if event else None
        return row

    @app.post('/api/jobs/{key}/cancel')
    def cancel_job(key: str):
        job_status(key)
        s.execute("UPDATE jobs SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END WHERE id=? AND state IN ('queued','running')", (key,))
        s.execute("UPDATE messages SET status='cancelled',content='本次回答已停止' WHERE job=? AND role='assistant' AND status='pending'", (key,))
        return job_status(key)

    @app.post('/api/jobs/{key}/retry')
    def retry_job(key: str):
        row = s.one('SELECT * FROM jobs WHERE id=?', (key,))
        if not row or row['state'] not in {'failed', 'interrupted', 'cancelled'}:
            raise HTTPException(409, '该任务不能重试')
        if row['kind'] == 'chat':
            raise HTTPException(409, '问答请在会话中重新发送或重新生成，避免自动重复计费')
        payload = json.loads(row['payload'])
        if row['kb']: kb(row['kb'])
        if row['kind'] == 'parse':
            payload['resume_output'] = payload.get('resume_output') or str(s.data / 'parsed' / row['id'])
        return {'job_id': s.enqueue(row['kb'], row['kind'], payload)}

    @app.get('/api/jobs/{key}/events')
    async def job_events(key: str, request: Request, after: int = 0):
        job_status(key)
        try:
            cursor = max(after, int(request.headers.get('last-event-id', '0')))
        except ValueError:
            raise HTTPException(422)
        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                events = s.all('SELECT * FROM events WHERE job=? AND id>? ORDER BY id', (key, cursor))
                for event in events:
                    cursor = event['id']
                    yield f'id: {cursor}\nevent: progress\ndata: {dump(event)}\n\n'
                if job_status(key)['state'] not in {'queued', 'running'}:
                    yield 'event: done\ndata: {}\n\n'
                    break
                yield ': heartbeat\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})

    @app.get('/api/preferences')
    def preferences():
        return json.loads(s.setting('preferences'))

    @app.patch('/api/preferences')
    def save_preferences(data: Preferences):
        merged=json.loads(s.setting('preferences')) | data.model_dump(exclude_unset=True)
        s.set_setting('preferences',dump(merged))
        return merged

    @app.get('/api/watches')
    def watches():
        return s.all('SELECT * FROM watches ORDER BY id')

    @app.post('/api/watches')
    def create_watch(data: WatchRequest):
        kb(data.kb)
        root = Path(data.path).resolve()
        if not root.is_dir() or root == Path(root.anchor) or root.is_relative_to(s.data):
            raise HTTPException(422, '请选择具体可读资料目录；不能监控磁盘根目录或应用数据目录')
        key = uid('watch')
        s.execute('INSERT INTO watches(id,kb,path) VALUES(?,?,?)',(key,data.kb,str(root)))
        return {'id':key}

    @app.patch('/api/watches/{key}')
    def edit_watch(key: str,data: Enabled):
        if not s.execute('UPDATE watches SET enabled=? WHERE id=?',(int(data.enabled),key)):
            raise HTTPException(404)
        return {'enabled':data.enabled}

    @app.get('/api/sources/{key}')
    def asset(key: str):
        row = s.one('SELECT * FROM assets WHERE id=?', (key,))
        if not row or not Path(row['path']).is_file():
            raise HTTPException(404, '来源已不可用')
        s.asset(row['path'], row['name'], row['mime'], row['doc'])
        return FileResponse(row['path'], media_type=row['mime'], filename=row['name'], content_disposition_type='inline')

    from .quality_routes import install
    install(app, s, kb, cancel_chats)

    dist = ROOT / 'web' / 'dist'
    if dist.is_dir():
        app.mount('/', StaticFiles(directory=dist, html=True), name='web')
    return app
