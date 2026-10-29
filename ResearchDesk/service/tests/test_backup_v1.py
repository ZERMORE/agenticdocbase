from pathlib import Path
from unittest.mock import patch
import json
import pytest
from researchdesk.store import Store
from researchdesk.manage import backup,restore
from researchdesk.reliability import schedule_backup

def test_backup_database_hash_and_tamper_rejection(tmp_path):
    s=Store(tmp_path/'data');s.init(seed=False);(s.data/'docbase_storage').mkdir()
    folder=backup(s)
    assert json.loads((folder/'manifest.json').read_text())['database_sha256']
    with (folder/'app.sqlite').open('ab') as f:f.write(b'changed')
    with pytest.raises(RuntimeError,match='数据库校验'):restore(folder,tmp_path/'restore')
    assert not (tmp_path/'restore').exists()

def test_scheduled_retention_never_prunes_manual_backup(tmp_path):
    s=Store(tmp_path/'data');s.init(seed=False);(s.data/'docbase_storage').mkdir()
    manual=backup(s)
    old=backup(s);s.execute('UPDATE backup_records SET scheduled=1 WHERE path=?',(str(old),))
    s.set_setting('backup_policy',json.dumps({'enabled':True,'keep':1,'interval_hours':1}))
    s.set_setting('backup_success','0');s.set_setting('backup_attempt','0')
    schedule_backup(s)
    assert manual.exists() and not old.exists()
    assert s.one("SELECT COUNT(*) n FROM backup_records WHERE scheduled=1 AND state='succeeded'")['n']==1

def test_backup_preserves_prior_revision_files(tmp_path):
    s=Store(tmp_path/'data');s.init(seed=False);(s.data/'docbase_storage').mkdir()
    base=s.data/'parsed'/'old';base.mkdir(parents=True);(base/'document.md').write_text('old')
    new=s.data/'parsed'/'new';new.mkdir();(new/'document.md').write_text('new')
    s.execute('INSERT INTO document_revisions(kb,doc,artifact,parent,action,reason,created) VALUES(?,?,?,?,?,?,?)',
              ('k','d',str(new),str(base),'edit','test',1))
    folder=backup(s);target=restore(folder,tmp_path/'restored')
    restored=Store(target);row=restored.one('SELECT artifact,parent FROM document_revisions')
    assert Path(row['artifact']).is_relative_to(target) and Path(row['parent']).is_relative_to(target)
    assert (Path(row['parent'])/'document.md').read_text()=='old'
