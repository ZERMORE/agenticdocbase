from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values
from .common import DocBaseError, digest

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    profile: str = 'hybrid'
    embed_base_url: str = 'https://ai.gitee.com/v1'
    embed_model: str = 'Qwen3-Embedding-8B'
    embed_api_key: str = field(default='', repr=False)
    rerank_base_url: str = 'https://ai.gitee.com/v1'
    rerank_model: str = 'Qwen3-Reranker-8B'
    rerank_api_key: str = field(default='', repr=False)
    sparse_backend: str = 'unconfigured'
    sparse_base_url: str = ''
    sparse_model: str = ''
    sparse_api_key: str = field(default='', repr=False)
    embed_path: str = '/embeddings'
    rerank_path: str = '/rerank'
    sparse_path: str = '/sparse_embeddings'
    model_revision: str = 'user-managed-v1'
    query_instruction: str = 'Given a query, retrieve relevant passages that answer the query'
    tokenizer_path: str = ''
    chunk_size: int = 800
    overlap: int = 100
    recall_k: int = 30
    rerank_candidates: int = 50
    rrf_k: int = 60
    batch_size: int = 16
    timeout: float = 90
    retries: int = 2
    dictionary_path: str = ''
    chunk_strategy: str = 'block-v1'

    @classmethod
    def load(cls, path: Path | None = None, **overrides):
        if path is not None and not Path(path).is_file():
            raise DocBaseError('CONFIG', '指定的 env 文件不存在')
        env = dotenv_values(path or ROOT / '.env')
        defaults = cls()
        data = {}
        for key in cls.__dataclass_fields__:
            value = os.environ.get('DOCBASE_' + key.upper(), env.get('DOCBASE_' + key.upper()))
            if value is None or value == '':
                continue
            try:
                typ = type(getattr(defaults, key))
                data[key] = typ(value) if typ in (int, float) else str(value).strip()
            except ValueError as exc:
                raise DocBaseError('CONFIG', f'DOCBASE_{key.upper()} 格式不正确') from exc
        data.update({k: v for k, v in overrides.items() if v is not None})
        result = cls(**data)
        result.validate()
        return result

    def validate(self):
        if self.chunk_strategy not in {'block-v1', 'context-v2'}:
            raise DocBaseError('CONFIG', 'chunk_strategy 必须为 block-v1 或 context-v2')
        if self.profile not in {'bm25-only', 'hybrid', 'full'}:
            raise DocBaseError('CONFIG', 'profile 必须为 bm25-only、hybrid 或 full')
        if not 0 <= self.overlap < self.chunk_size or self.chunk_size < 100:
            raise DocBaseError('CONFIG', 'chunk_size 至少 100，overlap 必须小于 chunk_size')
        if min(self.recall_k, self.rerank_candidates, self.rrf_k, self.batch_size, self.timeout) <= 0 or not 0 <= self.retries <= 5:
            raise DocBaseError('CONFIG', '检索/批量/超时参数必须为正数，retries 在 0～5 之间')

    def encoding_config(self):
        return {'base_url': self.embed_base_url, 'model': self.embed_model,
                'path': self.embed_path, 'revision': self.model_revision,
                'query_instruction': self.query_instruction, 'normalization': 'l2-float32-v1'}

    def sparse_config(self):
        return {'backend': self.sparse_backend, 'base_url': self.sparse_base_url,
                'model': self.sparse_model, 'path': self.sparse_path, 'revision': self.model_revision}

    def chunk_config(self):
        tokenizer_hash = ''
        if self.tokenizer_path:
            from .common import file_hash
            try:
                tokenizer_hash = file_hash(Path(self.tokenizer_path))
            except OSError as exc:
                raise DocBaseError('TOKENIZER', 'tokenizer JSON 路径不可读') from exc
        return {'version': 'struct-v1' if self.chunk_strategy == 'block-v1' else 'context-v2.1', 'size': self.chunk_size, 'overlap': self.overlap,
                'length_unit': 'tokens' if self.tokenizer_path else 'characters', 'tokenizer_hash': tokenizer_hash}

    def require(self, profile=None):
        profile = profile or self.profile
        if profile in {'hybrid', 'full'}:
            for kind in ('embed', 'rerank'):
                if not getattr(self, kind + '_api_key') or not getattr(self, kind + '_model'):
                    raise DocBaseError('MODEL_NOT_CONFIGURED', f'{kind} 模型或 Key 未配置')
        if profile == 'full' and self.sparse_backend not in {'http', 'local'}:
            raise DocBaseError('SPARSE_NOT_CONFIGURED', 'full 三路模式需要 BGE-M3 稀疏输出；当前可使用 hybrid')
