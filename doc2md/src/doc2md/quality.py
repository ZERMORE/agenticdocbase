"""Measurable parser facts and explicitly heuristic risks; never an accuracy score."""
from collections import Counter
import hashlib
import json
from pathlib import Path


def report(blocks, manifest):
    pages = []
    issues = []
    count = manifest.get('page_count')
    numbers = list(range(1, count + 1)) if isinstance(count, int) else sorted({
        b['source']['page_index'] for b in blocks if b.get('source', {}).get('page_index')})
    for number in numbers:
        rows = [b for b in blocks if b.get('source', {}).get('page_index') == number]
        text = '\n'.join(b.get('text', '') for b in rows if b['type'] != 'image' and b.get('status') == 'ok')
        pending = [b['block_id'] for b in rows if b.get('status') != 'ok']
        page = {'page_index': number - 1, 'display_page': number, 'block_count': len(rows),
                'text_chars': len(text), 'pending_blocks': len(pending),
                'images': sum(b['type'] == 'image' for b in rows),
                'tables': sum(b['type'] == 'table' for b in rows)}
        pages.append(page)
        if not text.strip() or text.strip() == '[空白页]':
            issues.append({'code': 'blank_candidate', 'heuristic': True, 'page_index': number - 1,
                           'message': '空白/低文字候选，可能是正常空页或扫描页，请对照原文。'})
        elif len(text.strip()) < 30:
            issues.append({'code': 'low_text', 'heuristic': True, 'page_index': number - 1,
                           'message': '本页文字较少，不代表解析错误。'})
        if pending:
            issues.append({'code': 'pending_content', 'heuristic': False, 'page_index': number - 1,
                           'block_ids': pending, 'message': '存在未完成的图像或文字区域。'})
    for block in blocks:
        if '\ufffd' in block.get('text', ''):
            issues.append({'code': 'replacement_character', 'heuristic': True,
                           'page_index': (block.get('source', {}).get('page_index') or 1) - 1,
                           'block_ids': [block['block_id']], 'message': '发现替换字符，建议核对原文。'})
    return {'schema_version': 'quality-v1', 'status': manifest.get('status'),
            'doc_id': manifest['doc_id'], 'source_revision': manifest['revision'],
            'artifact_revision': hashlib.sha256(json.dumps([manifest,blocks],ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()[:24],
            'config_fingerprint': manifest.get('config_fingerprint'),
            'tool_version': manifest.get('tool_version'), 'mode': manifest.get('mode'),
            'page_count': count, 'block_count': len(blocks),
            'pending_blocks': sum(b.get('status') != 'ok' for b in blocks),
            'types': dict(Counter(b['type'] for b in blocks)), 'pages': pages, 'issues': issues,
            'warnings': manifest.get('warnings', []),
            'note': '事实统计与启发式风险分别列出；不表示文字识别准确率。'}


def read_report(artifact):
    root = Path(artifact)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    blocks = [json.loads(x) for x in (root / 'blocks.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    return report(blocks, manifest)
