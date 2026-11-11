import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile

from .store import Store, ROOT, WORKSPACE, atomic


def backup(store, check=lambda: None):
    directory = store.data / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + __import__('uuid').uuid4().hex[:8])
    final = directory
    directory = directory.with_name('.incomplete-' + directory.name)
    directory.mkdir(parents=True)
    check()
    with store.connect() as source:
        target = sqlite3.connect(directory / 'app.sqlite')
        source.backup(target)
        target.close()
    # Tool snapshots are immutable. Include only registered documents/artifacts/indexes, never .env.
    paths = set()
    for row in store.all('SELECT path FROM docs UNION SELECT path FROM assets'):
        paths.add(Path(row['path']))
    for row in store.all('SELECT artifact FROM members WHERE artifact IS NOT NULL UNION SELECT artifact FROM document_revisions UNION SELECT parent AS artifact FROM document_revisions'):
        paths.update(p for p in Path(row['artifact']).rglob('*') if p.is_file())
    for row in store.all('SELECT collection FROM kbs'):
        root = store.docbase_storage / 'indexes' / row['collection']
        if root.exists():
            paths.update(p for p in root.rglob('*') if p.is_file() and not any(part.startswith('.build-') for part in p.parts))
    # All source bundles are content-addressed and shared; keeping them protects historical references.
    bundles = store.docbase_storage / 'source_store'
    if bundles.exists():
        paths.update(p for p in bundles.rglob('*') if p.is_file())
    for folder in ('uploads', 'catalogs', 'runs', 'secrets'):
        paths.update(p for p in (store.data / folder).rglob('*') if p.is_file())
    manifest = []
    with zipfile.ZipFile(directory / 'files.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for i, path in enumerate(sorted(paths)):
            check()
            if path.name == '.env' or not path.is_file():
                continue
            entry = f'files/{i:06d}/{path.name}'
            archive.write(path, entry)
            manifest.append({'original': str(path.resolve()), 'entry': entry,
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    atomic(directory / 'manifest.json', {'schema_version': 1, 'data_root': str(store.data),
                                       'tool_storage': str(store.docbase_storage), 'files': manifest,
                                       'database_sha256': hashlib.sha256((directory / 'app.sqlite').read_bytes()).hexdigest()})
    check()
    directory.rename(final)
    directory = final
    store.execute('INSERT INTO backup_records(path,state,created) VALUES(?,?,?)', (str(directory), 'succeeded', time.time()))
    store.set_setting('backup_success', str(time.time()))
    return directory


def restore(backup_path, destination):
    backup_path, destination = Path(backup_path).resolve(), Path(destination).resolve()
    if destination.exists():
        raise RuntimeError('恢复目录必须尚不存在；不会覆盖现有数据')
    manifest = json.loads((backup_path / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('database_sha256') and hashlib.sha256((backup_path / 'app.sqlite').read_bytes()).hexdigest() != manifest['database_sha256']:
        raise RuntimeError('备份数据库校验失败')
    destination.mkdir(parents=True)
    shutil.copyfile(backup_path / 'app.sqlite', destination / 'app.sqlite')
    mappings = {}
    old_data = Path(manifest['data_root'])
    with zipfile.ZipFile(backup_path / 'files.zip') as archive:
        for i, row in enumerate(manifest['files']):
            raw = archive.read(row['entry'])
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise RuntimeError('备份文件校验失败')
            original = Path(row['original'])
            tool_storage = Path(manifest.get('tool_storage', WORKSPACE / 'DocBase' / 'storage'))
            if original.is_relative_to(tool_storage):
                path = destination / 'docbase_storage' / original.relative_to(tool_storage)
            elif original.is_relative_to(old_data):
                path = destination / original.relative_to(old_data)
            elif original.is_relative_to(WORKSPACE / 'doc2md' / 'output'):
                path = destination / 'restored_parsed' / original.relative_to(WORKSPACE / 'doc2md' / 'output')
            else:
                path = destination / 'external' / str(i) / original.name
            path = path.resolve()
            if not path.is_relative_to(destination):
                raise RuntimeError('备份文件含有越界恢复路径')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            mappings[str(original)] = str(path)
    # Rewrite artifact directories and raw result paths in the application DB without touching originals.
    c = sqlite3.connect(destination / 'app.sqlite')
    for table in ('docs', 'assets'):
        for old, new in mappings.items():
            c.execute(f'UPDATE {table} SET path=? WHERE path=?', (new, old))
    directory_map = {str(old_data): str(destination), str(tool_storage): str(destination / 'docbase_storage'),
                     str(WORKSPACE / 'doc2md' / 'output'): str(destination / 'restored_parsed')}
    for old, new in mappings.items():
        directory_map[str(Path(old).parent)] = str(Path(new).parent)
    for doc, kb, artifact in c.execute('SELECT doc,kb,artifact FROM members WHERE artifact IS NOT NULL').fetchall():
        mapped = directory_map.get(artifact)
        if mapped:
            c.execute('UPDATE members SET artifact=? WHERE kb=? AND doc=?',(mapped,kb,doc))
    def rewrite(value):
        if isinstance(value,str):
            for old,new in sorted(directory_map.items(),key=lambda x:-len(x[0])):
                if value == old or value.startswith(old + os.sep):
                    return new + value[len(old):]
            return mappings.get(value,value)
        if isinstance(value,list):return [rewrite(x) for x in value]
        if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
        return value
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='document_revisions'").fetchone():
        for rid, artifact, parent in c.execute('SELECT id,artifact,parent FROM document_revisions').fetchall():
            c.execute('UPDATE document_revisions SET artifact=?,parent=? WHERE id=?', (rewrite(artifact),rewrite(parent),rid))
    for jid, payload in c.execute('SELECT id,payload FROM jobs').fetchall():
        c.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(rewrite(json.loads(payload)),ensure_ascii=False),jid))
    for mid,raw in c.execute('SELECT id,result FROM messages WHERE result IS NOT NULL').fetchall():
        c.execute('UPDATE messages SET result=? WHERE id=?',(json.dumps(rewrite(json.loads(raw)),ensure_ascii=False),mid))
    for kid,collection,version in c.execute('SELECT id,collection,index_version FROM kbs').fetchall():
        if version:
            atomic(destination/'docbase_storage'/'collections'/collection/'CURRENT.json',{'schema_version':'1','index_version':version})
    c.execute("UPDATE settings SET value=? WHERE key='session'",(__import__('secrets').token_urlsafe(32),))
    c.execute('UPDATE watches SET enabled=0')
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='zotero_sources'").fetchone():c.execute('UPDATE zotero_sources SET enabled=0')
    c.execute("UPDATE jobs SET state='interrupted',error='从备份恢复，请手动重试' WHERE state IN ('queued','running')")
    c.commit()
    c.close()
    atomic(destination / 'restore-mappings.json', mappings)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'worker', 'start', 'stop', 'status', 'backup', 'restore', 'import-history'])
    parser.add_argument('--backup-path')
    parser.add_argument('--destination')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--reuse', action='store_true', help='Reuse an active instance on the requested port')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535')
    address = f'http://127.0.0.1:{args.port}'
    s = Store()
    s.init()
    pidfile = s.data / 'processes.json'
    if args.command == 'init':
        print('Research Desk initialized')
    elif args.command == 'import-history':
        count = 0
        for file in (WORKSPACE / 'DocAgent' / 'storage' / 'runs').glob('*/answer.json'):
            value = json.loads(file.read_text(encoding='utf-8'))
            if value.get('status') != 'answered' or value.get('model_returned') != 'deepseek-flash':
                continue
            name = 'Zotero%' if value.get('collection') == 'zotero_third_20261028' else '示例%'
            kb = s.one('SELECT * FROM kbs WHERE name LIKE ? AND deleted=0', (name,))
            key = 'import_' + value['run_id']
            if not kb or s.one('SELECT id FROM conversations WHERE id=?', (key,)):
                continue
            for citation in value.get('citations', []):
                for source in citation['sources']:
                    path = source.get('snapshot_path_absolute')
                    if path and Path(path).is_file():
                        source['asset_id'] = s.asset(path)
            timestamp = file.stat().st_mtime
            with s.connect() as c:
                c.execute('INSERT INTO conversations(id,kb,title,created,updated) VALUES(?,?,?,?,?)',
                          (key, kb['id'], value['question'][:80], timestamp, timestamp))
                c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',
                          (key+'_q',key,'user',value['question'],'sent',None,None,timestamp))
                c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',
                          (key+'_a',key,'assistant',value['answer'],'answered',None,json.dumps(value,ensure_ascii=False),timestamp+.001))
            count += 1
        print('Imported real CLI conversations:', count)
    elif args.command == 'worker':
        from .worker import Worker
        Worker(s).run()
    elif args.command == 'backup':
        print(backup(s))
    elif args.command == 'restore':
        if not args.backup_path or not args.destination:
            raise SystemExit('需要 --backup-path 和 --destination')
        print(restore(args.backup_path, args.destination))
    elif args.command == 'start':
        missing = [
            name for name in ('doc2md', 'DocBase', 'DocAgent')
            if not (WORKSPACE / name / '.conda' / 'python.exe').is_file()
        ]
        if missing:
            raise SystemExit('缺少组件环境：' + '、'.join(missing) + '。请在项目根目录运行 setup.cmd')
        if not (ROOT / 'web' / 'dist' / 'index.html').is_file():
            raise SystemExit('请先运行 setup.cmd 构建网页')
        if pidfile.is_file():
            import psutil
            saved = json.loads(pidfile.read_text(encoding='utf-8'))
            active = {}
            for item in saved:
                try:
                    process = psutil.Process(item['pid'])
                    command = process.cmdline()
                    if (Path(process.exe()).resolve() == Path(sys.executable).resolve()
                            and any(part.startswith('researchdesk') for part in command)):
                        active[item['name']] = command
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if active:
                api_command = active.get('api', [])
                if args.reuse and 'worker' in active and api_command[-2:] == ['--port', str(args.port)]:
                    print('研知已就绪：' + address)
                    return
                raise SystemExit('本项目已启动，请先停止后台服务。')
        # Refuse to start a second application on an occupied port.
        import socket
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', args.port))
            except OSError:
                raise SystemExit(f'{args.port} 端口已被占用；未启动任何新进程')
        logs = s.data / 'logs'
        logs.mkdir(exist_ok=True)
        # Rotate only our known API/worker logs, keeping three prior files.
        for name in ('api','worker'):
            path = (logs / (name + '.log')).resolve()
            if path.parent != logs.resolve(): raise RuntimeError('日志路径越界')
            if path.is_file() and path.stat().st_size > 10 * 1024**2:
                for number in (2,1):
                    prior = path.with_name(path.name + '.' + str(number))
                    if prior.is_file(): prior.replace(path.with_name(path.name + '.' + str(number+1)))
                path.replace(path.with_name(path.name + '.1'))
        launched = []
        for name, command in [('api', ['-m', 'uvicorn', 'researchdesk.server:app', '--host', '127.0.0.1', '--port', str(args.port)]),
                              ('worker', ['-m', 'researchdesk', 'worker'])]:
            with (logs / (name + '.log')).open('a', encoding='utf-8') as log:
                p = subprocess.Popen([sys.executable, '-X', 'utf8', *command], cwd=ROOT / 'service', stdout=log, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
                launched.append({'pid': p.pid, 'name': name})
        atomic(pidfile, launched)
        # Report readiness rather than merely successful process creation.
        import httpx
        import psutil
        ready = False
        for _ in range(40):
            try:
                with httpx.Client(trust_env=False, timeout=1) as client:
                    client.get(address + '/api/bootstrap').raise_for_status()
                    health = client.get(address + '/api/health').json()
                    if health.get('worker_alive'):
                        ready = True
                        break
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(.25)
        if not ready:
            for item in launched:
                try:
                    process = psutil.Process(item['pid'])
                    for child in process.children(recursive=True): child.terminate()
                    process.terminate()
                except psutil.NoSuchProcess: pass
            pidfile.unlink(missing_ok=True)
            raise SystemExit('启动未就绪，已回收本次进程；请检查data/logs')
        print('研知已就绪：' + address)
        print('日志：' + str(logs))
    elif args.command == 'stop':
        import psutil
        if pidfile.is_file():
            for item in json.loads(pidfile.read_text(encoding='utf-8')):
                try:
                    p = psutil.Process(item['pid'])
                    command = ' '.join(p.cmdline())
                    if 'researchdesk' in command and Path(p.exe()).resolve() == Path(sys.executable).resolve():
                        for child in p.children(recursive=True):
                            child.terminate()
                        p.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            pidfile.unlink()
        print('已停止本项目进程；未完成任务会在下次启动标记中断。')
    else:
        print(json.dumps({'processes': json.loads(pidfile.read_text()) if pidfile.exists() else [],
            'worker_heartbeat': s.setting('worker_heartbeat'), 'data': str(s.data)}, ensure_ascii=False, indent=2))
