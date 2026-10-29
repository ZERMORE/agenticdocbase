from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jieba
import numpy as np
from rank_bm25 import BM25Plus

from .chunker import make_chunks
from .clients import ModelClient
from .common import DocBaseError, digest, file_hash, inside, name_checked, read_json, write_json, writer_lock
from .config import ROOT, Settings
from .ingest import load_catalog, save_source_bundle


def progress(message):
    print(message, file=sys.stderr, flush=True)


def make_tokenizer(cfg):
    jieba.setLogLevel(logging.ERROR)
    tokenizer = jieba.Tokenizer()
    dictionary_hash = ''
    if cfg.dictionary_path:
        path = Path(cfg.dictionary_path)
        try:
            dictionary_hash = file_hash(path)
            tokenizer.load_userdict(str(path))
        except OSError as exc:
            raise DocBaseError('DICTIONARY', '术语词典路径不可读') from exc
    def tokenize(text):
        values = []
        for token in re.findall(r'[A-Za-z][A-Za-z0-9_./:-]*|[\u4e00-\u9fff]+|\d+(?:\.\d+)?', text):
            if '\u4e00' <= token[0] <= '\u9fff':
                values.extend(word for word in tokenizer.lcut(token) if word.strip())
            else:
                value = token.lower().rstrip('.:/-')
                if value:
                    values.append(value)
        return values
    return tokenize, {'version': 'jieba-regex-v1', 'dictionary_hash': dictionary_hash, 'bm25': 'BM25Plus-v1'}


def rrf_fuse(rankings, k=60):
    result = {}
    for route, identifiers in rankings.items():
        if len(identifiers) != len(set(identifiers)):
            raise DocBaseError('RANKING', '同一路召回有重复 chunk ID')
        for rank, identifier in enumerate(identifiers, 1):
            row = result.setdefault(identifier, {'rrf': 0.0})
            row['rrf'] += 1.0 / (k + rank)
            row[route + '_rank'] = rank
    return sorted(result.items(), key=lambda row: (-row[1]['rrf'], row[0]))


def sparse_postings(weights):
    postings = {}
    for i, vector in enumerate(weights):
        for token, weight in vector.items():
            if weight > 0:
                postings.setdefault(token, []).append([i, weight])
    return postings


def sparse_scores(postings, query_weights, allowed):
    scores = {}
    for token, weight in query_weights.items():
        for row, value in postings.get(token, []):
            if row in allowed:
                scores[row] = scores.get(row, 0.0) + weight * value
    return scores


