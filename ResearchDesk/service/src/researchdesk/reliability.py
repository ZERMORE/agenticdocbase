"""Failure taxonomy, disk guard and idle-only scheduled backups."""
import errno
import json
from pathlib import Path
import re
import shutil
import time


def failure(exc):
    text = str(exc)
    text = re.sub(r'(?i)(Bearer\s+|(?:api[_-]?key|authorization)\s*[:=]\s*)[^\s,;]+', r'\1[REDACTED]', text)
    category, advice = 'local', '查看任务诊断；核对输入文件与依赖后手动重试。'
    mapping = [
        ('configuration', ('CONFIG:',), '检查网页模型配置中的参数；保存后重新提问。'),
        ('quota', ('MODEL_QUOTA', '余额', '计费资源', 'HTTP 402'), '检查账户额度后再重试，不会自动重放问答。'),
        ('authentication', ('MODEL_AUTH', '401', '403'), '检查服务端模型配置和库级外部处理授权。'),
        ('rate_limit', ('MODEL_BUSY', '429'), '供应商限流，请稍后手动重试。'),
        ('timeout', ('TIMEOUT', '超时', '时限'), '检查任务阶段；问答需手动重新发送，避免重复计费。'),
        ('network', ('MODEL_NETWORK', '网络'), '检查网络后手动重试；未知计费状态不自动重放。'),
        ('version_conflict', ('VERSION_CONFLICT',), '刷新文档并基于当前版本重新操作。'),
        ('dependency', ('依赖', 'LibreOffice', 'Tesseract'), '安装相应可选依赖或选择支持的解析方式。'),
        ('input', ('输入', '格式', '加密', 'QUESTION'), '检查输入格式、加密和页码。'),
        ('disk', ('DISK_LOW', '磁盘不足'), '释放磁盘空间后手动重试；原始资料保持不变。')]
    for name, terms, hint in mapping:
        if any(term in text for term in terms):
            category, advice = name, hint
            break
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        category, advice = 'disk', '磁盘空间不足，释放空间后再重试。'
    message = text[:350] if isinstance(exc, RuntimeError) else '本地任务处理失败，请检查诊断与文件。'
    return {'category': category, 'message': message, 'advice': advice, 'automatic_retry': False}


def ensure_space(path, required=0):
    # Fixed headroom plus estimated expanded input; never deletes user files.
    if shutil.disk_usage(path).free < 256 * 1024**2 + required:
        raise RuntimeError('DISK_LOW: 磁盘可用空间不足，请释放空间后重试')


def schedule_backup(s):
    config = json.loads(s.setting('backup_policy') or '{"enabled":false,"interval_hours":24,"keep":7}')
    if not config['enabled'] or s.one("SELECT 1 FROM jobs WHERE state IN ('queued','running')"):
        return
    last = float(s.setting('backup_attempt') or 0)
    success = float(s.setting('backup_success') or 0)
    now = time.time()
    if now - success < config['interval_hours'] * 3600 or now - last < 3600:
        return
    s.set_setting('backup_attempt', str(now))
    try:
        from .manage import backup
        ensure_space(s.data)
        last_beat = [0]
        def heartbeat():
            if time.time() - last_beat[0] > 3:
                s.set_setting('worker_heartbeat', str(time.time()))
                last_beat[0] = time.time()
        path = backup(s, check=heartbeat)
        s.set_setting('backup_success', str(time.time()))
        s.set_setting('backup_error', '')
        # Only registered successful scheduled backups can be pruned. Manual backups untouched.
        rows = s.all("SELECT id,path FROM backup_records WHERE scheduled=1 AND state='succeeded' ORDER BY created DESC")
        for row in rows[config['keep']-1:]:
            resolved = Path(row['path']).resolve()
            parent = (s.data / 'backups').resolve()
            if resolved.parent == parent and resolved != parent and resolved.is_dir():
                shutil.rmtree(resolved)
                s.execute("UPDATE backup_records SET state='pruned' WHERE id=?", (row['id'],))
        s.execute('UPDATE backup_records SET scheduled=1 WHERE path=?', (str(path),))
    except Exception as exc:
        s.set_setting('backup_error', failure(exc)['message'])
