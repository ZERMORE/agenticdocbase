"""Real local retrieval with an explicitly simulated model; never makes HTTP calls."""
import json
from pathlib import Path
import subprocess
import time

from docagent.config import ROOT, Settings
from docagent.common import write_json
from docagent.docbase_adapter import DocBaseAdapter
from docagent.runner import AgentRunner


class SyntheticModel:
    stats = {'requests': 0, 'usage_records': [], 'mode': 'mock-no-network'}

    def complete(self, messages, tools, deadline):
        self.stats['requests'] += 1
        data = json.loads(messages[-1]['content'].split('\n', 1)[1])
        evidence = next(e for e in data['results'] if '1729' in e['text'])
        submission = {'status': 'answered', 'claims': [{'text': '测试文档中的版本号为1729。', 'kind': 'direct',
            'supports': [{'evidence_id': evidence['evidence_id'], 'quote': '1729'}]}], 'limitations': ['本次模型生成经过模拟，仅验证本地工作流。']}
        return {'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'synthetic_submit_1', 'type': 'function', 'function': {'name': 'submit_answer',
            'arguments': json.dumps(submission, ensure_ascii=False)}}]}, 'model': 'MOCK-not-live-deepseek'}


def main():
    cfg = Settings.load()
    storage = ROOT / 'storage' / 'integration'
    storage.mkdir(parents=True, exist_ok=True)
    collection = 'zotero_third_20261028'
    adapter = DocBaseAdapter(cfg, collection, 'bm25-only', time.monotonic() + 180)
    adapter.pin()
    # Baseline uses the old retrieval order and index, preserved in the report.
    baseline = storage / 'retrieval_before.json'
    if baseline.is_file():
        before = json.loads(baseline.read_text(encoding='utf-8'))
    else:
        before = adapter.search('vision language model', 5, prefer_body=False)
        write_json(baseline, before)
    base = cfg.base_root
    process = subprocess.run([str(base / '.conda' / 'python.exe'), '-m', 'docbase', 'build',
        '--env-file', str(ROOT / 'configs' / 'docbase-context.env'), '--collection', collection,
        '--catalog', str(base / 'storage' / 'catalog_zotero_third_20261028.json'), '--incremental'],
        cwd=base, shell=False, capture_output=True, text=True, encoding='utf-8', errors='replace')
    (storage / 'build.log').write_text(process.stderr, encoding='utf-8')
    build = json.loads(process.stdout)
    write_json(storage / 'build.json', build)
    if process.returncode not in (0, 2):
        raise RuntimeError('DocBase build failed; see integration/build.json')
    fresh = DocBaseAdapter(cfg, collection, 'bm25-only', time.monotonic() + 180)
    fresh.pin()
    after = fresh.search('vision language model', 5)
    write_json(storage / 'retrieval_after.json', after)
    context = fresh.context(after['results'][0]['chunk_id'])
    write_json(storage / 'context.json', context)
    smoke = AgentRunner(cfg, client=SyntheticModel()).ask('测试文档中的版本号1729是什么？', collection='default')
    summary = {'mode': 'local-real-retrieval-with-mock-generation', 'api_requests': 0,
        'before_index': before['index_version'], 'after_index': after['index_version'],
        'build': build, 'context_blocks': len(context), 'smoke_status': smoke['status'],
        'smoke_answer_md': smoke['answer_md_path'],
        'before_top5': [{'text': r['text'][:500], 'heading': r['heading_path']} for r in before['results']],
        'after_top5': [{'text': r['text'][:500], 'heading': r['heading_path'],
                       'ranges': len(r['block_ranges'])} for r in after['results']],
        'note': '此查询仅作故障样例回归，不能替代人工冻结题集；真实DeepSeek尚未联调。'}
    write_json(storage / 'summary.json', summary)
    print(json.dumps({'status': smoke['status'], 'report': str(storage / 'summary.json'),
                      'index_version': after['index_version'], 'api_requests': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