def build_index(catalog, storage=None, settings=None, *, collection='default', profile=None,
                incremental=False, quality='usable', client=None, reporter=progress, allow_empty=False):
    cfg = settings or Settings.load()
    profile = profile or cfg.profile
    cfg.validate()
    if profile not in {'bm25-only', 'hybrid', 'full'}:
        raise DocBaseError('CONFIG', 'profile 不受支持')
    cfg.require(profile)
    storage = Path(storage or ROOT / 'storage').resolve()
    name_checked(collection)
    client = client or ModelClient(cfg, storage / 'caches', progress=reporter)
    collection_path = storage / 'collections' / collection
    with writer_lock(collection_path / 'build.lock'):
        reporter('[1/6] 校验登记产物与内容版本')
        artifacts = load_catalog(Path(catalog).resolve(), quality, allow_empty=allow_empty)
        reporter('[2/6] 保存不可变来源快照并切片')
        for artifact in artifacts:
            save_source_bundle(artifact, storage)
        chunks = make_chunks(artifacts, cfg)
        if not chunks and artifacts:
            raise DocBaseError('NO_CONTENT', '所选产物没有有效可索引内容')
        tokenize, lexical_config = make_tokenizer(cfg)
        tokens = [tokenize(chunk['search_text']) for chunk in chunks]
        if chunks and not any(tokens):
            raise DocBaseError('NO_CONTENT', '所选正文没有可检索词项')
        inputs = [{'artifact_id': a['artifact_id'], 'bundle_id': a['bundle_id'],
                   'category': a.get('category_override')} for a in artifacts]
        signature = digest({'inputs': inputs, 'profile': profile, 'chunks': cfg.chunk_config(),
                            'lexical': lexical_config, 'embedding': cfg.encoding_config() if profile != 'bm25-only' else None,
                            'sparse': cfg.sparse_config() if profile == 'full' else None})
        current_file = collection_path / 'CURRENT.json'
        if incremental and current_file.exists():
            old = read_json(current_file)
            old_path, old_manifest, _ = load_index(storage, collection, old['index_version'])
            if old_manifest['input_signature'] == signature:
                return {'status': 'ok', 'action': 'unchanged', 'index_version': old['index_version'],
                        'index_path': str(old_path), 'chunk_count': len(chunks), 'model_usage': client.stats}
        old_manifest = None
        if current_file.exists():
            _, old_manifest, _ = load_index(storage, collection)
        matrices = None
        sparse_vectors = None
        reporter(f'[3/6] 编码 {len(chunks)} 片；长度单位 {cfg.chunk_config()["length_unit"]}')
        if profile != 'bm25-only':
            matrices = client.embeddings([chunk['search_text'] for chunk in chunks])
        if profile == 'full':
            # Cache successful sparse outputs independently of the index snapshot.
            sparse_vectors = []
            for offset in range(0, len(chunks), cfg.batch_size):
                group = chunks[offset:offset + cfg.batch_size]
                keys = [digest([cfg.sparse_config(), chunk['search_text']]) for chunk in group]
                values = [None] * len(group)
                missing = []
                for i, key in enumerate(keys):
                    path = storage / 'caches' / 'sparse' / (key + '.json')
                    try:
                        values[i] = client.validate_sparse([read_json(path)], 1)[0]
                    except (OSError, ValueError, DocBaseError):
                        missing.append(i)
                if missing:
                    reporter(f'Sparse 批次 {offset // cfg.batch_size + 1}/{math.ceil(len(chunks) / cfg.batch_size)}')
                    new = client.sparse([group[i]['search_text'] for i in missing])
                    for i, value in zip(missing, new):
                        values[i] = value
                        write_json(storage / 'caches' / 'sparse' / (keys[i] + '.json'), value)
                sparse_vectors.extend(values)
        version = 'idx_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
        index_root = storage / 'indexes' / collection
        index_root.mkdir(parents=True, exist_ok=True)
        destination = index_root / version
        reporter('[4/6] 构建元数据、词项及向量索引快照')
        with tempfile.TemporaryDirectory(prefix='.build-', dir=index_root) as tmp:
            work = Path(tmp)
            connection = sqlite3.connect(work / 'metadata.sqlite')
            try:
                connection.execute('CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                connection.executemany('INSERT INTO chunks VALUES (?, ?)', [(c['chunk_id'], json.dumps(c, ensure_ascii=False)) for c in chunks])
                connection.commit()
            finally:
                connection.close()
            write_json(work / 'rows.json', [chunk['chunk_id'] for chunk in chunks])
            write_json(work / 'bm25_tokens.json', tokens)
            if matrices is not None:
                np.save(work / 'dense.npy', matrices, allow_pickle=False)
            if sparse_vectors is not None:
                write_json(work / 'sparse_postings.json', sparse_postings(sparse_vectors))
            components = {file.name: file_hash(file) for file in work.iterdir() if file.is_file()}
            old_artifacts = set(a['artifact_id'] for a in old_manifest['artifacts']) if old_manifest else set()
            new_artifacts = {a['artifact_id'] for a in artifacts}
            warnings = sorted(set(warning for chunk in chunks for warning in chunk['quality_warnings']))
            manifest = {'schema_version': '1', 'index_version': version, 'profile': profile,
                        'collection': collection, 'input_signature': signature, 'artifacts': inputs,
                        'chunk_count': len(chunks), 'dimension': int(matrices.shape[1]) if matrices is not None else None,
                        'embedding': cfg.encoding_config() if matrices is not None else None,
                        'sparse': cfg.sparse_config() if sparse_vectors is not None else None,
                        'lexical': lexical_config, 'chunking': cfg.chunk_config(),
                        'quality_warnings': warnings, 'components': components,
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'model_usage': client.stats, 'added_artifacts': len(new_artifacts - old_artifacts),
                        'removed_artifacts': len(old_artifacts - new_artifacts),
                        'excluded_blocks': sum(a['excluded_blocks'] for a in artifacts)}
            write_json(work / 'index_manifest.json', manifest)
            reporter('[5/6] 验证组件摘要和行号对应关系')
            if matrices is not None and matrices.shape[0] != len(chunks):
                raise DocBaseError('INDEX_INVALID', '向量行数不匹配')
            for file, checksum in components.items():
                if file_hash(work / file) != checksum:
                    raise DocBaseError('INDEX_INVALID', '构建组件校验失败')
            work.rename(destination)
        reporter('[6/6] 发布 CURRENT 指针，历史版本保留')
        write_json(current_file, {'schema_version': '1', 'index_version': version})
        report = {'status': 'partial' if warnings else 'ok', 'action': 'built', 'index_version': version,
                  'index_path': str(destination), 'profile': profile, 'chunk_count': len(chunks),
                  'document_count': len(artifacts), 'excluded_blocks': manifest['excluded_blocks'],
                  'dimension': manifest['dimension'], 'length_unit': cfg.chunk_config()['length_unit'],
                  'quality_warnings': warnings, 'model_usage': client.stats}
        report['report_path'] = str(storage / 'reports' / ('build_' + version + '.json'))
        write_json(Path(report['report_path']), report)
        return report


def load_index(storage, collection='default', version=None):
    storage = Path(storage).resolve()
    name_checked(collection)
    try:
        if version is None:
            version = read_json(storage / 'collections' / collection / 'CURRENT.json')['index_version']
        name_checked(version)
        path = storage / 'indexes' / collection / version
        manifest = read_json(path / 'index_manifest.json')
        if manifest['index_version'] != version or manifest.get('schema_version') != '1':
            raise DocBaseError('INDEX_INVALID', '索引身份或 schema 不正确')
        for relative, checksum in manifest['components'].items():
            if file_hash(inside(path, relative)) != checksum:
                raise DocBaseError('INDEX_CORRUPT', '索引组件摘要不匹配')
        ids = read_json(path / 'rows.json')
        connection = sqlite3.connect((path / 'metadata.sqlite').as_uri() + '?mode=ro', uri=True)
        try:
            rows = {key: json.loads(data) for key, data in connection.execute('SELECT chunk_id, data FROM chunks')}
        finally:
            connection.close()
        if len(rows) != manifest['chunk_count'] or set(rows) != set(ids) or len(ids) != len(rows):
            raise DocBaseError('INDEX_INVALID', '索引行/切片映射不一致')
        return path, manifest, [rows[key] for key in ids]
    except DocBaseError:
        raise
    except (OSError, KeyError, ValueError, sqlite3.Error) as exc:
        raise DocBaseError('INDEX_NOT_READY', '索引未就绪或不可读；先执行 build') from exc


def resolve_sources(chunk, storage):
    result = []
    for source in chunk['sources']:
        source = dict(source)
        bundle = Path(storage) / 'source_store' / source['source_bundle_id']
        for field in ('snapshot_path', 'rendered_pdf_path', 'image_path'):
            if source.get(field):
                path = inside(bundle, source[field])
                if not path.is_file():
                    raise DocBaseError('SOURCE_MISSING', '索引引用的来源文件缺失')
                source[field + '_absolute'] = str(path)
        result.append(source)
    return result


def query(question, storage=None, settings=None, *, collection='default', index_version=None,
          top_k=5, category=None, profile=None, client=None, prefer_body=False, allowed_doc_ids=None):
    started = time.monotonic()
    if not isinstance(question, str) or not question.strip() or len(question) > 12000:
        raise DocBaseError('QUERY', '问题不能为空，且不超过 12000 字符')
    if not 1 <= top_k <= 100:
        raise DocBaseError('QUERY', 'top_k 应在 1～100')
    storage = Path(storage or ROOT / 'storage').resolve()
    cfg = settings or Settings.load()
    profile = profile or cfg.profile
    cfg.validate()
    path, manifest, chunks = load_index(storage, collection, index_version)
    if profile != 'bm25-only' and manifest['profile'] == 'bm25-only' or profile == 'full' and manifest['profile'] != 'full':
        raise DocBaseError('PROFILE_MISMATCH', '当前快照不具备指定模式，请按所需模式重新建库')
    if profile not in {'bm25-only', 'hybrid', 'full'}:
        raise DocBaseError('CONFIG', 'profile 不受支持')
    cfg.require(profile)
    if profile != 'bm25-only' and manifest['embedding'] != cfg.encoding_config():
        raise DocBaseError('MODEL_CHANGED', 'Embedding 模型/配置与索引不一致，必须重建或恢复原配置')
    if profile == 'full' and manifest['sparse'] != cfg.sparse_config():
        raise DocBaseError('MODEL_CHANGED', 'Sparse 模型/配置与索引不一致')
    tokenize, lexical = make_tokenizer(cfg)
    if lexical != manifest['lexical']:
        raise DocBaseError('DICTIONARY_CHANGED', '分词或词典与索引不一致，需要重建')
    allowed_ids = set(allowed_doc_ids) if allowed_doc_ids is not None else None
    allowed = [i for i, chunk in enumerate(chunks) if (category is None or chunk['category'] == category)
               and (allowed_ids is None or chunk['doc_id'] in allowed_ids)]
    client = client or ModelClient(cfg, storage / 'caches')
    timings, rankings = {}, {}
    tokens = read_json(path / 'bm25_tokens.json')
    if len(tokens) != len(chunks):
        raise DocBaseError('INDEX_INVALID', 'BM25 行号映射不一致')
    t = time.monotonic()
    words = tokenize(question)
    lexical_ranks = []
    if allowed and words:
        # Corpus statistics are computed after category filtering, before choosing Top-N.
        subset = [tokens[i] for i in allowed]
        if any(subset):
            model = BM25Plus(subset)
            scores = model.get_scores(words)
            def body_penalty(j):
                chunk = chunks[allowed[j]]
                refs = chunk.get('section_role') == 'references' or any(h.strip().lower() in {'references', 'bibliography', '参考文献'} for h in chunk['heading_path'])
                return int(refs) * 2 + int(len(chunk['text'].strip()) < 40) if prefer_body else 0
            lexical_ranks = [chunks[allowed[j]]['chunk_id'] for j in sorted(range(len(allowed)), key=lambda j: (body_penalty(j), -scores[j], chunks[allowed[j]]['chunk_id']))
                             if set(words).intersection(subset[j])][:cfg.recall_k]
    rankings['bm25'] = lexical_ranks
    timings['bm25'] = round(time.monotonic() - t, 4)
    if profile != 'bm25-only' and allowed:
        t = time.monotonic()
        matrix = np.load(path / 'dense.npy', allow_pickle=False, mmap_mode='r')
        vector = client.embeddings([question], role='query')[0]
        if matrix.shape != (len(chunks), manifest['dimension']) or len(vector) != matrix.shape[1]:
            raise DocBaseError('VECTOR_INVALID', '查询/文档向量维度不一致')
        scores = matrix[allowed] @ vector
        rankings['dense'] = [chunks[allowed[j]]['chunk_id'] for j in sorted(range(len(allowed)), key=lambda j: (-float(scores[j]), chunks[allowed[j]]['chunk_id']))[:cfg.recall_k]]
        timings['dense'] = round(time.monotonic() - t, 4)
    if profile == 'full' and allowed:
        t = time.monotonic()
        weights = client.sparse([question])[0]
        scores = sparse_scores(read_json(path / 'sparse_postings.json'), weights, set(allowed))
        rankings['sparse'] = [chunks[i]['chunk_id'] for i in sorted(scores, key=lambda i: (-scores[i], chunks[i]['chunk_id'])) if scores[i] > 0][:cfg.recall_k]
        timings['sparse'] = round(time.monotonic() - t, 4)
    t = time.monotonic()
    fused = rrf_fuse(rankings, cfg.rrf_k)
    timings['fusion'] = round(time.monotonic() - t, 4)
    by_id = {chunk['chunk_id']: chunk for chunk in chunks}
    candidates = fused[:max(cfg.rerank_candidates, top_k)]
    ordered = [(i, None) for i in range(len(candidates))]
    if profile != 'bm25-only' and candidates:
        t = time.monotonic()
        ordered = client.rerank(question, [by_id[key]['search_text'] for key, _ in candidates])
        timings['rerank'] = round(time.monotonic() - t, 4)
    results = []
    seen_content = {}
    for position, rerank_score in ordered:
        identifier, scores = candidates[position]
        chunk = by_id[identifier]
        content_key = (chunk['artifact_id'], chunk['text'])
        if content_key in seen_content:
            existing = seen_content[content_key]
            existing['equivalent_chunk_ids'].append(identifier)
            for source in resolve_sources(chunk, storage):
                if source not in existing['sources']:
                    existing['sources'].append(source)
            existing['block_ranges'].extend(r for r in chunk['block_ranges'] if r not in existing['block_ranges'])
            continue
        if len(results) >= top_k:
            continue
        result = dict(chunk)
        result['block_ranges'] = list(chunk['block_ranges'])
        result['equivalent_chunk_ids'] = [identifier]
        result['sources'] = resolve_sources(chunk, storage)
        result['scores'] = dict(scores, rerank=rerank_score)
        results.append(result)
        seen_content[content_key] = result
    timings['total'] = round(time.monotonic() - started, 4)
    routes = ['bm25'] + (['dense'] if profile != 'bm25-only' else []) + (['sparse'] if profile == 'full' else [])
    report = {'schema_version': '1', 'query_id': 'q_' + uuid.uuid4().hex[:16], 'status': 'ok',
              'query': question, 'index_version': manifest['index_version'], 'profile': profile, 'prefer_body': prefer_body,
              'results': results, 'diagnostics': {'active_routes': routes, 'degraded': False,
                'rerank_executed': profile != 'bm25-only' and bool(candidates),
                'candidate_counts': {route: len(ranking) for route, ranking in rankings.items()},
                'quality_warnings': manifest['quality_warnings'], 'timings': timings,
                'model_usage': client.stats, 'rankings': rankings,
                'models': {'embedding': cfg.embed_model if profile != 'bm25-only' else None,
                           'reranker': cfg.rerank_model if profile != 'bm25-only' else None,
                           'sparse': cfg.sparse_model if profile == 'full' else None}}}
    report['report_path'] = str(storage / 'reports' / (report['query_id'] + '.json'))
    write_json(Path(report['report_path']), report)
    return report


def get_evidence(chunk_id, storage=None, *, collection='default', index_version=None):
    storage = Path(storage or ROOT / 'storage').resolve()
    _, manifest, chunks = load_index(storage, collection, index_version)
    for chunk in chunks:
        if chunk['chunk_id'] == chunk_id:
            return {'status': 'ok', 'index_version': manifest['index_version'],
                    'evidence': dict(chunk, sources=resolve_sources(chunk, storage))}
    raise DocBaseError('NOT_FOUND', '该版本中不存在指定 chunk_id')


def get_context(chunk_id, storage=None, *, collection='default', index_version=None, before=1, after=1):
    """Read adjacent successful blocks from checksum-verified immutable bundles."""
    from .ingest import inspect_artifact
    from .chunker import adjacent_prose
    if type(before) is not int or type(after) is not int or not 0 <= before <= 2 or not 0 <= after <= 2:
        raise DocBaseError('CONTEXT', 'before/after 必须在 0～2 之间')
    storage = Path(storage or ROOT / 'storage').resolve()
    target = get_evidence(chunk_id, storage, collection=collection, index_version=index_version)
    chunk, results, seen = target['evidence'], [], set()
    verified_bundles = {}
    for source in chunk['sources']:
        bundle_id = name_checked(source['source_bundle_id'])
        bundle = inside(storage / 'source_store', bundle_id)
        if bundle_id not in verified_bundles:
            verified_bundles[bundle_id] = inspect_artifact(bundle)
        artifact = verified_bundles[bundle_id]
        if artifact['bundle_id'] != bundle_id or artifact['artifact_id'] != chunk['artifact_id']:
            raise DocBaseError('SOURCE_CORRUPT', '上下文来源与索引登记摘要不一致')
        blocks = artifact['blocks']
        matches = [i for i, b in enumerate(blocks) if b['block_id'] == source['source_block_id']]
        if len(matches) != 1:
            raise DocBaseError('SOURCE_CORRUPT', '来源块不存在或重复')
        center = matches[0]
        chosen = [center]
        for step, count in ((-1, before), (1, after)):
            for distance in range(1, count + 1):
                i = center + step * distance
                if not 0 <= i < len(blocks):
                    break
                b = blocks[i]
                if b['type'] == 'heading' or b.get('heading_path') != blocks[center].get('heading_path'):
                    break
                # Never append an unrelated table or figure as paragraph continuation.
                if b['type'] != blocks[center]['type'] or b['type'] not in {'paragraph', 'ocr_text'}:
                    break
                neighbor = blocks[i - step]
                left, right = (b, neighbor) if step < 0 else (neighbor, b)
                if not adjacent_prose(left, right):
                    break
                chosen.append(i)
        for i in sorted(chosen):
            block = blocks[i]
            key = (bundle_id, block['block_id'])
            if key in seen:
                continue
            seen.add(key)
            src = dict(block['source'], doc_id=chunk['doc_id'], revision=source['revision'],
                       source_bundle_id=bundle_id, source_block_id=block['block_id'])
            record = dict(chunk, chunk_id=None, evidence_kind='context_block', text=block['text'],
                          type=block['type'], heading_path=block.get('heading_path', []),
                          block_ranges=[{'block_id': block['block_id'], 'start': 0, 'end': len(block['text'])}],
                          sources=[src])
            record['content_origin'] = [block.get('content_origin') or {'figure_description':'model_description', 'vision_transcription':'model_transcription', 'ocr_text':'ocr'}.get(block['type'], 'native_text')]
            record['sources'] = resolve_sources(record, storage)
            results.append(record)
    return {'status': 'ok', 'index_version': target['index_version'], 'results': results}
