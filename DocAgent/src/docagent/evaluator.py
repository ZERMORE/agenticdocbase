import json
import statistics
from pathlib import Path
import uuid
from .common import AgentError, write_json


def evaluate(runner, dataset, **options):
    rows = [json.loads(line) for line in Path(dataset).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if not rows or any(not isinstance(x.get('question'), str) or x.get('answerability') not in {'answerable', 'unanswerable'} for x in rows):
        raise AgentError('DATASET', '题集需有question与answerability（answerable/unanswerable）')
    results = []
    for i, row in enumerate(rows):
        answer = runner.ask(row['question'], **options)
        results.append({'id': row.get('id', i), 'answerability': row['answerability'],
                        'status': answer['status'], 'answer_json_path': answer['answer_json_path'],
                        'seconds': answer['timings']['total_seconds'], 'usage': answer['usage'],
                        'human_correct': None, 'human_citation_support': None,
                        'gold': {k: v for k, v in row.items() if k != 'question'}})
    times = sorted(r['seconds'] for r in results)
    report = {'schema_version': '1', 'results': results,
              'summary': {'count': len(results), 'answered': sum(r['status'] == 'answered' for r in results),
                          'failed': sum(r['status'] == 'failed' for r in results),
                          'p50_seconds': statistics.median(times),
                          'p95_seconds': times[min(len(times) - 1, int(len(times) * .95))]},
              'note': '答案正确率及引用语义支持率须人工标注；answered数量不能替代正确率。'}
    path = runner.storage / 'evaluations' / ('eval_' + uuid.uuid4().hex[:16] + '.json')
    report['report_path'] = str(path.resolve())
    write_json(path, report)
    return report
