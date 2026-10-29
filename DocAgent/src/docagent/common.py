import hashlib
import json
import os
from pathlib import Path
import uuid


class AgentError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def sanitize(value, secret):
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, '[REDACTED]')
    if isinstance(value, dict):
        return {sanitize(k, secret): sanitize(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(x, secret) for x in value]
    return value
