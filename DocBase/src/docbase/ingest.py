from __future__ import annotations

import json
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from .common import DocBaseError, digest, file_hash, inside, read_json, write_json

ALLOWED = {'heading', 'paragraph', 'table', 'ocr_text', 'vision_transcription', 'figure_description'}


def inspect_artifact(path: Path, strict=False):
    path = Path(path).resolve()
    manifest_path, blocks_path = path / 'manifest.json', path / 'blocks.jsonl'
    try:
        before = (file_hash(manifest_path), file_hash(blocks_path))
        manifest = read_json(manifest_path)
        if manifest.get('schema_version') != '1':
            raise DocBaseError('SCHEMA', '不支持的 doc2md schema_version')
        if manifest.get('status') not in {'ok', 'partial'}:
            raise DocBaseError('ARTIFACT_QUALITY', 'doc2md 产物不是已发布 ok/partial 状态')
        if strict and manifest['status'] != 'ok':
            raise DocBaseError('ARTIFACT_QUALITY', 'strict 模式不接受 partial 文档')
        doc_id = manifest['doc_id']
        # Current doc2md layout: output/doc_id/revision/config. Avoid an active writer.
        if len(path.parents) > 2 and (path.parents[2] / (doc_id + '.lock')).exists():
            raise DocBaseError('ARTIFACT_BUSY', 'doc2md 正在重写该文档产物')
        blocks = [json.loads(line) for line in blocks_path.read_text(encoding='utf-8').splitlines() if line.strip()]
        if manifest.get('blocks_sha256') and manifest['blocks_sha256'] != before[1]:
            raise DocBaseError('ARTIFACT_INCOMPLETE', '内容块哈希与manifest不一致')
        if len(blocks) != manifest['block_count']:
            raise DocBaseError('ARTIFACT_INCOMPLETE', '内容块数与 manifest 不一致')
        seen = set()
        source_paths = {manifest['source_snapshot']}
        if manifest.get('rendered_pdf'):
            source_paths.add(manifest['rendered_pdf'])
        for block in blocks:
            if block.get('doc_id') != doc_id or block.get('revision') != manifest['revision'] or block.get('schema_version') != '1':
                raise DocBaseError('ARTIFACT_INCOMPLETE', '内容块文档/版本/schema 与 manifest 不一致')
            if block['block_id'] in seen or not isinstance(block.get('text'), str):
                raise DocBaseError('ARTIFACT_INCOMPLETE', '内容块 ID 重复或正文无效')
            seen.add(block['block_id'])
            for field in ('snapshot_path', 'rendered_pdf_path', 'image_path'):
                if value := block.get('source', {}).get(field):
                    source_paths.add(value)
        hashes = {}
        for relative in source_paths:
            local = inside(path, relative)
            if not local.is_file():
                raise DocBaseError('SOURCE_MISSING', '产物引用的来源快照/图片缺失')
            hashes[relative] = file_hash(local)
        if hashes[manifest['source_snapshot']] != manifest['revision']:
            raise DocBaseError('SOURCE_CHANGED', '原文快照哈希与 revision 不一致')
        after = (file_hash(manifest_path), file_hash(blocks_path))
        if before != after:
            raise DocBaseError('ARTIFACT_BUSY', '读取时产物发生变化，请重试')
        artifact_id = digest([doc_id, manifest['revision'], manifest['config_fingerprint'], before[1]])
        bundle_id = digest([artifact_id, hashes, before[0]])
        valid = [b for b in blocks if b.get('status') == 'ok' and b.get('type') in ALLOWED
                 and b['text'].strip() and b['text'].strip() != '[空白页]']
        return {'path': str(path), 'artifact_id': artifact_id, 'bundle_id': bundle_id,
                'manifest': manifest, 'manifest_hash': before[0], 'blocks_hash': before[1],
                'source_hashes': hashes, 'blocks': valid, 'excluded_blocks': len(blocks) - len(valid)}
    except DocBaseError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DocBaseError('ARTIFACT_INVALID', '产物文件不可读，或结构不符合 doc2md 契约') from exc


