import json
import math
import sqlite3
from pathlib import Path

import httpx
import numpy as np
import pytest

from docbase.chunker import make_chunks
from docbase.clients import ModelClient
from docbase.common import DocBaseError, digest, file_hash, read_json, write_json
from docbase.config import Settings
from docbase.engine import build_index, get_evidence, load_index, query, rrf_fuse, sparse_scores, sparse_postings
from docbase.evaluator import eval_retrieval
from docbase.ingest import inspect_artifact, load_catalog, scan_artifacts
from docbase.engine import get_context


def test_empty_index_and_document_scope(tmp_path, cfg):
    catalog = tmp_path / 'empty.json'
    write_json(catalog, {'schema_version': '1', 'entries': []})
    storage = tmp_path / 'store'
    result = build_index(catalog, storage, cfg, allow_empty=True, reporter=lambda _: None)
    assert result['chunk_count'] == 0
    assert query('anything', storage, cfg)['results'] == []
    root = tmp_path / 'parsed'
    create_artifact(root, doc_id='first')
    create_artifact(root, doc_id='second')
    scan_artifacts(root, catalog)
    build_index(catalog, storage, cfg, reporter=lambda _: None)
    assert {r['doc_id'] for r in query('1729', storage, cfg, allowed_doc_ids=['first'])['results']} == {'first'}
    assert query('1729', storage, cfg, allowed_doc_ids=[])['results'] == []


def create_artifact(root, *, doc_id='doc_a', category='技术', status='ok', text=None):
    path = root / doc_id
    (path / 'source').mkdir(parents=True, exist_ok=True)
    source = path / 'source' / 'sample.txt'
    source.write_text('Original facts: version 1729. INPUT A sends data to OUTPUT B.', encoding='utf-8')
    revision = file_hash(source)
    values = text or ['Version number is 1729.', 'Data flows from INPUT A to OUTPUT B.', 'A tree grows outside.']
    blocks = []
    for i, value in enumerate(values):
        blocks.append({'schema_version': '1', 'doc_id': doc_id, 'revision': revision,
                       'block_id': f'b{i}', 'type': 'paragraph', 'text': value, 'heading_path': [],
                       'category': category, 'status': 'ok',
                       'source': {'original_path': str(source), 'snapshot_path': 'source/sample.txt',
                                  'rendered_pdf_path': None, 'page_index': i + 1,
                                  'slide_number': None, 'anchor': None, 'bbox': None,
                                  'image_path': None, 'figure_id': None}})
    (path / 'blocks.jsonl').write_text(''.join(json.dumps(b) + '\n' for b in blocks), encoding='utf-8')
    manifest = {'schema_version': '1', 'doc_id': doc_id, 'revision': revision,
                'config_fingerprint': 'parse-v1', 'status': status, 'source_path': str(source),
                'source_snapshot': 'source/sample.txt', 'rendered_pdf': None,
                'block_count': len(blocks), 'warnings': []}
    write_json(path / 'manifest.json', manifest)
    return path


def test_context_checks_bundle_and_offsets(sample, cfg):
    _, catalog, storage = sample
    report = build_index(catalog, storage, cfg, reporter=lambda _: None)
    hit = query('1729', storage, cfg)['results'][0]
    context = get_context(hit['chunk_id'], storage, index_version=report['index_version'])
    assert context['results'][0]['text'] == hit['text']
    assert all(r['chunk_id'] is None and r['evidence_kind'] == 'context_block' for r in context['results'])
    bundle = storage / 'source_store' / hit['sources'][0]['source_bundle_id']
    with (bundle / 'blocks.jsonl').open('a', encoding='utf-8') as stream:
        stream.write('\n')
    with pytest.raises(DocBaseError):
        get_context(hit['chunk_id'], storage, index_version=report['index_version'])


