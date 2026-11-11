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
    stream_mode: str = 'off'
    support_base_url: str = ''
    support_model: str = ''
    support_request_timeout_seconds: float = 60
    support_max_output_tokens: int = 2500
    support_max_model_calls: int = 6
    support_api_key: str = field(default='',repr=False)
    docbase_root: str = '../DocBase'
    collection: str = 'default'
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
                    data[key] = cls.__dataclass_fields__[key].type(value)
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
        if self.stream_mode not in {'auto','off'}:raise AgentError('CONFIG','流模式无效')
        if self.support_base_url:
            support=urlparse(self.support_base_url)
            if support.scheme!='https' or not support.netloc or support.username or support.password or support.query or support.fragment:raise AgentError('CONFIG','支持模型地址需为无凭据HTTPS')
        if self.support_model and not self.support_api_key:raise AgentError('MODEL_NOT_CONFIGURED','单独的支持模型需要自己的Key；也可选择沿用Agent模型')
        checked_name(self.collection)
        for key in ('max_model_calls', 'max_tool_calls', 'max_searches', 'max_evidence_chars', 'max_output_tokens',
                    'request_timeout_seconds', 'total_timeout_seconds','support_request_timeout_seconds','support_max_output_tokens','support_max_model_calls'):
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
