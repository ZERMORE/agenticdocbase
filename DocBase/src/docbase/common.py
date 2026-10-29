from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path


class DocBaseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def digest(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for piece in iter(lambda: f.read(1024 * 1024), b''):
            h.update(piece)
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if not relative or candidate == root or not candidate.is_relative_to(root):
        raise DocBaseError('INVALID_SOURCE_PATH', '来源路径不在登记产物目录内部')
    return candidate


def name_checked(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise DocBaseError('INVALID_NAME', '集合/版本 ID 仅允许字母、数字、下划线和短横线')
    return value


def remove_owned(path: Path, root: Path):
    resolved = path.resolve()
    if resolved == root.resolve() or not resolved.is_relative_to(root.resolve()):
        raise DocBaseError('INVALID_DELETE', '拒绝删除工作目录之外的内容')
    if path.exists():
        shutil.rmtree(path)


@contextmanager
def writer_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise DocBaseError('LOCKED', f'构建锁已存在：{path}；请确认原进程已退出后再手动移除') from exc
    try:
        os.write(fd, f'pid={os.getpid()}'.encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)
