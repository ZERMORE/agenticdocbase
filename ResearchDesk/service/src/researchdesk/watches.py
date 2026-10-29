"""Read-only directory discovery. New/changed documents are copied into managed storage."""
import hashlib
import json
from pathlib import Path
import shutil
import time

from .store import uid, dump


def scan_watches(store):
    for watch in store.all('SELECT * FROM watches WHERE enabled=1 AND last_scan<?', (time.time() - 60,)):
        root = Path(watch['path'])
        try:
            if not root.is_dir():
                raise OSError('目录不可达')
            known = json.loads(store.setting('watch:' + watch['id']) or '{}')
            next_known = dict(known)
            for source in root.rglob('*'):
                if not source.is_file() or source.suffix.lower() not in {'.pdf','.docx','.pptx','.doc','.ppt','.png','.jpg','.jpeg'}:
                    continue
                stat = source.stat()
                # Avoid files still being written and directories owned by this service.
                if time.time() - stat.st_mtime < 5 or source.resolve().is_relative_to(store.data):
                    continue
                signature = [stat.st_size, stat.st_mtime_ns]
                previous = known.get(str(source), {})
                if isinstance(previous, dict) and previous.get('signature') == signature:
                    continue
                if stat.st_size > 100 * 1024 * 1024:
                    continue
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                if source.stat().st_size != stat.st_size or source.stat().st_mtime_ns != stat.st_mtime_ns:
                    continue
                doc = 'd_' + digest[:24]
                # Respect a previous explicit removal. A changed file has a different content identity.
                if store.one('SELECT doc FROM members WHERE kb=? AND doc=?', (watch['kb'],doc)):
                    next_known[str(source)] = {'signature':signature,'doc':doc}
                    continue
                target = store.data / 'uploads' / (digest + source.suffix.lower())
                target.parent.mkdir(exist_ok=True)
                if not target.is_file():
                    tmp = target.with_suffix('.' + uid('copy') + '.tmp')
                    shutil.copyfile(source,tmp)
                    if hashlib.sha256(tmp.read_bytes()).hexdigest() != digest:
                        tmp.unlink()
                        continue
                    tmp.replace(target)
                with store.connect() as c:
                    c.execute('INSERT OR IGNORE INTO docs VALUES(?,?,?,?,?,?)',(doc,source.name,digest,str(target),stat.st_size,time.time()))
                    c.execute('INSERT OR IGNORE INTO members(kb,doc) VALUES(?,?)',(watch['kb'],doc))
                    c.execute('UPDATE kbs SET generation=generation+1 WHERE id=?',(watch['kb'],))
                    old_doc = previous.get('doc') if isinstance(previous,dict) else None
                    shared = any(k != str(source) and isinstance(v,dict) and v.get('doc') == old_doc for k,v in next_known.items())
                    if old_doc and old_doc != doc and not shared:
                        c.execute("UPDATE members SET active=0,status='removed' WHERE kb=? AND doc=?",(watch['kb'],old_doc))
                    c.execute("UPDATE jobs SET cancel=1 WHERE kb=? AND kind='chat' AND state IN ('queued','running')",(watch['kb'],))
                store.enqueue(watch['kb'],'parse',{'doc':doc,'offline':True})
                next_known[str(source)] = {'signature':signature,'doc':doc}
            # Missing files never automatically delete knowledge-base memberships.
            store.set_setting('watch:' + watch['id'],dump(next_known))
            store.execute('UPDATE watches SET last_scan=?,error=NULL WHERE id=?',(time.time(),watch['id']))
        except OSError:
            store.execute('UPDATE watches SET last_scan=?,error=? WHERE id=?',(time.time(),'目录暂不可达，未移除任何已入库资料',watch['id']))
