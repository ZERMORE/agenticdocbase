"""Read-only Zotero JSON/storage sync. No SQLite writes to Zotero; parsing is offline."""
import hashlib,json,os,shutil,time
from pathlib import Path
from .store import uid,dump
from .v2_store import set_metadata

def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def attachments(source):
    export=Path(source['path']).resolve();storage=Path(source['storage_root']).resolve()
    if not export.is_file() or not storage.is_dir():raise ValueError('导出JSON或storage目录暂不可达，未删除任何资料')
    if export.suffix.lower()!='.json' or export.stat().st_size>50*1024**2:raise ValueError('请选择不超过50MB的Zotero JSON导出文件')
    data=json.loads(export.read_text(encoding='utf-8-sig'));items=data if isinstance(data,list) else data.get('items',[])
    result=[];seen=set()
    for item in items:
        if not isinstance(item,dict):continue
        itemkey=str(item.get('key') or item.get('itemKey') or item.get('uri','').rstrip('/').split('/')[-1] or hashlib.sha256(dump([item.get('DOI'),item.get('title')]).encode()).hexdigest()[:16])
        authors=[]
        for creator in item.get('creators',[]):
            name=creator.get('name') or ' '.join(str(creator.get(k,'')) for k in ('firstName','lastName')).strip()
            if name:authors.append(name[:300])
        import re
        date=str(item.get('date',''));match=re.search(r'\b(1[5-9]\d\d|20\d\d|21\d\d)\b',date)
        meta={'title':str(item.get('title',''))[:500],'authors':authors[:100],'year':int(match[0]) if match else None,
              'doi':str(item.get('DOI',''))[:200],'venue':str(item.get('publicationTitle',''))[:300],
              'tags':[str(x.get('tag','') if isinstance(x,dict) else x)[:80] for x in item.get('tags',[])][:50]}
        for attachment in item.get('attachments',[]):
            value=attachment.get('path') or attachment.get('localPath')
            if not isinstance(value,str):continue
            if value.startswith('storage:'):
                pieces=value.split(':',2)
                if len(pieces)==3:path=storage/pieces[1]/pieces[2]
                else:path=storage/str(attachment.get('key',''))/pieces[-1]
            else:
                raw=Path(value)
                path=raw if raw.is_absolute() else export.parent/raw
            path=path.resolve()
            if not (path.is_relative_to(storage) or path.is_relative_to(export.parent)) or path.suffix.lower()!='.pdf':continue
            key=str(attachment.get('key') or attachment.get('attachmentKey') or path.parent.name)
            if not key or key in seen:
                result.append({'attachment_key':key,'action':'conflict','reason':'附件键重复，需修正导出文件'});continue
            seen.add(key)
            row={'attachment_key':key,'item_key':itemkey,'path':str(path),'name':path.name,'metadata':meta,
                 'metadata_hash':hashlib.sha256(dump(meta).encode()).hexdigest()}
            if not path.is_file():row.update(action='missing',reason='源附件暂不可用')
            elif path.stat().st_size>100*1024**2:row.update(action='conflict',reason='附件超过100MB')
            else:
                with path.open('rb') as stream:valid=stream.read(5)==b'%PDF-'
                if not valid:row.update(action='conflict',reason='文件内容不是PDF')
                else:row['sha']=sha(path)
            result.append(row)
    return result,sha(export)

def preview(s,source_id):
    source=s.one('SELECT * FROM zotero_sources WHERE id=?',(source_id,))
    if not source:raise ValueError('同步来源不存在')
    items,source_hash=attachments(source)
    old={x['attachment_key']:x for x in s.all('SELECT * FROM zotero_links WHERE source=?',(source_id,))}
    for item in items:
        if item.get('action'):continue
        prior=old.get(item['attachment_key'])
        member=s.one('SELECT active FROM members WHERE kb=? AND doc=?',(source['kb'],'d_'+item['sha'][:24]))
        if prior and prior['sha']==item['sha']:
            item['action']='metadata' if prior['metadata_hash']!=item['metadata_hash'] else 'unchanged'
        elif prior:item['action']='changed'
        else:item['action']='duplicate' if member else 'new'
        if member and not member['active']:item['action']='removed';item['reason']='已由用户移出，自动同步不会恢复'
    present={x['attachment_key'] for x in items}
    for key,row in old.items():
        if key not in present:items.append({'attachment_key':key,'item_key':row['item_key'],'action':'missing','reason':'导出文件不再列出附件，保留库内资料','path':row['path']})
    key=uid('sync')
    s.execute('INSERT INTO sync_runs(id,source,preview,source_hash,created) VALUES(?,?,?,?,?)',(key,source_id,dump({'items':items,'applied':[]}),source_hash,time.time()))
    return {'id':key,'items':[{k:v for k,v in row.items() if k not in {'path','sha','metadata_hash'}} for row in items],
            'counts':{a:sum(x['action']==a for x in items) for a in sorted({x['action'] for x in items})}}