def test_context_grouping_preserves_multiple_block_ranges(tmp_path, cfg):
    root = tmp_path / 'parsed'
    artifact = create_artifact(root, text=['  The first paragraph contains enough meaningful words to explain the proposed method. ', 'The second paragraph continues the discussion with details of the evaluation setup.'])
    blocks_path = artifact / 'blocks.jsonl'
    blocks = [json.loads(x) for x in blocks_path.read_text().splitlines()]
    for b in blocks:
        b['source']['page_index'] = 1
        b['heading_path'] = ['9']
    blocks_path.write_text(''.join(json.dumps(b) + '\n' for b in blocks), encoding='utf-8')
    catalog = tmp_path / 'catalog.json'
    scan_artifacts(root, catalog)
    cfg.chunk_strategy = 'context-v2'
    storage = tmp_path / 'store'
    report = build_index(catalog, storage, cfg, reporter=lambda _: None)
    assert report['chunk_count'] == 1
    hit = query('paragraph', storage, cfg)['results'][0]
    assert len(hit['block_ranges']) == 2 and len(hit['sources']) == 2
    assert hit['heading_path'] == []
    for r in hit['block_ranges']:
        original = next(b for b in blocks if b['block_id'] == r['block_id'])
        assert original['text'][r['start']:r['end']] in hit['text']
    assert len(get_context(hit['chunk_id'], storage)['results']) == 2


def test_prefer_body_is_opt_in(tmp_path, cfg):
    artifact = create_artifact(tmp_path / 'parsed', text=['vision language', 'vision language models use visual and textual features for transfer learning.'])
    p = artifact / 'blocks.jsonl'
    blocks = [json.loads(x) for x in p.read_text().splitlines()]
    blocks[0]['heading_path'] = ['References']
    p.write_text(''.join(json.dumps(b) + '\n' for b in blocks), encoding='utf-8')
    catalog = tmp_path / 'catalog.json'
    scan_artifacts(artifact, catalog)
    storage = tmp_path / 'store'
    build_index(catalog, storage, cfg, reporter=lambda _: None)
    assert query('vision language', storage, cfg)['results'][0]['heading_path'] == ['References']
    assert query('vision language', storage, cfg, prefer_body=True)['results'][0]['heading_path'] == []


def test_context_grouping_does_not_merge_diagram_labels(tmp_path, cfg):
    artifact_path = create_artifact(tmp_path, text=[
        'The model combines visual features and language features through a trainable projection layer.',
        'Language Model', 'Projection',
        'The evaluation measures how the trained model answers visual questions on unseen images.'])
    artifact = inspect_artifact(artifact_path)
    for b in artifact['blocks']:
        b['source']['page_index'] = 1
    cfg.chunk_strategy = 'context-v2'
    chunks = make_chunks([artifact], cfg)
    assert len(chunks) == 4
    assert all(not ('Projection' in r['text'] and 'evaluation' in r['text']) for r in chunks)


def test_plain_paragraph_references_boundary(tmp_path, cfg):
    artifact_path = create_artifact(tmp_path, text=[
        'This section explains the method used to align visual features with a language model.',
        'References', '[1] Example author. A vision language model. Conference proceedings, 2024.'])
    artifact = inspect_artifact(artifact_path)
    cfg.chunk_strategy = 'context-v2'
    chunks = make_chunks([artifact], cfg)
    assert len(chunks) == 2
    assert chunks[0]['section_role'] == 'body' and chunks[1]['section_role'] == 'references'


@pytest.fixture
def sample(tmp_path):
    artifact = create_artifact(tmp_path / 'parsed')
    catalog = tmp_path / 'catalog.json'
    scan_artifacts(tmp_path / 'parsed', catalog)
    return artifact, catalog, tmp_path / 'store'


@pytest.fixture
def cfg():
    return Settings(profile='bm25-only', embed_api_key='test-secret', rerank_api_key='test-secret', retries=0)


