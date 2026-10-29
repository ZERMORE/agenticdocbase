import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT.parent
DATA = Path(os.environ.get('RESEARCHDESK_DATA', ROOT / 'data')).resolve()


def uid(prefix):
    return prefix + '_' + uuid.uuid4().hex[:20]


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(dump(value), encoding='utf-8')
    os.replace(tmp, path)


class Store:
    def __init__(self, data=None):
        self.data = Path(data or DATA).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / 'app.sqlite'

    @property
    def docbase_storage(self):
        restored = self.data / 'docbase_storage'
        return restored if restored.is_dir() else WORKSPACE / 'DocBase' / 'storage'

    @contextlib.contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def all(self, sql, args=()):
        with self.connect() as c:
            return [dict(row) for row in c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connect() as c:
            return c.execute(sql, args).rowcount

    def init(self, seed=True):
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS kbs(id TEXT PRIMARY KEY,name TEXT NOT NULL,collection TEXT UNIQUE NOT NULL,
              generation INTEGER DEFAULT 0,published_generation INTEGER DEFAULT -1,index_version TEXT,
              external_enabled INTEGER DEFAULT 0,deleted INTEGER DEFAULT 0,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS docs(id TEXT PRIMARY KEY,name TEXT NOT NULL,sha TEXT NOT NULL,
              path TEXT NOT NULL,size INTEGER NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS members(kb TEXT REFERENCES kbs(id),doc TEXT REFERENCES docs(id),
              active INTEGER DEFAULT 1,docbase_id TEXT,artifact TEXT,quality TEXT DEFAULT 'pending',
              status TEXT DEFAULT 'uploaded',pages INTEGER,PRIMARY KEY(kb,doc));
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,kb TEXT REFERENCES kbs(id),title TEXT NOT NULL,
              preferences TEXT DEFAULT '{}',created REAL NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,conversation TEXT REFERENCES conversations(id),
              role TEXT NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL,job TEXT,result TEXT,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kb TEXT,kind TEXT NOT NULL,payload TEXT NOT NULL,
              state TEXT DEFAULT 'queued',stage TEXT DEFAULT 'queued',token TEXT,lease REAL DEFAULT 0,
              attempt INTEGER DEFAULT 0,cancel INTEGER DEFAULT 0,error TEXT,result TEXT,created REAL,updated REAL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT NOT NULL,
              type TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,name TEXT NOT NULL,
              mime TEXT NOT NULL,doc TEXT);
            CREATE TABLE IF NOT EXISTS watches(id TEXT PRIMARY KEY,kb TEXT NOT NULL,path TEXT NOT NULL,
              enabled INTEGER DEFAULT 1,last_scan REAL DEFAULT 0,error TEXT);
            ''')
            c.executescript("""
            CREATE TABLE IF NOT EXISTS document_revisions(id INTEGER PRIMARY KEY AUTOINCREMENT,
              kb TEXT,doc TEXT,artifact TEXT UNIQUE,parent TEXT,action TEXT,reason TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS backup_records(id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT UNIQUE,state TEXT,scheduled INTEGER DEFAULT 0,created REAL);
            """)
            c.execute('UPDATE schema_version SET version=2 WHERE version<2')
            c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('session', secrets.token_urlsafe(32)))
            c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('preferences', dump({
                'language': '中文', 'length': '适中', 'research_focus': '', 'background': '研究生', 'structure': '先结论'})))
        prefs=json.loads(self.setting('preferences'))
        if 'support_check_level' not in prefs:
            prefs['support_check_level']='advisory'
            self.set_setting('preferences',dump(prefs))
        if seed and not self.setting('seeded'):
            for catalog_name, name in [('catalog_zotero_third_20261028.json', 'Zotero · 论文资料'), ('catalog.json', '示例 · 工具验证')]:
                path = WORKSPACE / 'DocBase' / 'storage' / catalog_name
                if path.is_file():
                    kb = self.create_kb(name)
                    for entry in json.loads(path.read_text(encoding='utf-8')).get('entries', []):
                        if entry.get('active'):
                            self.register_artifact(kb['id'], entry)
                    self.enqueue(kb['id'], 'reindex', {})
            self.set_setting('seeded', '1')

    def setting(self, key):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return row['value'] if row else None

    def set_setting(self, key, value):
        self.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, value))

    def create_kb(self, name):
        key = uid('kb')
        self.execute('INSERT INTO kbs(id,name,collection,created) VALUES (?,?,?,?)', (key, name, 'rd_' + key, time.time()))
        return self.one('SELECT * FROM kbs WHERE id=?', (key,))

    def register_artifact(self, kb, entry):
        artifact = Path(entry['artifact_path']).resolve()
        manifest = json.loads((artifact / 'manifest.json').read_text(encoding='utf-8'))
        path = (artifact / manifest['source_snapshot']).resolve()
        if not path.is_relative_to(artifact) or not path.is_file():
            raise ValueError('来源快照缺失')
        doc = 'd_' + manifest['revision'][:24]
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO docs VALUES (?,?,?,?,?,?)', (doc, entry['name'], manifest['revision'], str(path), path.stat().st_size, time.time()))
            c.execute('INSERT OR REPLACE INTO members(kb,doc,active,docbase_id,artifact,quality,status,pages) VALUES(?,?,1,?,?,?,?,?)',
                (kb, doc, manifest['doc_id'], str(artifact), manifest['status'], 'indexing', manifest.get('page_count')))
            c.execute('UPDATE kbs SET generation=generation+1 WHERE id=?', (kb,))
        return doc

    def enqueue(self, kb, kind, payload):
        key, now = uid('job'), time.time()
        with self.connect() as c:
            if kind == 'reindex':
                prior = c.execute("SELECT id FROM jobs WHERE kb=? AND kind='reindex' AND state='queued' AND cancel=0", (kb,)).fetchone()
                if prior:
                    return prior['id']
            c.execute('INSERT INTO jobs(id,kb,kind,payload,created,updated) VALUES(?,?,?,?,?,?)', (key, kb, kind, dump(payload), now, now))
            c.execute('INSERT INTO events(job,type,payload,created) VALUES(?,?,?,?)', (key, 'queued', '{}', now))
        return key

    def event(self, job, kind, payload=None):
        self.execute('INSERT INTO events(job,type,payload,created) VALUES(?,?,?,?)', (job, kind, dump(payload or {}), time.time()))

    def claim(self):
        now, token = time.time(), uid('lease')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            expired = c.execute("SELECT id FROM jobs WHERE state='running' AND lease<?", (now,)).fetchall()
            for row in expired:
                c.execute("UPDATE jobs SET state='interrupted',error='后台进程中断，可重试',updated=? WHERE id=?", (now, row['id']))
                c.execute("UPDATE messages SET status='failed',content='后台进程中断，请重新发送本轮问题' WHERE job=? AND role='assistant' AND status='pending'", (row['id'],))
            row = c.execute("SELECT * FROM jobs j WHERE state='queued' AND cancel=0 AND NOT EXISTS(SELECT 1 FROM jobs r WHERE r.kb=j.kb AND r.state='running') ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            c.execute("UPDATE jobs SET state='running',token=?,lease=?,attempt=attempt+1,updated=? WHERE id=?", (token, now + 30, now, row['id']))
            value = dict(row)
            value['token'] = token
            return value

    def scope(self, kb):
        return [r['docbase_id'] for r in self.all('SELECT docbase_id FROM members WHERE kb=? AND active=1 AND docbase_id IS NOT NULL', (kb,))]

    def asset(self, path, name=None, mime='application/pdf', doc=None):
        path = Path(path).resolve()
        roots = [self.data, WORKSPACE / 'DocBase' / 'storage' / 'source_store', WORKSPACE / 'doc2md' / 'output']
        if not any(path.is_relative_to(r.resolve()) for r in roots) or not path.is_file():
            raise ValueError('文件不在登记存储范围')
        key = 'asset_' + hashlib.sha256(str(path).encode()).hexdigest()[:28]
        self.execute('INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?)', (key, str(path), name or path.name, mime, doc))
        return key
