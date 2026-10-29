from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from dotenv import dotenv_values
from .common import AgentError

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    base_url: str = 'https://api.deepseek.com'
    model: str = 'deepseek-flash'
    api_key: str = field(default='', repr=False)
    thinking: str = 'disabled'
    docbase_root: str = '../DocBase'
    collection: str = 'zotero_third_20261028'
    retrieval_profile: str = 'bm25-only'
    max_model_calls: int = 6
    max_tool_calls: int = 10
    max_searches: int = 3
    request_timeout_seconds: float = 60
    total_timeout_seconds: float = 180
    max_evidence_chars: int = 24000
    max_output_tokens: int = 2500

    @classmethod
    def load(cls, path=None):
        if path and not Path(path).is_file():
            raise AgentError('CONFIG', '指定的配置文件不存在')
        values, defaults, data = dotenv_values(path or ROOT / '.env'), cls(), {}
        for key in cls.__dataclass_fields__:
            name = 'DOCAGENT_' + key.upper()
            value = os.environ.get(name, values.get(name))
            if value is not None and value != '':
                try:
                    data[key] = type(getattr(defaults, key))(value)
                except (ValueError, TypeError) as exc:
                    raise AgentError('CONFIG', f'{name} 格式不正确') from exc
        result = cls(**data)
        result.validate()
        return result

    def validate(self):
        parsed = urlparse(self.base_url)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AgentError('CONFIG', '模型 base_url 必须是无凭据的 HTTPS 地址')
        if not self.model or self.thinking != 'disabled':
            raise AgentError('CONFIG', '初版仅支持 thinking=disabled')
        if self.retrieval_profile not in {'bm25-only', 'hybrid', 'full'}:
            raise AgentError('CONFIG', '检索 profile 不正确')
        checked_name(self.collection)
        for key in ('max_model_calls', 'max_tool_calls', 'max_searches', 'max_evidence_chars', 'max_output_tokens',
                    'request_timeout_seconds', 'total_timeout_seconds'):
            value = getattr(self, key)
            if not 0 < value < 1000000:
                raise AgentError('CONFIG', f'{key} 超出有效范围')

    @property
    def base_root(self):
        return (ROOT / self.docbase_root).resolve()


def checked_name(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise AgentError('CONFIG', '集合/版本ID格式不正确')
    return value