def scan_artifacts(input_dir: Path, catalog_path: Path):
    root = Path(input_dir).resolve()
    catalog_path = Path(catalog_path).resolve()
    if not root.is_dir():
        raise DocBaseError('INPUT', '输入目录不存在')
    manifests = [root / 'manifest.json'] if (root / 'manifest.json').is_file() else sorted(root.rglob('manifest.json'))
    groups, rejected = defaultdict(list), []
    for path in manifests:
        if any(part.startswith(('.work-', '.previous-')) or '.previous-' in part or part in {'.conda', '.cache', '.venv'} for part in path.relative_to(root).parts):
            continue
        try:
            artifact = inspect_artifact(path.parent)
            m = artifact['manifest']
            if not artifact['blocks']:
                raise DocBaseError('NO_CONTENT', '没有有效可检索内容块')
            groups[m['doc_id']].append({'doc_id': m['doc_id'], 'revision': m['revision'],
                                      'artifact_path': str(path.parent), 'artifact_id': artifact['artifact_id'],
                                      'status': m['status'], 'name': Path(m['source_path']).name, 'active': False})
        except DocBaseError as exc:
            rejected.append({'path': str(path.parent), 'code': exc.code, 'error': str(exc)})
    old = read_json(catalog_path) if catalog_path.exists() else {'entries': []}
    old_by_doc = defaultdict(list)
    for entry in old.get('entries', []):
        old_by_doc[entry['doc_id']].append(entry)
    entries = []
    conflicts = []
    for doc_id, candidates in sorted(groups.items()):
        # Same artifact in multiple roots is a duplicate, not a new document version.
        unique = {}
        for item in candidates:
            unique.setdefault(item['artifact_id'], item)
        candidates = list(unique.values())
        prior = {str(Path(e['artifact_path']).resolve()): e for e in old_by_doc[doc_id]}
        for item in candidates:
            if item['artifact_path'] in prior:
                previous = prior[item['artifact_path']]
                item['active'] = previous.get('active', False)
                if 'category' in previous:
                    item['category'] = previous['category']
            elif len(candidates) == 1 and not prior:
                item['active'] = True
        if len(candidates) > 1 and sum(e['active'] for e in candidates) != 1:
            for item in candidates:
                item['active'] = False
            conflicts.append(doc_id)
        entries.extend(candidates)
    # Do not automatically deactivate sources just because a scan was narrower or offline.
    found_paths = {e['artifact_path'] for e in entries}
    for old_entry in old.get('entries', []):
        if str(Path(old_entry['artifact_path']).resolve()) not in found_paths:
            entries.append(old_entry)
    catalog = {'schema_version': '1', 'entries': entries}
    write_json(catalog_path, catalog)
    return {'status': 'partial' if rejected or conflicts else 'ok', 'catalog': str(catalog_path),
            'discovered': sum(len(v) for v in groups.values()), 'active': sum(bool(e.get('active')) for e in entries),
            'conflicts': conflicts, 'rejected': rejected,
            'note': '有版本冲突时编辑 catalog，仅为每个 doc_id 启用一个版本；扫描不会隐式删除旧登记'}


def load_catalog(path: Path, quality='usable', allow_empty=False):
    try:
        value = read_json(Path(path))
        if value.get('schema_version') != '1' or not isinstance(value['entries'], list):
            raise ValueError()
        active = [item for item in value['entries'] if item.get('active') is True]
        if not active and not allow_empty:
            raise DocBaseError('EMPTY_CATALOG', 'catalog 没有启用的文档；先 scan 并检查 active 字段')
        if len({item['doc_id'] for item in active}) != len(active):
            raise DocBaseError('VERSION_CONFLICT', '同一 doc_id 只能启用一个解析产物')
        artifacts = []
        for item in active:
            artifact_path = Path(item['artifact_path'])
            if not artifact_path.is_absolute():
                artifact_path = Path(path).resolve().parent / artifact_path
            artifact = inspect_artifact(artifact_path, strict=quality == 'strict')
            artifact['category_override'] = item.get('category')
            artifacts.append(artifact)
        return artifacts
    except DocBaseError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise DocBaseError('CATALOG', 'catalog 文件不可读或结构错误') from exc


def select_artifact(artifact_path: Path, catalog_path: Path):
    """Explicitly activate a published artifact, disabling other versions of its doc_id."""
    artifact = inspect_artifact(artifact_path)
    if not artifact['blocks']:
        raise DocBaseError('NO_CONTENT', '所选产物没有有效知识块')
    catalog_path = Path(catalog_path).resolve()
    catalog = read_json(catalog_path) if catalog_path.exists() else {'schema_version': '1', 'entries': []}
    doc_id = artifact['manifest']['doc_id']
    selected = None
    for entry in catalog['entries']:
        if entry['doc_id'] == doc_id:
            entry['active'] = False
        if str(Path(entry['artifact_path']).resolve()) == artifact['path']:
            selected = entry
    if selected is None:
        selected = {'doc_id': doc_id, 'artifact_path': artifact['path']}
        catalog['entries'].append(selected)
    selected.update(artifact_id=artifact['artifact_id'], revision=artifact['manifest']['revision'],
                    status=artifact['manifest']['status'], active=True,
                    name=Path(artifact['manifest']['source_path']).name)
    write_json(catalog_path, catalog)
    return {'status': 'ok', 'catalog': str(catalog_path), 'selected': selected,
            'note': '只切换此 doc_id 的当前产物，其他文档登记不变'}


def save_source_bundle(artifact, storage: Path):
    store = storage / 'source_store'
    store.mkdir(parents=True, exist_ok=True)
    target = store / artifact['bundle_id']
    if target.exists():
        for relative, expected in artifact['source_hashes'].items():
            if not inside(target, relative).is_file() or file_hash(inside(target, relative)) != expected:
                raise DocBaseError('SOURCE_CORRUPT', '内容寻址的来源快照已损坏')
        return
    origin = Path(artifact['path'])
    with tempfile.TemporaryDirectory(prefix='.bundle-', dir=store) as tmp:
        temporary = Path(tmp)
        for relative, expected in artifact['source_hashes'].items():
            destination = inside(temporary, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(inside(origin, relative), destination)
            if file_hash(destination) != expected:
                raise DocBaseError('SOURCE_CHANGED', '复制时来源发生变化')
        for filename, expected in [('manifest.json', artifact['manifest_hash']), ('blocks.jsonl', artifact['blocks_hash'])]:
            shutil.copyfile(origin / filename, temporary / filename)
            if file_hash(temporary / filename) != expected:
                raise DocBaseError('SOURCE_CHANGED', '复制时解析产物发生变化')
        if (origin / 'document.md').is_file():
            shutil.copyfile(origin / 'document.md', temporary / 'document.md')
        temporary.rename(target)
