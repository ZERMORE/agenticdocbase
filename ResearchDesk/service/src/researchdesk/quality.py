"""Legacy-safe, read-only quality inspection and revision job publication."""
import json
from pathlib import Path
from .store import atomic, WORKSPACE


def inspect(artifact):
    # Do not mutate old content-addressed artifacts just to add a report.
    root = Path(artifact)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    blocks = [json.loads(x) for x in (root / 'blocks.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    report_path = root / 'quality.json'
    if report_path.is_file():
        quality = json.loads(report_path.read_text(encoding='utf-8'))
    else:
        # Single implementation shared as pure code; no runtime dependency/environment merge.
        import importlib.util
        spec = importlib.util.spec_from_file_location('_doc2md_quality', WORKSPACE / 'doc2md/src/doc2md/quality.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        quality = module.report(blocks, manifest)
    import hashlib
    quality['artifact_revision'] = hashlib.sha256(json.dumps([manifest,blocks],ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()[:24]
    quality['blocks'] = [{'block_id': b['block_id'], 'type': b['type'], 'text': b['text'],
                          'status': b['status'], 'page_index': (b['source'].get('page_index') or 1) - 1,
                          'has_page': b['source'].get('page_index') is not None,
                          'bbox': b['source'].get('bbox')} for b in blocks]
    quality['has_parent'] = bool(manifest.get('parent_artifact'))
    return quality


def revise_job(s, kb, payload, folder, call, check, stage):
    member = s.one('SELECT * FROM members WHERE kb=? AND doc=? AND active=1', (kb['id'], payload['doc']))
    if not member or member['artifact'] != payload['artifact']:
        raise RuntimeError('VERSION_CONFLICT: 文档已更新或移出，请刷新后重试')
    request = {k: v for k, v in payload.items() if k != 'doc'}
    if not request.get('offline', True) and not kb['external_enabled']:
        raise RuntimeError('MODEL_AUTH: 此知识库未启用外部模型处理')
    atomic(folder / 'revision-request.json', request)
    stage('revising')
    result = call('doc2md', 'doc2md.revisions', ['--request-file', folder / 'revision-request.json',
                   '--output', s.data / 'parsed' / folder.name], 'revise', 3600)
    check()
    artifact = Path(result['output'])
    manifest = json.loads((artifact / 'manifest.json').read_text(encoding='utf-8'))
    with s.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        changed = c.execute("UPDATE members SET artifact=?,quality=?,status='indexing' WHERE kb=? AND doc=? AND active=1 AND artifact=?",
                            (str(artifact), manifest['status'], kb['id'], payload['doc'], payload['artifact'])).rowcount
        if not changed:
            raise RuntimeError('VERSION_CONFLICT: 修订期间资料版本发生变化')
        c.execute('INSERT INTO document_revisions(kb,doc,artifact,parent,action,reason,created) VALUES(?,?,?,?,?,?,?)',
                  (kb['id'], payload['doc'], str(artifact), payload['artifact'], request['action'], request['reason'], __import__('time').time()))
        c.execute('UPDATE kbs SET generation=generation+1 WHERE id=?', (kb['id'],))
    s.enqueue(kb['id'], 'reindex', {})
    return {'document_id': payload['doc'], 'artifact_revision': manifest['config_fingerprint'], 'quality': manifest['status']}
