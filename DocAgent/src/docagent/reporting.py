from pathlib import Path
import re
from .common import write_json, sanitize


def render(result):
    lines = [f"# {result['question']}", '', f"状态：{result['status']}", '']
    if result.get('error'):
        lines += [f"{result.get('code', 'ERROR')}：{result['error']}", '']
    for claim in result.get('claims', []):
        prefix = '推断：' if claim['kind'] == 'inference' else ''
        refs = list(dict.fromkeys(s['evidence_id'] for s in claim['supports']))
        lines += [prefix + claim['text'] + ' ' + ' '.join(f'[{e}]' for e in refs), '']
    if result.get('limitations'):
        lines += ['## 限制与缺口', ''] + ['- ' + x for x in result['limitations']] + ['']
    if result.get('mermaid'):
        # Prevent generated fences from escaping the display block.
        diagram = result['mermaid'].replace('```', '')
        lines += ['## 流程图', '', '以下为未经过自动渲染/原图核验的草稿。', '', '```mermaid', diagram, '```', '']
    lines += ['## 来源', '']
    seen = set()
    for citation in result.get('citations', []):
        eid = citation['evidence_id']
        if eid in seen:
            continue
        seen.add(eid)
        for source in citation['sources']:
            path = source.get('snapshot_path_absolute') or source.get('rendered_pdf_path_absolute')
            label = Path(source.get('original_path', path)).name
            label = re.sub(r'[\[\]\r\n]', '_', label)
            page = source.get('page_index')
            link = Path(path).as_uri() + (f'#page={page}' if page and path.lower().endswith('.pdf') else '')
            lines += [f'- [{eid}] [{label}]({link})；PDF文件页序号：{page if page else "未提供"}']
        lines += ['']
    return '\n'.join(lines)


def save_run(directory, result, trace, ledger, policy, secret):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = sanitize(result, secret)
    result.update(answer_md_path=str((directory / 'answer.md').resolve()),
                  answer_json_path=str((directory / 'answer.json').resolve()),
                  trace_path=str((directory / 'trace.jsonl').resolve()))
    result['answer'] = render(result)
    write_json(directory / 'answer.json', result)
    write_json(directory / 'evidence.json', sanitize(ledger.records, secret))
    (directory / 'answer.md').write_text(result['answer'], encoding='utf-8')
    (directory / 'policy_snapshot.md').write_text(sanitize(policy, secret), encoding='utf-8')
    import json
    (directory / 'trace.jsonl').write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n'
                for x in sanitize(trace, secret)), encoding='utf-8')
    return result