def apply(s,run_id,check=lambda:None):
    run=s.one('SELECT * FROM sync_runs WHERE id=?',(run_id,))
    if not run:raise ValueError('同步预览不存在')
    source=s.one('SELECT * FROM zotero_sources WHERE id=?',(run['source'],))
    library=s.one('SELECT deleted FROM kbs WHERE id=?',(source['kb'],))
    if not library or library['deleted']:raise ValueError('知识库已回收')
    if sha(source['path'])!=run['source_hash']:raise ValueError('导出文件已变化，请重新预览')
    plan=json.loads(run['preview']);done=set(plan['applied']);count=0
    for item in plan['items']:
        check();key=item['attachment_key']
        if key in done:continue
        action=item['action']
        if action=='missing':s.execute('UPDATE zotero_links SET missing=1 WHERE source=? AND attachment_key=?',(source['id'],key))
        elif action in {'new','changed','duplicate','metadata','unchanged'}:
            path=Path(item['path'])
            if not path.is_file() or sha(path)!=item['sha']:raise ValueError('附件在预览后发生变化，请重新预览')
            doc='d_'+item['sha'][:24];existing=s.one('SELECT * FROM members WHERE kb=? AND doc=?',(source['kb'],doc))
            if existing and not existing['active']:continue
            target=s.data/'uploads'/(item['sha']+'.pdf');target.parent.mkdir(exist_ok=True)
            if not target.exists():
                temporary=target.with_name(target.name+'.'+uid('copy')+'.tmp');shutil.copyfile(path,temporary)
                if sha(temporary)!=item['sha']:temporary.unlink();raise ValueError('复制时源文件变化')
                temporary.replace(target)
            prior=s.one('SELECT doc FROM zotero_links WHERE source=? AND attachment_key=?',(source['id'],key))
            with s.connect() as c:
                c.execute('INSERT OR IGNORE INTO docs VALUES(?,?,?,?,?,?)',(doc,item['name'],item['sha'],str(target),target.stat().st_size,time.time()))
                c.execute('INSERT OR IGNORE INTO members(kb,doc) VALUES(?,?)',(source['kb'],doc))
                c.execute('INSERT OR REPLACE INTO zotero_links VALUES(?,?,?,?,?,?,?,0)',(source['id'],item['item_key'],key,doc,item['sha'],item['metadata_hash'],str(path)))
                if prior and prior['doc']!=doc:
                    shared=c.execute('SELECT 1 FROM zotero_links z JOIN zotero_sources src ON src.id=z.source WHERE src.kb=? AND z.doc=?',(source['kb'],prior['doc'])).fetchone()
                    if not shared:c.execute("UPDATE members SET active=0,status='removed' WHERE kb=? AND doc=?",(source['kb'],prior['doc']))
                if not existing:
                    c.execute('UPDATE kbs SET generation=generation+1 WHERE id=?',(source['kb'],))
                    c.execute("UPDATE jobs SET cancel=1 WHERE kb=? AND kind='chat' AND state IN ('queued','running')",(source['kb'],))
            if action!='unchanged':
                set_metadata(s,source['kb'],doc,item['metadata'],origin='zotero')
                if existing:
                    s.execute('UPDATE kbs SET published_generation=CASE WHEN published_generation=generation THEN generation+1 ELSE published_generation END,generation=generation+1 WHERE id=?',(source['kb'],))
                    s.execute("UPDATE jobs SET cancel=1 WHERE kb=? AND kind='chat' AND state IN ('queued','running')",(source['kb'],))
            if not existing or not existing['artifact']:
                queued=any(json.loads(j['payload']).get('doc')==doc for j in s.all("SELECT payload FROM jobs WHERE kb=? AND kind='parse' AND state IN ('queued','running')",(source['kb'],)))
                if not queued:s.enqueue(source['kb'],'parse',{'doc':doc,'offline':True})
            count+=1
        done.add(key);plan['applied']=sorted(done)
        s.execute("UPDATE sync_runs SET preview=?,state='running' WHERE id=?",(dump(plan),run_id))
    s.execute("UPDATE sync_runs SET state='succeeded' WHERE id=?",(run_id,))
    s.execute('UPDATE zotero_sources SET last_scan=?,error=NULL WHERE id=?',(time.time(),source['id']))
    return {'sync_run':run_id,'processed':count,'new_model_requests':0}

def scan(s):
    for source in s.all('SELECT z.* FROM zotero_sources z JOIN kbs k ON k.id=z.kb WHERE z.enabled=1 AND k.deleted=0 AND z.last_scan<?',(time.time()-60,)):
        try:
            if s.one("SELECT 1 FROM jobs WHERE kb=? AND kind='zotero_sync' AND state IN ('queued','running')",(source['kb'],)):continue
            plan=preview(s,source['id'])
            if any(k in {'new','changed','duplicate','metadata'} and v for k,v in plan['counts'].items()):s.enqueue(source['kb'],'zotero_sync',{'sync_run':plan['id']})
            else:s.execute('DELETE FROM sync_runs WHERE id=?',(plan['id'],))
            s.execute('UPDATE zotero_sources SET last_scan=? WHERE id=?',(time.time(),source['id']))
        except (OSError,ValueError):s.execute('UPDATE zotero_sources SET last_scan=?,error=? WHERE id=?',(time.time(),'来源暂不可用或格式变化，未移除资料',source['id']))
