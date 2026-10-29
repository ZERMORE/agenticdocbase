from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .common import DocBaseError, write_json
from .engine import query


def matches(result, evidence):
    if evidence.get('quote') and evidence['quote'] not in result['text']:
        return False
    for source in result['sources']:
        if any(evidence.get(field) is not None and source.get(field) != evidence[field]
               for field in ('doc_id', 'revision', 'page_index', 'slide_number', 'figure_id', 'source_block_id')):
            continue
        if 'start' in evidence or 'end' in evidence:
            covered = any(r['block_id'] == evidence.get('source_block_id') and r['start'] <= evidence['start']
                          and r['end'] >= evidence['end'] for r in result['block_ranges'])
            if not covered:
                continue
        return True
    return False


def eval_retrieval(dataset, storage, settings, *, top_k=5, collection='default', index_version=None,
                   profile=None, client=None, reporter=None):
    try:
        questions = [json.loads(line) for line in Path(dataset).read_text(encoding='utf-8').splitlines() if line.strip()]
        if not questions or len({q['qid'] for q in questions}) != len(questions):
            raise ValueError()
        for q in questions:
            if not isinstance(q['question'], str) or not isinstance(q['answerable'], bool):
                raise ValueError()
            if q['answerable'] and not q.get('relevant'):
                raise ValueError()
            for evidence in q.get('relevant', []):
                if not evidence.get('doc_id') or not (evidence.get('quote') or all(k in evidence for k in ('source_block_id', 'start', 'end'))):
                    raise ValueError()
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise DocBaseError('DATASET', '题集需为 JSONL，含唯一 qid/question/answerable；相关证据需 doc_id 和 quote 或源块范围') from exc
    records = []
    fixed_version = index_version
    for number, q in enumerate(questions, 1):
        if reporter:
            reporter(f'评测 {number}/{len(questions)}: {q["qid"]}')
        response = query(q['question'], storage, settings, top_k=top_k, collection=collection,
                         index_version=fixed_version, category=q.get('category'), profile=profile, client=client)
        fixed_version = response['index_version']
        relevant = q.get('relevant', [])
        hits = [[i for i, evidence in enumerate(relevant) if matches(result, evidence)] for result in response['results']]
        covered = set(i for group in hits for i in group)
        first = next((rank for rank, group in enumerate(hits, 1) if group), None)
        metrics = {'hit': int(first is not None), 'recall': len(covered) / len(relevant),
                   'rr': 1 / first if first else 0} if q['answerable'] else None
        records.append({'qid': q['qid'], 'answerable': q['answerable'], 'metrics': metrics,
                        'query_id': response['query_id'], 'index_version': fixed_version,
                        'ranked_chunk_ids': [r['chunk_id'] for r in response['results']],
                        'evidence_hits': hits, 'diagnostics': response['diagnostics']})
    scored = [record['metrics'] for record in records if record['metrics'] is not None]
    metrics = {f'Hit@{top_k}': sum(m['hit'] for m in scored) / len(scored) if scored else None,
               f'Recall@{top_k}': sum(m['recall'] for m in scored) / len(scored) if scored else None,
               f'MRR@{top_k}': sum(m['rr'] for m in scored) / len(scored) if scored else None}
    report = {'schema_version': '1', 'status': 'ok', 'index_version': fixed_version, 'top_k': top_k,
              'answerable_count': len(scored), 'unanswerable_count': len(questions) - len(scored),
              'metrics': metrics, 'records': records,
              'note': '无答案题仅记录检索结果，不据此计算 Agent 拒答率；微型样例指标不代表真实语料质量'}
    path = Path(storage) / 'reports' / ('eval_' + uuid.uuid4().hex[:16] + '.json')
    report['report_path'] = str(path.resolve())
    write_json(path, report)
    return report
