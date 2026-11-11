import json
import os
from pathlib import Path
import subprocess
import time


def terminate(process):
    if process.poll() is None:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            process.kill()


def invoke(python, module, args, cwd, folder, check=lambda: None, timeout=600, event_file=None, on_event=None, env_overrides=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env.pop('PYTHONPATH', None)
    # Secrets remain in each tool's own .env, not in the service request payload.
    env = {k: v for k, v in env.items() if not k.startswith(('DOCAGENT_', 'DOCBASE_', 'DOC2MD_'))}
    env.update(env_overrides or {})
    out_path, err_path = folder / 'stdout.log', folder / 'stderr.log'
    with out_path.open('w', encoding='utf-8') as out, err_path.open('w', encoding='utf-8') as err:
        with subprocess.Popen([str(python), '-X', 'utf8', '-m', module, *map(str, args)], cwd=cwd, env=env, shell=False,
                stdout=out, stderr=err, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as p:
            started, lines = time.monotonic(), 0
            try:
                while p.poll() is None:
                    check()
                    if time.monotonic() - started > timeout:
                        raise RuntimeError('任务超过执行时限')
                    if event_file and Path(event_file).is_file():
                        events = Path(event_file).read_text(encoding='utf-8', errors='replace').splitlines()
                        for line in events[lines:]:
                            try:
                                if on_event:
                                    on_event(json.loads(line))
                                lines += 1
                            except json.JSONDecodeError:
                                break
                    time.sleep(.15)
                check()
                # Drain the tail after process exit as well; final verified events must not vanish.
                if event_file and Path(event_file).is_file():
                    for line in Path(event_file).read_text(encoding='utf-8',errors='replace').splitlines()[lines:]:
                        try:
                            if on_event:on_event(json.loads(line))
                        except json.JSONDecodeError:break
            except BaseException:
                terminate(p)
                p.wait(timeout=10)
                raise
    raw = out_path.read_text(encoding='utf-8', errors='replace')
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    if p.returncode not in (0, 2):
        raise RuntimeError(str(data.get('error') or '工具执行失败，请查看任务诊断'))
    return data