def mock_client(cfg, tmp_path, handler=None):
    def server(request):
        body = json.loads(request.content)
        if request.url.path.endswith('/embeddings'):
            data = []
            for i, text in enumerate(body['input']):
                v = [1, .1, .1] if '1729' in text or 'version' in text.lower() else ([.1, 1, .1] if 'flow' in text.lower() or 'INPUT' in text else [.1, .1, 1])
                data.append({'index': i, 'embedding': v})
            return httpx.Response(200, json={'data': data[::-1], 'usage': {'total_tokens': 5}})
        if request.url.path.endswith('/sparse_embeddings'):
            return httpx.Response(200, json={'lexical_weights': [{'1': 1.0, '2': .2} for text in body['input']]})
        if request.url.path.endswith('/rerank'):
            return httpx.Response(200, json={'results': [{'index': i, 'relevance_score': .9 if '1729' in text else .1}
                                                       for i, text in reversed(list(enumerate(body['documents']))) ]})
        raise AssertionError(request.url)
    return ModelClient(cfg, tmp_path / 'cache', transport=httpx.MockTransport(handler or server), sleeper=lambda _: None)


def test_bm25_build_query_get_and_reload(sample, cfg):
    artifact, catalog, storage = sample
    report = build_index(catalog, storage, cfg, reporter=lambda _: None)
    assert report['status'] == 'ok' and report['chunk_count'] == 3
    result = query('version 1729', storage, cfg)
    assert result['results'][0]['text'] == 'Version number is 1729.'
    assert result['diagnostics']['active_routes'] == ['bm25']
    citation = result['results'][0]['sources'][0]
    assert Path(citation['snapshot_path_absolute']).is_file()
    assert 'source_store' in citation['snapshot_path_absolute']
    evidence = get_evidence(result['results'][0]['chunk_id'], storage, index_version=result['index_version'])
    assert evidence['evidence']['text'] == result['results'][0]['text']
    assert query('version 1729', storage, cfg)['results'][0]['chunk_id'] == evidence['evidence']['chunk_id']


def test_hybrid_protocol_dense_rows_and_rerank(sample, cfg, tmp_path):
    _, catalog, storage = sample
    cfg.profile = 'hybrid'
    client = mock_client(cfg, tmp_path)
    build = build_index(catalog, storage, cfg, client=client, reporter=lambda _: None)
    assert build['dimension'] == 3
    result = query('What is the version number?', storage, cfg, client=client)
    assert result['results'][0]['text'] == 'Version number is 1729.'
    assert result['results'][0]['scores']['rerank'] == .9
    assert set(result['diagnostics']['active_routes']) == {'dense', 'bm25'}
    assert result['diagnostics']['rerank_executed']
    assert 'test-secret' not in json.dumps(read_json(Path(build['index_path']) / 'index_manifest.json'))


def test_full_uses_real_sparse_adapter_contract(sample, cfg, tmp_path):
    _, catalog, storage = sample
    cfg.profile, cfg.sparse_backend = 'full', 'http'
    cfg.sparse_base_url, cfg.sparse_model = 'https://sparse.test/v1', 'BAAI/bge-m3'
    client = mock_client(cfg, tmp_path)
    build_index(catalog, storage, cfg, client=client, reporter=lambda _: None)
    result = query('version', storage, cfg, client=client)
    assert 'sparse' in result['diagnostics']['active_routes']
    assert result['results'][0]['scores']['sparse_rank'] is not None


def test_pending_text_is_not_knowledge(sample, cfg):
    artifact, catalog, storage = sample
    blocks = [json.loads(line) for line in (artifact / 'blocks.jsonl').read_text().splitlines()]
    pending = dict(blocks[0], block_id='pending', type='figure_description', text='图像释义未完成', status='pending')
    blocks += [pending, dict(pending, block_id='image', type='image', text='文档图像', status='ok')]
    (artifact / 'blocks.jsonl').write_text(''.join(json.dumps(b) + '\n' for b in blocks), encoding='utf-8')
    manifest = read_json(artifact / 'manifest.json')
    manifest.update(block_count=len(blocks), status='partial')
    write_json(artifact / 'manifest.json', manifest)
    build = build_index(catalog, storage, cfg, reporter=lambda _: None)
    assert build['chunk_count'] == 3 and build['excluded_blocks'] == 2 and build['status'] == 'partial'
    assert not query('图像释义未完成', storage, cfg)['results']
    with pytest.raises(DocBaseError, match='strict'):
        load_catalog(catalog, 'strict')


