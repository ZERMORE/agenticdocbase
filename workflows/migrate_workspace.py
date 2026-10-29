"""Copy a workspace, clone isolated Conda environments and rebase mutable references."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

source = Path(__file__).resolve().parents[1]
target = Path(sys.argv[1]).resolve()
if source == target or target.is_relative_to(source):
    raise SystemExit('Destination must be outside the original workspace')
target.mkdir(parents=True, exist_ok=True)
logs = target / 'migration'
logs.mkdir(exist_ok=True)
env = os.environ.copy()
env['PYTHONUTF8'] = '1'
env['ComSpec'] = r'C:\Windows\System32\cmd.exe'

def run(args, cwd, log):
    with (logs / log).open('w',encoding='utf-8') as out:
        p = subprocess.run(list(map(str,args)), cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    if p.returncode:
        raise RuntimeError(f'{log}: exit {p.returncode}')

print('Copying all files except Conda prefixes (cloned separately).',flush=True)
with (logs / 'copy.log').open('w',encoding='utf-8') as out:
    p = subprocess.run(['robocopy', str(source), str(target), '/E', '/COPY:DAT', '/DCOPY:DAT', '/R:1','/W:1','/XJ',
                        '/XD','.conda','/NP','/NFL','/NDL'],stdout=out,stderr=subprocess.STDOUT)
if p.returncode >= 8:
    raise RuntimeError('File copy failed')
projects = ['doc2md','DocBase','DocAgent','ResearchDesk/service']

def clone(project):
    root = target / project
    prefix = root / '.conda'
    if not (prefix/'conda-meta').is_dir():
        run([r'D:\Python_Packages\Anaconda3\python.exe','-m','conda','create','--prefix',prefix,
             '--clone',source/project/'.conda','--offline','--copy','-y'],target,project.replace('/','-')+'-clone.log')
    run([prefix/'python.exe','-m','pip','install','--no-deps','--no-build-isolation','-e','.'],root,project.replace('/','-')+'-editable.log')
    print('Environment ready: '+project,flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(clone,projects))

def rebase(text):
    variants = [(str(source),str(target)),(source.as_posix(),target.as_posix()),
                (str(source).replace('\\','\\\\'),str(target).replace('\\','\\\\'))]
    for old,new in sorted(variants,key=lambda p:-len(p[0])):
        text=text.replace(old,new)
    return text

# Immutable manifests/blocks/index metadata must retain bytes: their hashes form provenance IDs.
# Only mutable catalogs, run reports and application rows receive path changes.
changed=[]
for root in [target/'DocBase/storage',target/'ResearchDesk/data',target/'DocAgent/storage']:
    for path in root.rglob('*.json'):
        if any(p in {'source_store','indexes','parsed','backups'} for p in path.relative_to(root).parts):
            continue
        raw=path.read_text(encoding='utf-8-sig')
        new=rebase(raw)
        if raw!=new:
            json.loads(new)
            path.write_text(new,encoding='utf-8')
            changed.append(str(path.relative_to(target)))
db=target/'ResearchDesk/data/app.sqlite'
if db.is_file():
    c=sqlite3.connect(db)
    for table, in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        columns=c.execute(f'PRAGMA table_info("{table}")').fetchall()
        for col in columns:
            name=col[1]
            if col[2].upper()!='TEXT':continue
            rows=c.execute(f'SELECT rowid,"{name}" FROM "{table}" WHERE "{name}" IS NOT NULL').fetchall()
            for rid,value in rows:
                new=rebase(value)
                if new!=value:c.execute(f'UPDATE "{table}" SET "{name}"=? WHERE rowid=?',(new,rid))
    c.execute("UPDATE settings SET value=? WHERE key='session'",(__import__('secrets').token_urlsafe(32),))
    c.execute("UPDATE jobs SET state='interrupted',error='迁移后请手动重试' WHERE state IN ('running','queued')")
    c.commit();c.close()
(target/'ResearchDesk/data/processes.json').unlink(missing_ok=True)

# Keep runtime packages in place to preserve their supported sibling layout; archive unrelated course material.
archive=target/'archive'/'course-materials'
archive.mkdir(parents=True,exist_ok=True)
keep={'doc2md','DocBase','DocAgent','ResearchDesk','workflows','imports','实验方案输出','migration','archive'}
for path in list(target.iterdir()):
    if path.name not in keep:
        path.rename(archive/path.name)

for command in ('start','stop','status','backup'):
    (target/(command+'.cmd')).write_text('@echo off\ncall "%~dp0ResearchDesk\\'+command+'.cmd" %*\n',encoding='ascii')
(target/'setup.cmd').write_text('@echo off\ncall "%~dp0ResearchDesk\\setup.cmd" %*\n',encoding='ascii')
(target/'README.md').write_text('''# AgenticDocBase

本机文献知识库与 Agent 聊天应用。启动 `start.cmd`，访问 http://127.0.0.1:8765。
停止 `stop.cmd`，检查 `status.cmd`，备份 `backup.cmd`。

## 架构与目录

- ResearchDesk：浏览器界面、API、持久任务 Worker 与业务数据库。
- DocAgent：模型工具循环、多轮上下文与引用校验。
- DocBase：检索、索引版本、来源快照；不能删除 source_store 来清理空间。
- doc2md：文档解析及图片释义。
- 实验方案输出：设计与实施方案；imports：原始导入清单。
- workflows：批量导入和迁移工具；migration：迁移日志与验证结果。
- archive/course-materials：全部原有课程资料和杂项，独立于运行代码保留。

四个组件保留独立 Conda 环境，前端依赖在 ResearchDesk/web。
入口脚本统一在根目录，避免进入错误项目执行命令。
细节参见 [应用使用说明](ResearchDesk/README.md)。

数据：ResearchDesk/data；解析输出：doc2md/output 与 ResearchDesk/data/parsed；
索引和不可变来源：DocBase/storage。历史记录中的原路径是溯源信息，不是运行时依赖。
Key仅保存在各项目.env，不进入前端。原目录完整保留，迁移副本独立运行。
旧.venv、测试缓存和历史报告为完整复制保留，不参与当前运行；不自动删除。
''',encoding='utf-8')
report={'source':str(source),'destination':str(target),'environments':projects,'rebased_files':changed,
        'immutable_provenance_preserved':True}
(logs/'migration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('Migration and organization complete.',flush=True)
