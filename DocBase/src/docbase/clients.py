from __future__ import annotations

import math
import time
from pathlib import Path
import httpx
import numpy as np
from .common import DocBaseError, digest, read_json, write_json
from .config import Settings


class ModelClient:
    def __init__(self, settings: Settings, cache_dir: Path, transport=None, sleeper=time.sleep, progress=None):
        self.cfg, self.cache, self.transport = settings, Path(cache_dir), transport
        self.sleeper, self.progress = sleeper, progress or (lambda _: None)
        self.stats = {'requests': 0, 'cache_hits': 0, 'usage': [], 'elapsed_seconds': 0.0}
        self.local_sparse = None

    def post(self, capability, body):
        prefix = {'embedding': 'embed', 'reranker': 'rerank', 'sparse': 'sparse'}[capability]
        url = getattr(self.cfg, prefix + '_base_url').rstrip('/')
        path = getattr(self.cfg, prefix + '_path')
        if not url.startswith('https://') and not url.startswith('http://'):
            raise DocBaseError('MODEL_CONFIG', f'{capability} base_url 未正确配置')
        if not url.endswith(path):
            url += '/' + path.lstrip('/')
        key = getattr(self.cfg, prefix + '_api_key')
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        started = time.monotonic()
        try:
            with httpx.Client(timeout=self.cfg.timeout, transport=self.transport, follow_redirects=False) as client:
                for attempt in range(self.cfg.retries + 1):
                    self.stats['requests'] += 1
                    try:
                        response = client.post(url, headers=headers, json=body)
                    except (httpx.TimeoutException, httpx.NetworkError):
                        if attempt < self.cfg.retries:
                            self.sleeper(min(2 ** attempt, 8))
                            continue
                        raise DocBaseError('MODEL_NETWORK', f'{capability} 网络失败或超时')
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < self.cfg.retries:
                            self.sleeper(min(2 ** attempt, 8))
                            continue
                    if response.status_code != 200:
                        # Recognize a small set of provider failures without echoing raw bodies.
                        if response.status_code in {400, 402, 429}:
                            try:
                                error = response.json().get('error', {})
                                message = error.get('message', '') if isinstance(error, dict) else str(error)
                            except (ValueError, AttributeError):
                                message = ''
                            if any(term in message.lower() for term in ('计费资源', '余额不足', 'insufficient balance', 'insufficient_quota')):
                                raise DocBaseError('MODEL_QUOTA', f'{capability} 账户没有可用计费资源/额度；请在平台确认免费资格或可用资源')
                        # Never include HTTP response bodies, headers or credentials.
                        raise DocBaseError('MODEL_HTTP', f'{capability} HTTP {response.status_code}；检查权限、额度、模型和接口路径')
                    try:
                        value = response.json()
                        if isinstance(value, dict) and isinstance(value.get('usage'), dict):
                            self.stats['usage'].append(value['usage'])
                        return value
                    except ValueError as exc:
                        raise DocBaseError('MODEL_RESPONSE', f'{capability} 未返回 JSON') from exc
        except httpx.HTTPError as exc:
            raise DocBaseError('MODEL_NETWORK', f'{capability} HTTP 连接异常') from exc
        finally:
            self.stats['elapsed_seconds'] += round(time.monotonic() - started, 3)

    def embeddings(self, texts, role='document'):
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        encoded = [f'Instruct: {self.cfg.query_instruction}\nQuery: {text}' if role == 'query' and self.cfg.query_instruction else text for text in texts]
        keys = [digest([self.cfg.encoding_config(), role, text]) for text in encoded]
        vectors, missing = [None] * len(texts), []
        for i, key in enumerate(keys):
            path = self.cache / 'dense' / f'{key}.json'
            try:
                value = read_json(path)
                vector = self.validate_vectors([value])[0]
                vectors[i] = vector
                self.stats['cache_hits'] += 1
            except (OSError, ValueError, TypeError, DocBaseError):
                missing.append(i)
        for start in range(0, len(missing), self.cfg.batch_size):
            indexes = missing[start:start + self.cfg.batch_size]
            self.progress(f'Embedding 批次 {start // self.cfg.batch_size + 1}/{math.ceil(len(missing) / self.cfg.batch_size)}，文本 {len(indexes)} 条')
            response = self.post('embedding', {'model': self.cfg.embed_model, 'input': [encoded[i] for i in indexes]})
            try:
                items = response['data']
                if len(items) != len(indexes) or {item['index'] for item in items} != set(range(len(indexes))):
                    raise ValueError()
                ordered = sorted(items, key=lambda item: item['index'])
                batch = self.validate_vectors([item['embedding'] for item in ordered])
            except (KeyError, TypeError, ValueError) as exc:
                raise DocBaseError('MODEL_RESPONSE', 'Embedding 返回数目/index/向量格式错误') from exc
            for target, vector in zip(indexes, batch):
                vectors[target] = vector
                write_json(self.cache / 'dense' / f'{keys[target]}.json', vector.tolist())
        return self.validate_vectors(vectors)

    @staticmethod
    def validate_vectors(vectors):
        try:
            array = np.asarray(vectors, dtype=np.float32)
            if array.ndim != 2 or array.shape[1] == 0 or not np.isfinite(array).all():
                raise ValueError()
            norms = np.linalg.norm(array, axis=1, keepdims=True)
            if (norms < 1e-12).any():
                raise ValueError()
            return array / norms
        except (ValueError, TypeError) as exc:
            raise DocBaseError('VECTOR_INVALID', '向量维度不一致、为空、非有限或为零') from exc

    def rerank(self, question, texts):
        if not texts:
            return []
        result = self.post('reranker', {'model': self.cfg.rerank_model, 'query': question,
                                       'documents': texts, 'top_n': len(texts), 'return_documents': False})
        try:
            items = result['results']
            if len(items) != len(texts) or {item['index'] for item in items} != set(range(len(texts))):
                raise ValueError()
            values = [(item['index'], float(item['relevance_score'])) for item in items]
            if not all(math.isfinite(score) for _, score in values):
                raise ValueError()
            return sorted(values, key=lambda item: (-item[1], item[0]))
        except (KeyError, TypeError, ValueError) as exc:
            raise DocBaseError('MODEL_RESPONSE', 'Reranker 返回候选数量、index 或 relevance_score 不正确') from exc

    def sparse(self, texts):
        if self.cfg.sparse_backend == 'local':
            try:
                if self.local_sparse is None:
                    from FlagEmbedding import BGEM3FlagModel
                    self.local_sparse = BGEM3FlagModel(self.cfg.sparse_model or 'BAAI/bge-m3', use_fp16=False)
                raw = self.local_sparse.encode(texts, return_dense=False, return_sparse=True, return_colbert_vecs=False)['lexical_weights']
            except ImportError as exc:
                raise DocBaseError('SPARSE_DEPENDENCY', '本地 sparse 需安装 .[sparse] 和 BGE-M3 权重；不会自动替换为 dense') from exc
        elif self.cfg.sparse_backend == 'http':
            response = self.post('sparse', {'model': self.cfg.sparse_model, 'input': texts, 'return_sparse': True})
            raw = response.get('lexical_weights') if isinstance(response, dict) else None
        else:
            raise DocBaseError('SPARSE_NOT_CONFIGURED', '未配置真实 sparse 输出')
        return self.validate_sparse(raw, len(texts))

    @staticmethod
    def validate_sparse(raw, count):
        try:
            if not isinstance(raw, list) or len(raw) != count:
                raise ValueError()
            result = []
            for weights in raw:
                if not isinstance(weights, dict):
                    raise ValueError()
                normalized = {str(token): float(weight) for token, weight in weights.items()}
                if any(not token.isdigit() or not math.isfinite(w) or w < 0 for token, w in normalized.items()):
                    raise ValueError()
                result.append(normalized)
            return result
        except (TypeError, ValueError) as exc:
            raise DocBaseError('SPARSE_RESPONSE', '稀疏结果须为 lexical_weights 的 token ID → 非负有限权重列表') from exc