def test_same_revision_reparse_and_incremental(sample, cfg):
    artifact, catalog, storage = sample
    first = build_index(catalog, storage, cfg, reporter=lambda _: None)
    unchanged = build_index(catalog, storage, cfg, incremental=True, reporter=lambda _: None)
    assert unchanged['action'] == 'unchanged'
    blocks = [json.loads(line) for line in (artifact / 'blocks.jsonl').read_text().splitlines()]
    blocks[0]['text'] = 'New parsed explanation contains ZEBRA.'
    (artifact / 'blocks.jsonl').write_text(''.join(json.dumps(b) + '\n' for b in blocks), encoding='utf-8')
    new = build_index(catalog, storage, cfg, incremental=True, reporter=lambda _: None)
    assert new['index_version'] != first['index_version']
    assert query('ZEBRA', storage, cfg)['results']
    assert query('1729', storage, cfg, index_version=first['index_version'])['results']


def test_failed_build_preserves_current(sample, cfg, tmp_path):
    _, catalog, storage = sample
    first = build_index(catalog, storage, cfg, reporter=lambda _: None)
    cfg.profile = 'hybrid'
    client = mock_client(cfg, tmp_path, handler=lambda request: httpx.Response(503))
    with pytest.raises(DocBaseError, match='503'):
        build_index(catalog, storage, cfg, client=client, reporter=lambda _: None)
    assert read_json(storage / 'collections/default/CURRENT.json')['index_version'] == first['index_version']
    cfg.profile = 'bm25-only'
    assert query('1729', storage, cfg)['results']


def test_category_filter_before_topn(tmp_path, cfg):
    root = tmp_path / 'parsed'
    create_artifact(root, doc_id='a', category='one')
    create_artifact(root, doc_id='b', category='two', text=['Version number in category two is 2026.'])
    catalog, storage = tmp_path / 'catalog.json', tmp_path / 'storage'
    scan_artifacts(root, catalog)
    build_index(catalog, storage, cfg, reporter=lambda _: None)
    result = query('version', storage, cfg, category='two')
    assert result['results'] and all(r['category'] == 'two' for r in result['results'])
    assert not query('version', storage, cfg, category='missing')['results']


def test_update_deactivates_document_without_deleting_history(tmp_path, cfg):
    root = tmp_path / 'parsed'
    create_artifact(root, doc_id='a', text=['ALPHA unique fact.'])
    create_artifact(root, doc_id='b', text=['BETA unique fact.'])
    catalog, storage = tmp_path / 'catalog.json', tmp_path / 'storage'
    scan_artifacts(root, catalog)
    old = build_index(catalog, storage, cfg, reporter=lambda _: None)
    content = read_json(catalog)
    next(e for e in content['entries'] if e['doc_id'] == 'b')['active'] = False
    write_json(catalog, content)
    build_index(catalog, storage, cfg, incremental=True, reporter=lambda _: None)
    assert not query('BETA', storage, cfg)['results']
    assert query('BETA', storage, cfg, index_version=old['index_version'])['results']


def test_source_path_traversal_rejected(sample):
    artifact, _, _ = sample
    manifest = read_json(artifact / 'manifest.json')
    manifest['source_snapshot'] = '../outside.txt'
    write_json(artifact / 'manifest.json', manifest)
    with pytest.raises(DocBaseError, match='内部'):
        inspect_artifact(artifact)


def test_scan_conflicting_versions_require_explicit_choice(tmp_path):
    first = create_artifact(tmp_path / 'one', doc_id='a')
    second = create_artifact(tmp_path / 'two', doc_id='a', text=['Changed parsing output.'])
    catalog = tmp_path / 'catalog.json'
    result = scan_artifacts(tmp_path, catalog)
    assert result['conflicts'] == ['a'] and result['active'] == 0
    content = read_json(catalog)
    content['entries'][0]['active'] = True
    write_json(catalog, content)
    assert len(load_catalog(catalog)) == 1


