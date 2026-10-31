"""Additive V2 schema; legacy message rows and immutable source artifacts are unchanged."""
import json,time
from .store import uid,dump

def migrate(s):
    with s.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS conversation_archives(conversation TEXT PRIMARY KEY,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS branches(id TEXT PRIMARY KEY,conversation TEXT NOT NULL,name TEXT NOT NULL,
          head TEXT,parent_branch TEXT,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS message_links(message TEXT PRIMARY KEY,branch TEXT NOT NULL,parent TEXT);
        CREATE TABLE IF NOT EXISTS message_drafts(job TEXT PRIMARY KEY,text TEXT NOT NULL,sequence INTEGER DEFAULT 0,updated REAL);
        CREATE TABLE IF NOT EXISTS saved_items(id TEXT PRIMARY KEY,conversation TEXT,message TEXT,kind TEXT,
          evidence_id TEXT,note TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS document_metadata(kb TEXT,doc TEXT,metadata TEXT NOT NULL,revision INTEGER DEFAULT 1,
          PRIMARY KEY(kb,doc));
        CREATE TABLE IF NOT EXISTS model_profile_revisions(id TEXT PRIMARY KEY,role TEXT,config TEXT,credential TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS model_profiles(role TEXT PRIMARY KEY,revision TEXT);
        CREATE TABLE IF NOT EXISTS zotero_sources(id TEXT PRIMARY KEY,kb TEXT,path TEXT,storage_root TEXT,enabled INTEGER DEFAULT 0,
          last_scan REAL DEFAULT 0,error TEXT);
        CREATE TABLE IF NOT EXISTS zotero_links(source TEXT,item_key TEXT,attachment_key TEXT,doc TEXT,sha TEXT,
          metadata_hash TEXT,path TEXT,missing INTEGER DEFAULT 0,PRIMARY KEY(source,attachment_key));
        CREATE TABLE IF NOT EXISTS sync_runs(id TEXT PRIMARY KEY,source TEXT,preview TEXT,source_hash TEXT,created REAL,state TEXT DEFAULT 'preview');
        UPDATE schema_version SET version=3 WHERE version<3;
        ''')
    for row in s.all('SELECT id FROM conversations'):ensure_branch(s,row['id'])

def ensure_branch(s,conversation):
    key='main_'+conversation
    with s.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR IGNORE INTO branches VALUES(?,?,?,?,?,?)',(key,conversation,'主分支',None,None,time.time()))
        # Adopt any legacy/directly imported messages once; V2 sends create links transactionally.
        head=c.execute('SELECT head FROM branches WHERE id=?',(key,)).fetchone()['head']
        for row in c.execute('SELECT id FROM messages WHERE conversation=? AND id NOT IN(SELECT message FROM message_links) ORDER BY created,id',(conversation,)).fetchall():
            c.execute('INSERT INTO message_links VALUES(?,?,?)',(row['id'],key,head));head=row['id']
        c.execute('UPDATE branches SET head=? WHERE id=?',(head,key))
        setting='branch:'+conversation
        c.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(setting,key))
    return s.setting('branch:'+conversation)

def ancestors(s,conversation,branch=None,head=None):
    branch=branch or ensure_branch(s,conversation)
    row=s.one('SELECT * FROM branches WHERE id=? AND conversation=?',(branch,conversation))
    if not row:raise ValueError('对话分支不存在')
    cursor=row['head'] if head is None else head
    result=[];seen=set()
    while cursor:
        if cursor in seen or len(seen)>10000:raise ValueError('对话链数据异常')
        seen.add(cursor)
        message=s.one('SELECT m.*,l.parent,l.branch FROM messages m JOIN message_links l ON l.message=m.id WHERE m.id=? AND m.conversation=?',(cursor,conversation))
        if not message:raise ValueError('对话祖先消息缺失')
        result.append(message);cursor=message['parent']
    return result[::-1]

def fork(s,conversation,message,mode='continue',name='新的思路'):
    ensure_branch(s,conversation)
    row=s.one('SELECT m.*,l.parent,l.branch FROM messages m JOIN message_links l ON l.message=m.id WHERE m.id=? AND m.conversation=?',(message,conversation))
    if not row:raise ValueError('消息不存在')
    seed='';head=message
    if mode=='edit':
        if row['role']!='user':raise ValueError('只能编辑用户问题')
        seed=row['content'];head=row['parent']
    elif mode=='regenerate':
        if row['role']!='assistant':raise ValueError('请选择待重新生成的回答')
        question=s.one('SELECT m.*,l.parent FROM messages m JOIN message_links l ON l.message=m.id WHERE m.id=?',(row['parent'],))
        if not question or question['role']!='user':raise ValueError('找不到原问题')
        seed=question['content'];head=question['parent']
    key=uid('branch')
    with s.connect() as c:
        c.execute('INSERT INTO branches VALUES(?,?,?,?,?,?)',(key,conversation,name,head,row['branch'],time.time()))
        c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('branch:'+conversation,key))
    return {'id':key,'seed_question':seed,'name':name}

def metadata(s,kb,doc):
    row=s.one('SELECT metadata,revision FROM document_metadata WHERE kb=? AND doc=?',(kb,doc))
    return json.loads(row['metadata'])|{'revision':row['revision']} if row else {
        'title':'','authors':[],'year':None,'doi':'','venue':'','tags':[], 'provenance':{},'revision':0}

def set_metadata(s,kb,doc,value,origin='manual'):
    prior=metadata(s,kb,doc);value={k:v for k,v in value.items() if k in {'title','authors','year','doi','venue','tags'}}
    if origin!='manual':value={k:v for k,v in value.items() if prior.get('provenance',{}).get(k,{}).get('source')!='manual'}
    merged={k:v for k,v in prior.items() if k!='revision'}|value
    provenance=dict(prior.get('provenance',{}))
    for key in value:provenance[key]={'source':origin,'reviewed':origin=='manual','updated':time.time()}
    merged['provenance']=provenance
    s.execute('INSERT INTO document_metadata VALUES(?,?,?,?) ON CONFLICT(kb,doc) DO UPDATE SET metadata=excluded.metadata,revision=document_metadata.revision+1',
              (kb,doc,dump(merged),1))
    return metadata(s,kb,doc)

def filtered_documents(s,kb,filters=None):
    filters=validate_filters(filters);output=[]
    for row in s.all('SELECT m.*,d.name,d.size,d.created FROM members m JOIN docs d ON d.id=m.doc WHERE m.kb=? AND m.active=1 ORDER BY d.created DESC',(kb,)):
        meta=metadata(s,kb,row['doc']);year=meta.get('year')
        if filters.get('year_from') is not None and (year is None or year<filters['year_from']):continue
        if filters.get('year_to') is not None and (year is None or year>filters['year_to']):continue
        if filters.get('author') and not any(filters['author'].casefold() in str(a).casefold() for a in meta.get('authors',[])):continue
        if filters.get('tag') and filters['tag'] not in meta.get('tags',[]):continue
        if filters.get('status') and row['status']!=filters['status']:continue
        row['metadata']=meta;output.append(row)
    return output

def validate_filters(value):
    if value is None:return {}
    if not isinstance(value,dict) or set(value)-{'year_from','year_to','author','tag','status'}:raise ValueError('筛选字段无效')
    for key in ('year_from','year_to'):
        if value.get(key) is not None and (type(value[key]) is not int or not 1000<=value[key]<=2200):raise ValueError('年份须为1000至2200的整数')
    for key in ('author','tag','status'):
        if key in value and (not isinstance(value[key],str) or len(value[key])>300):raise ValueError('筛选文本无效')
    if value.get('year_from') and value.get('year_to') and value['year_from']>value['year_to']:raise ValueError('起始年份不能晚于截止年份')
    return value
