from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

from .clients import ModelClient
from .common import DocBaseError, read_json
from .config import ROOT, Settings


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='DocBase 图文知识库与混合检索')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('doctor', 'probe', 'scan', 'select', 'build', 'query', 'get', 'context', 'test', 'status', 'overview'):
        p = sub.add_parser(command)
        p.add_argument('--env-file', type=Path)
        p.add_argument('--storage', type=Path, default=ROOT / 'storage')
        p.add_argument('--collection', default='default')
        if command in ('build', 'query', 'test'):
            p.add_argument('--profile', choices=['bm25-only', 'hybrid', 'full'])
        if command in ('query', 'get', 'context', 'test', 'status', 'overview'):
            p.add_argument('--index-version')
        if command in ('query', 'test'):
            p.add_argument('--top-k', type=int, default=5)
        if command == 'probe':
            p.add_argument('--capability', required=True, choices=['embedding', 'reranker', 'sparse'])
        if command == 'scan':
            p.add_argument('--input', type=Path, required=True)
            p.add_argument('--catalog', type=Path)
        if command == 'select':
            p.add_argument('--artifact', type=Path, required=True)
            p.add_argument('--catalog', type=Path)
        if command == 'build':
            p.add_argument('--allow-empty', action='store_true')
            p.add_argument('--catalog', type=Path)
            p.add_argument('--incremental', action='store_true')
            p.add_argument('--quality', choices=['usable', 'strict'], default='usable')
        if command == 'overview':
            p.add_argument('--allowed-doc-ids-file', type=Path)
            p.add_argument('--category')
            p.add_argument('--budget', type=int, default=24000)
        if command == 'query':
            p.add_argument('--allowed-doc-ids-file', type=Path)
            p.add_argument('--question', required=True)
            p.add_argument('--prefer-body', action='store_true')
            p.add_argument('--category')
            p.add_argument('--format', choices=['json'], default='json')
        if command in ('get', 'context'):
            p.add_argument('--chunk-id', required=True)
        if command == 'context':
            p.add_argument('--before', type=int, default=1)
            p.add_argument('--after', type=int, default=1)
        if command == 'test':
            p.add_argument('--dataset', type=Path, required=True)
    args = parser.parse_args(argv)
    cfg = None
    try:
        cfg = Settings.load(args.env_file)
        storage = args.storage.resolve()
        profile = getattr(args, 'profile', None) or cfg.profile
        if args.command == 'doctor':
            packages = {}
            for name in ('numpy', 'httpx', 'python-dotenv', 'jieba', 'rank-bm25'):
                try:
                    packages[name] = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:
                    packages[name] = 'missing'
            emit({'status': 'ok' if 'missing' not in packages.values() else 'failed',
                  'python': sys.version.split()[0], 'executable': sys.executable, 'project': str(ROOT),
                  'packages': packages, 'profile': cfg.profile,
                  'embedding': {'model': cfg.embed_model, 'key_configured': bool(cfg.embed_api_key)},
                  'reranker': {'model': cfg.rerank_model, 'key_configured': bool(cfg.rerank_api_key)},
                  'sparse_backend': cfg.sparse_backend,
                  'chunking': cfg.chunk_config(), 'storage': str(storage),
                  'note': 'doctor 不调用模型；probe 才验证真实接口，hybrid 不等于 full 三路'})
            return 1 if 'missing' in packages.values() else 0
        if args.command == 'probe':
            # Probes bypass persistent caches; small non-private test strings only.
            import tempfile
            with tempfile.TemporaryDirectory() as temp:
                client = ModelClient(cfg, Path(temp))
                if args.capability == 'embedding':
                    if not cfg.embed_api_key:
                        raise DocBaseError('MODEL_NOT_CONFIGURED', 'Embedding Key 未填写')
                    vectors = client.embeddings(['Version number is 1729.', 'Data flows from input A to output B.'])
                    data = {'count': len(vectors), 'dimension': int(vectors.shape[1]), 'finite': True}
                elif args.capability == 'reranker':
                    if not cfg.rerank_api_key:
                        raise DocBaseError('MODEL_NOT_CONFIGURED', 'Reranker Key 未填写')
                    scores = client.rerank('What is the version number?', ['The version number is 1729.', 'A picture shows a tree.'])
                    data = {'ranked_candidate_indices': [i for i, _ in scores], 'scores': [s for _, s in scores]}
                else:
                    weights = client.sparse(['Version number is 1729.'])
                    data = {'count': len(weights), 'nonzero_terms': len(weights[0])}
                emit({'status': 'ok', 'capability': args.capability, 'data': data, 'usage': client.stats})
                return 0
        if args.command == 'scan':
            from .ingest import scan_artifacts
            report = scan_artifacts(args.input, args.catalog or storage / 'catalog.json')
        elif args.command == 'select':
            from .ingest import select_artifact
            report = select_artifact(args.artifact, args.catalog or storage / 'catalog.json')
        elif args.command == 'build':
            from .engine import build_index
            report = build_index(args.catalog or storage / 'catalog.json', storage, cfg,
                                 collection=args.collection, profile=profile,
                                 incremental=args.incremental, quality=args.quality, allow_empty=args.allow_empty)
        elif args.command == 'overview':
            from .overview import overview
            report = overview(storage,collection=args.collection,index_version=args.index_version,category=args.category,budget=args.budget,
                              allowed_doc_ids=read_json(args.allowed_doc_ids_file) if args.allowed_doc_ids_file else None)
        elif args.command == 'query':
            from .engine import query
            report = query(args.question, storage, cfg, collection=args.collection,
                           index_version=args.index_version, top_k=args.top_k,
                           category=args.category, profile=profile, prefer_body=args.prefer_body,
                           allowed_doc_ids=read_json(args.allowed_doc_ids_file) if args.allowed_doc_ids_file else None)
        elif args.command == 'get':
            from .engine import get_evidence
            report = get_evidence(args.chunk_id, storage, collection=args.collection, index_version=args.index_version)
        elif args.command == 'context':
            from .engine import get_context
            report = get_context(args.chunk_id, storage, collection=args.collection,
                                 index_version=args.index_version, before=args.before, after=args.after)
        elif args.command == 'test':
            from .engine import progress
            from .evaluator import eval_retrieval
            report = eval_retrieval(args.dataset, storage, cfg, collection=args.collection,
                                    index_version=args.index_version, top_k=args.top_k, profile=profile, reporter=progress)
        else:
            from .engine import load_index
            path, manifest, _ = load_index(storage, args.collection, args.index_version)
            report = {'status': 'ok', 'index_path': str(path), 'manifest': manifest}
        emit(report)
        return 2 if report.get('status') == 'partial' else 0
    except (DocBaseError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, DocBaseError) else '本地文件读写或数据格式错误，请检查输入路径和诊断记录'
        if cfg:
            for secret in (cfg.embed_api_key, cfg.rerank_api_key, cfg.sparse_api_key):
                if secret:
                    message = message.replace(secret, '[REDACTED]')
        emit({'status': 'failed', 'code': getattr(exc, 'code', 'LOCAL_ERROR'), 'error': message})
        return 1
    except KeyboardInterrupt:
        emit({'status': 'interrupted', 'note': '旧 CURRENT 保留；确认进程退出后清理对应构建锁再重试'})
        return 130