def test_chunk_overflow_table_headers_and_stability(tmp_path, cfg):
    artifact = create_artifact(tmp_path, text=['A' * 1200,
        '| Name | Value |\n| --- | --- |\n' + ''.join(f'| row{i} | {"x"*30} |\n' for i in range(12))])
    blocks = [json.loads(l) for l in (artifact / 'blocks.jsonl').read_text().splitlines()]
    blocks[1]['type'] = 'table'
    (artifact / 'blocks.jsonl').write_text(''.join(json.dumps(b) + '\n' for b in blocks), encoding='utf-8')
    cfg.chunk_size, cfg.overlap = 200, 30
    art = inspect_artifact(artifact)
    chunks = make_chunks([art], cfg)
    assert all(c['length'] <= 200 for c in chunks)
    assert all(c['text'].startswith('| Name | Value |') for c in chunks if c['type'] == 'table')
    assert [c['chunk_id'] for c in chunks] == [c['chunk_id'] for c in make_chunks([art], cfg)]


def test_rrf_sparse_math():
    fused = rrf_fuse({'dense': ['a', 'b'], 'bm25': ['b', 'c']})
    assert fused[0][0] == 'b'
    assert fused[0][1]['rrf'] == pytest.approx(1/62 + 1/61)
    post = sparse_postings([{'1': 2, '2': 3}, {'1': 1}])
    assert sparse_scores(post, {'1': .5, '2': 2}, {0, 1}) == {0: 7, 1: .5}
    assert sparse_scores(post, {'1': .5}, {1}) == {1: .5}


@pytest.mark.parametrize('vectors', [[[0, 0]], [[float('nan'), 1]], [[1, 2], [1]], [[1, float('inf')]]])
def test_invalid_vectors_fail(vectors):
    with pytest.raises(DocBaseError):
        ModelClient.validate_vectors(vectors)


@pytest.mark.parametrize('status,attempts', [(401, 1), (403, 1), (400, 1), (429, 3), (503, 3)])
def test_http_failures_do_not_expose_keys(tmp_path, cfg, status, attempts):
    cfg.retries = 2
    client = mock_client(cfg, tmp_path, lambda req: httpx.Response(status, text='test-secret'))
    with pytest.raises(DocBaseError) as exc:
        client.embeddings(['test'])
    assert 'test-secret' not in str(exc.value)
    assert client.stats['requests'] == attempts
    assert not list((tmp_path / 'cache').rglob('*.json'))


def test_embedding_cache_role_and_model_invalidation(tmp_path, cfg):
    client = mock_client(cfg, tmp_path)
    a = client.embeddings(['Version 1729.'])
    client.embeddings(['Version 1729.'])
    assert client.stats['requests'] == 1
    client.embeddings(['Version 1729.'], role='query')
    assert client.stats['requests'] == 2
    cfg.embed_model = 'changed'
    client.embeddings(['Version 1729.'])
    assert client.stats['requests'] == 3
    assert np.linalg.norm(a[0]) == pytest.approx(1)


def test_rerank_bad_mapping_is_not_silently_accepted(tmp_path, cfg):
    client = mock_client(cfg, tmp_path, lambda req: httpx.Response(200, json={'results': [{'index': 0, 'relevance_score': .8}]}))
    with pytest.raises(DocBaseError):
        client.rerank('query', ['one', 'two'])


def test_corrupt_index_and_model_change(sample, cfg, tmp_path):
    _, catalog, storage = sample
    cfg.profile = 'hybrid'
    build = build_index(catalog, storage, cfg, client=mock_client(cfg, tmp_path), reporter=lambda _: None)
    cfg.embed_model = 'another-model'
    with pytest.raises(DocBaseError, match='不一致'):
        query('query', storage, cfg)
    (Path(build['index_path']) / 'rows.json').write_text('[]')
    with pytest.raises(DocBaseError, match='摘要'):
        load_index(storage)


