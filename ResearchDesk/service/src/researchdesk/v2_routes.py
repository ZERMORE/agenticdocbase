import json,time
from typing import Literal
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field
from .store import uid,dump
from .v2_store import ensure_branch,ancestors,fork,metadata,set_metadata,filtered_documents
from .model_profiles import ModelProfiles,ROLES

class Fork(BaseModel):
    message_id:str
    mode:Literal['edit','regenerate','continue']='continue'
    name:str=Field(default='新的思路',min_length=1,max_length=100)
class Meta(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(default='',max_length=500)
    authors:list[str]=Field(default_factory=list,max_length=100)
    year:int|None=Field(default=None,ge=1000,le=2200)
    doi:str=Field(default='',max_length=200)
    venue:str=Field(default='',max_length=300)
    tags:list[str]=Field(default_factory=list,max_length=50)
class Profile(BaseModel):
    model_config=ConfigDict(extra='forbid')
    base_url:str=Field(max_length=500)
    model:str=Field(min_length=1,max_length=200)
    timeout:float=Field(default=60,ge=1,le=300)
    max_calls:int=Field(default=6,ge=1,le=6)
    max_tokens:int=Field(default=2500,ge=128,le=16000)
    stream_mode:Literal['auto','off']='auto'
    inherit:bool=False
    api_key:str=Field(default='',max_length=1000,repr=False)
    clear_key:bool=False
class ZoteroSource(BaseModel):
    kb:str
    path:str=Field(max_length=1000)
    storage_root:str=Field(max_length=1000)
    enabled:bool=False

class Bookmark(BaseModel):
    message_id:str
    evidence_id:str|None=None
    note:str=Field(default='',max_length=1000)

def install(app,s,kb,convo,public_result,cancel_chats):
    @app.get('/api/conversations/{key}/branches')
    def branches(key:str):
        convo(key);active=ensure_branch(s,key)
        return {'active':active,'items':s.all('SELECT * FROM branches WHERE conversation=? ORDER BY created',(key,))}
    @app.post('/api/conversations/{key}/branches')
    def create_branch(key:str,data:Fork):
        convo(key)
        try:return fork(s,key,data.message_id,data.mode,data.name)
        except ValueError as e:raise HTTPException(422,str(e))
    @app.patch('/api/conversations/{key}/branches/{branch}')
    def rename_branch(key:str,branch:str,data:dict):
        convo(key);name=data.get('name','')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise HTTPException(422,'分支名称无效')
        if not s.execute('UPDATE branches SET name=? WHERE id=? AND conversation=?',(name.strip(),branch,key)):raise HTTPException(404)
        return {'id':branch,'name':name.strip()}
    @app.post('/api/conversations/{key}/branches/{branch}/activate')
    def activate(key:str,branch:str):
        convo(key)
        if not s.one('SELECT 1 FROM branches WHERE id=? AND conversation=?',(branch,key)):raise HTTPException(404)
        s.set_setting('branch:'+key,branch)
        return {'active':branch}
    @app.post('/api/conversations/{key}/reveal/{message}')
    def reveal(key:str,message:str):
        convo(key)
        row=s.one('SELECT l.branch FROM message_links l JOIN messages m ON m.id=l.message WHERE m.id=? AND m.conversation=?',(message,key))
        if not row:raise HTTPException(404)
        s.set_setting('branch:'+key,row['branch']);return {'branch':row['branch']}
    @app.get('/api/conversations/{key}/export')
    def export(key:str,format:Literal['markdown','json']='markdown'):
        conversation=convo(key);rows=ancestors(s,key)
        safe=[]
        for row in rows:
            safe.append({k:row.get(k) for k in ('id','parent','branch','role','content','status','created')}|
                        {'result':public_result(row['result'])})
        for message in safe:
            result=message.get('result') or {}
            if message['role']=='assistant' and result.get('claims'):
                message['content']='\n\n'.join(c['text']+' '+ ' '.join('['+x+']' for x in dict.fromkeys(p['evidence_id'] for p in c.get('supports',[]))) for c in result['claims'])
        if format=='json':
            return Response(dump({'schema_version':'conversation-v2','title':conversation['title'],'messages':safe}),media_type='application/json',headers={'Content-Disposition':'attachment; filename="conversation.json"'})
        lines=['# '+conversation['title'],'']
        for row in safe:
            lines+=['## '+('问题' if row['role']=='user' else '回答'),f"状态：{row['status']}；消息：{row['id']}",'',row['content'],'']
            result=row.get('result') or {}
            if result.get('request_snapshot'):lines+=['```json',dump({'request':result['request_snapshot'],'models':result.get('model_snapshot',{})}),'```']
            for cite in result.get('citations',[]):
                for source in cite['sources']:
                    if source.get('asset_id'):lines.append(f"- [{cite['evidence_id']}] {source.get('name','')}：/api/sources/{source['asset_id']}#page={source.get('page_index') or 1}")
        return Response('\n'.join(lines),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename="conversation.md"'})
    @app.post('/api/saved-items')
    def save_item(data:Bookmark):
        row=s.one('SELECT * FROM messages WHERE id=?',(data.message_id,))
        if not row:raise HTTPException(404)
        convo(row['conversation'])
        if data.evidence_id:
            result=json.loads(row['result'] or '{}')
            if not any(c['evidence_id']==data.evidence_id for c in result.get('citations',[])):raise HTTPException(422,'引用不存在')
        existing=s.one('SELECT id FROM saved_items WHERE message=? AND COALESCE(evidence_id,\'\')=?',(row['id'],data.evidence_id or ''))
        if existing:return existing
        key=uid('saved');s.execute('INSERT INTO saved_items VALUES(?,?,?,?,?,?,?)',(key,row['conversation'],row['id'],'citation' if data.evidence_id else 'answer',data.evidence_id,data.note,time.time()))
        return {'id':key}
    @app.get('/api/saved-items')
    def saved():
        return s.all('SELECT b.*,c.title FROM saved_items b JOIN conversations c ON c.id=b.conversation JOIN kbs k ON k.id=c.kb WHERE k.deleted=0 ORDER BY b.created DESC')
    @app.delete('/api/saved-items/{key}')
    def delete_saved(key:str):
        s.execute('DELETE FROM saved_items WHERE id=?',(key,));return {'status':'deleted'}
    @app.get('/api/knowledge-bases/{key}/documents/{doc}/metadata')
    def read_metadata(key:str,doc:str):
        kb(key)
        if not s.one('SELECT 1 FROM members WHERE kb=? AND doc=?',(key,doc)):raise HTTPException(404)
        return metadata(s,key,doc)
    @app.patch('/api/knowledge-bases/{key}/documents/{doc}/metadata')
    def write_metadata(key:str,doc:str,data:Meta):
        read_metadata(key,doc)
        value=data.model_dump(exclude_unset=True)
        if any(len(x)>300 for x in value.get('authors',[])) or any(len(x)>80 for x in value.get('tags',[])):raise HTTPException(422,'作者或标签过长')
        result=set_metadata(s,key,doc,value)
        s.execute('UPDATE kbs SET published_generation=CASE WHEN published_generation=generation THEN generation+1 ELSE published_generation END,generation=generation+1 WHERE id=?',(key,))
        cancel_chats(key);return result
    @app.post('/api/knowledge-bases/{key}/filter')
    def filter_members(key:str,data:dict):
        kb(key)
        try:rows=filtered_documents(s,key,data)
        except (TypeError,ValueError):raise HTTPException(422,'筛选条件无效')
        return {'items':[{k:v for k,v in r.items() if k!='artifact'} for r in rows],'total':len(rows)}
    @app.get('/api/zotero-sources')
    def zotero_sources():return s.all('SELECT z.* FROM zotero_sources z JOIN kbs k ON k.id=z.kb WHERE k.deleted=0')
    @app.post('/api/zotero-sources')
    def add_zotero(data:ZoteroSource):
        from pathlib import Path
        kb(data.kb);path=Path(data.path).resolve();storage=Path(data.storage_root).resolve()
        if path.suffix.lower()!='.json' or not path.is_file() or not storage.is_dir() or storage==Path(storage.anchor):raise HTTPException(422,'请选择具体JSON导出文件与storage目录')
        key=uid('zotero');s.execute('INSERT INTO zotero_sources(id,kb,path,storage_root,enabled) VALUES(?,?,?,?,?)',(key,data.kb,str(path),str(storage),int(data.enabled)))
        return {'id':key}
    @app.patch('/api/zotero-sources/{key}')
    def pause_zotero(key:str,data:dict):
        if type(data.get('enabled')) is not bool:raise HTTPException(422)
        row=s.one('SELECT * FROM zotero_sources WHERE id=?',(key,))
        if not row:raise HTTPException(404)
        kb(row['kb']);s.execute('UPDATE zotero_sources SET enabled=? WHERE id=?',(int(data['enabled']),key));return data
    @app.post('/api/zotero-sources/{key}/preview')
    def preview_zotero(key:str):
        from .zotero import preview
        row=s.one('SELECT * FROM zotero_sources WHERE id=?',(key,))
        if not row:raise HTTPException(404)
        kb(row['kb'])
        try:return preview(s,key)
        except (OSError,ValueError,KeyError):raise HTTPException(422,'来源不可用或JSON格式不符，请检查Zotero导出')
    @app.post('/api/zotero-sync/{key}/apply',status_code=202)
    def apply_zotero(key:str):
        row=s.one('SELECT z.kb,r.state FROM sync_runs r JOIN zotero_sources z ON z.id=r.source WHERE r.id=?',(key,))
        if not row:raise HTTPException(404)
        kb(row['kb'])
        if row['state']=='succeeded':return {'status':'already_applied'}
        for job in s.all("SELECT id,payload FROM jobs WHERE kb=? AND kind='zotero_sync' AND state IN ('queued','running')",(row['kb'],)):
            if json.loads(job['payload']).get('sync_run')==key:return {'job_id':job['id']}
        return {'job_id':s.enqueue(row['kb'],'zotero_sync',{'sync_run':key})}

    @app.get('/api/models')
    def profiles():
        manager=ModelProfiles(s);manager.ensure()
        return {'items':[manager.get(role) for role in ROLES],'retrieval':'bm25-only',
                'note':'当前检索使用本地BM25。更换向量模型配置不会复用旧向量索引，也不自动触发计费重建。'}
    @app.put('/api/models/{role}')
    def update_profile(role:str,data:Profile):
        try:return ModelProfiles(s).save(role,data.model_dump())
        except (ValueError,RuntimeError) as e:raise HTTPException(422,str(e))
    @app.get('/api/models/{role}/revisions')
    def model_revisions(role:str):
        if role not in ROLES:raise HTTPException(404)
        return s.all('SELECT id,role,config,created FROM model_profile_revisions WHERE role=? ORDER BY created DESC',(role,))
    @app.post('/api/models/{role}/probe',status_code=202)
    def probe(role:str):
        if role not in ROLES:raise HTTPException(404)
        snapshot=ModelProfiles(s).snapshot()
        return {'job_id':s.enqueue(None,'model_probe',{'role':role,'model_snapshot':snapshot})}
