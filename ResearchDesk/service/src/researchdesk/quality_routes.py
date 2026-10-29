"""Quality endpoints use existing local-session middleware and library lookup."""
import json
from pathlib import Path
import time
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .store import atomic, uid, WORKSPACE, dump
from .quality import inspect
from .processes import invoke

class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['edit','page','image']
    reason: str = Field(min_length=1, max_length=500)
    block_id: str | None = Field(default=None, max_length=200)
    page_index: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, max_length=50000)
    offline: bool = True
    expected_revision: str = Field(min_length=1,max_length=100)
    request_id: str = Field(min_length=1,max_length=100)

class BackupPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=720)
    keep: int = Field(default=7,ge=1,le=100)

def install(app, s, kb, cancel_chats):
    def member(key, doc):
        kb(key)
        row = s.one('SELECT * FROM members WHERE kb=? AND doc=?', (key, doc))
        if not row or not row['artifact']:
            raise HTTPException(404, '文档尚无解析产物')
        return row

    @app.get('/api/knowledge-bases/{key}/documents/{doc}/quality')
    def quality(key: str, doc: str):
        row = member(key, doc)
        result = inspect(row['artifact'])
        result['editable'] = bool(row['active'])
        return result

    @app.post('/api/knowledge-bases/{key}/documents/{doc}/revisions', status_code=202)
    def revise(key: str, doc: str, data: Revision):
        row = member(key, doc)
        quality = inspect(row['artifact'])
        if not row['active'] or quality['artifact_revision'] != data.expected_revision:
            raise HTTPException(409, '资料已更新或移出，请刷新质量报告')
        if not data.offline and not kb(key)['external_enabled']:
            raise HTTPException(409, '请先启用此知识库的外部模型处理')
        if not data.reason.strip(): raise HTTPException(422, '请填写修订原因')
        blocks = {b['block_id']: b for b in quality['blocks']}
        if data.action in {'edit','image'}:
            block = blocks.get(data.block_id)
            if not block or (data.action == 'image' and block['type'] != 'image'):
                raise HTTPException(422, '无效的目标块')
            if data.action == 'edit' and (block['type']=='image' or not (data.text or '').strip()):
                raise HTTPException(422, '请选择文字块并填写修正文字')
        if data.action == 'page' and (data.page_index is None or data.page_index >= (quality['page_count'] or 0)):
            raise HTTPException(422, '页序号无效')
        # Idempotent revision requests are scoped to this knowledge base and document.
        request_key = 'revision:' + key + ':' + doc + ':' + data.request_id
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            prior = c.execute('SELECT value FROM settings WHERE key=?',(request_key,)).fetchone()
            if prior: return json.loads(prior['value'])
            job, now = uid('job'), time.time()
            payload = data.model_dump(exclude={'expected_revision','request_id'}) | {'doc':doc, 'artifact':row['artifact']}
            c.execute('INSERT INTO jobs(id,kb,kind,payload,created,updated) VALUES(?,?,?,?,?,?)', (job,key,'revise',dump(payload),now,now))
            result = {'job_id':job}
            c.execute('INSERT INTO settings VALUES(?,?)', (request_key,dump(result)))
        cancel_chats(key)
        return result

    @app.get('/api/operations')
    def operations():
        return {'backup_policy': json.loads(s.setting('backup_policy') or '{"enabled":false,"interval_hours":24,"keep":7}'),
                'last_backup': s.one("SELECT state,created FROM backup_records WHERE state='succeeded' ORDER BY created DESC LIMIT 1"),
                'backup_error': s.setting('backup_error'),
                'disk_free_bytes': __import__('shutil').disk_usage(s.data).free}

    @app.patch('/api/operations/backup-policy')
    def policy(data: BackupPolicy):
        s.set_setting('backup_policy',dump(data.model_dump()))
        return data

    @app.post('/api/operations/backup', status_code=202)
    def backup():
        return {'job_id': s.enqueue(None,'backup',{})}

    @app.get('/api/messages/{message}/citations/{number}/locator')
    def locator(message: str, number: int):
        row = s.one('SELECT m.result,c.kb FROM messages m JOIN conversations c ON c.id=m.conversation WHERE m.id=?', (message,))
        if not row: raise HTTPException(404)
        kb(row['kb'])
        citations = json.loads(row['result'] or '{}').get('citations',[])
        if not 0 <= number < len(citations): raise HTTPException(404)
        citation = citations[number]
        source = citation['sources'][0]
        path = source.get('rendered_pdf_path_absolute') or source.get('snapshot_path_absolute')
        page = source.get('page_index')
        if not path or Path(path).suffix.lower() != '.pdf' or not page:
            return {'kind':'block', 'note':'非PDF或无可靠页码，请阅读原始引用块。', 'text':citation['quote']}
        # Validate containment via asset registry before invoking the renderer.
        s.asset(path)
        folder = s.data/'runs'/uid('locator')
        folder.mkdir(parents=True)
        request = {'path':path,'page_index':page-1,'quote':citation['quote'], 'bbox':source.get('bbox')}
        if path == source.get('snapshot_path_absolute'): request['source_hash'] = source.get('revision')
        atomic(folder/'request.json', request)
        try:
            result = invoke(WORKSPACE/'doc2md/.conda/python.exe','doc2md.locator',
                            ['--request-file',folder/'request.json','--output',folder/'page.png'],WORKSPACE/'doc2md',folder/'render',timeout=30)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        result['image_asset_id'] = s.asset(folder/'page.png',mime='image/png')
        result.pop('image',None)
        return result