def test_full_requires_sparse_and_profile_mismatch(sample, cfg):
    _, catalog, storage = sample
    build_index(catalog, storage, cfg, reporter=lambda _: None)
    with pytest.raises(DocBaseError, match='模式'):
        query('query', storage, cfg, profile='hybrid')
    with pytest.raises(DocBaseError, match='稀疏'):
        build_index(catalog, storage, cfg, profile='full', reporter=lambda _: None)


def test_evaluation_unique_evidence_and_unanswerable(sample, cfg, tmp_path):
    _, catalog, storage = sample
    build_index(catalog, storage, cfg, reporter=lambda _: None)
    dataset = tmp_path / 'eval.jsonl'
    questions = [{'qid': 'q1', 'question': 'version 1729', 'answerable': True,
                  'relevant': [{'doc_id': 'doc_a', 'quote': '1729'}]},
                 {'qid': 'q2', 'question': 'no matching xyz', 'answerable': False, 'relevant': []}]
    dataset.write_text(''.join(json.dumps(q) + '\n' for q in questions), encoding='utf-8')
    report = eval_retrieval(dataset, storage, cfg)
    assert report['metrics'] == {'Hit@5': 1, 'Recall@5': 1, 'MRR@5': 1}
    assert report['answerable_count'] == 1 and report['unanswerable_count'] == 1


@pytest.mark.parametrize('key', ['test-placeholder', '"test-placeholder"', "'test-placeholder'"])
def test_env_quotes(tmp_path, monkeypatch, key):
    monkeypatch.delenv('DOCBASE_EMBED_API_KEY', raising=False)
    env = tmp_path / '.env'
    env.write_text(f'DOCBASE_EMBED_API_KEY={key}\n', encoding='utf-8')
    assert Settings.load(env).embed_api_key == 'test-placeholder'


def test_quota_error_is_safe_and_actionable(tmp_path, cfg):
    def response(req):
        return httpx.Response(400, json={'error': {'message': '当前账户没有可用的计费资源 test-secret'}})
    client = mock_client(cfg, tmp_path, response)
    with pytest.raises(DocBaseError) as exc:
        client.embeddings(['non-private sample'])
    assert exc.value.code == 'MODEL_QUOTA'
    assert 'test-secret' not in str(exc.value)


def test_identical_passages_preserve_all_sources(tmp_path, cfg):
    path = create_artifact(tmp_path / 'parsed', text=['Version 1729.', 'Version 1729.'])
    catalog, storage = tmp_path / 'catalog.json', tmp_path / 'storage'
    scan_artifacts(path, catalog)
    build_index(catalog, storage, cfg, reporter=lambda _: None)
    result = query('1729', storage, cfg, top_k=1)
    assert len(result['results']) == 1
    assert len(result['results'][0]['sources']) == 2
    assert len(result['results'][0]['equivalent_chunk_ids']) == 2


def test_trimmed_text_retains_original_offsets(tmp_path, cfg):
    artifact = create_artifact(tmp_path, text=['   Version 1729.   '])
    chunks = make_chunks([inspect_artifact(artifact)], cfg)
    region = chunks[0]['block_ranges'][0]
    assert region['start'] == 3
    assert '   Version 1729.   '[region['start']:region['end']] == chunks[0]['text']


def test_explicit_selection_supports_service_workflow(tmp_path, cfg):
    from docbase.ingest import select_artifact
    first = create_artifact(tmp_path / 'old', doc_id='doc_a', text=['OLD VERSION.'])
    second = create_artifact(tmp_path / 'new', doc_id='doc_a', text=['NEW VERSION.'])
    catalog, storage = tmp_path / 'catalog.json', tmp_path / 'storage'
    select_artifact(first, catalog)
    build_index(catalog, storage, cfg, reporter=lambda _: None)
    select_artifact(second, catalog)
    active = [e for e in read_json(catalog)['entries'] if e['active']]
    assert len(active) == 1 and active[0]['artifact_path'] == str(second.resolve())
    build_index(catalog, storage, cfg, reporter=lambda _: None)
    assert query('NEW', storage, cfg)['results']
    assert not query('OLD', storage, cfg)['results']
