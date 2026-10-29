"""Sample Zotero PDFs once, then import offline through isolated project environments."""
import argparse
import hashlib
import json
import random
import secrets
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(r'C:\Users\Lenovo\Zotero\storage'))
    parser.add_argument('--batch', default='zotero_third_20261028')
    args = parser.parse_args()
    if not args.batch.replace('_', '').isalnum():
        raise ValueError('Invalid batch ID')
    batch = ROOT / 'imports' / args.batch
    batch.mkdir(parents=True, exist_ok=True)
    selection_file = batch / 'selection.json'
    if selection_file.exists():
        selection = json.loads(selection_file.read_text(encoding='utf-8'))
    else:
        source = args.source.resolve()
        files = sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.pdf')
        if len(files) < 3:
            raise ValueError('Need at least three PDFs')
        seed = secrets.randbits(64)
        selected = random.Random(seed).sample(files, len(files) // 3)
        selection = {'source': str(source), 'created_at': datetime.now().astimezone().isoformat(),
                     'population_count': len(files), 'selected_count': len(selected), 'seed': seed,
                     'rule': 'random sample without replacement, floor(file_count/3)',
                     'files': [{'path': str(p), 'sha256': sha(p), 'bytes': p.stat().st_size} for p in selected]}
        save(selection_file, selection)
        (batch / 'selected_files.txt').write_text('\n'.join(x['path'] for x in selection['files'])+'\n', encoding='utf-8')
    output = ROOT / 'doc2md' / 'output' / args.batch
    catalog = ROOT / 'DocBase' / 'storage' / ('catalog_' + args.batch + '.json')
    report_file = batch / 'import_report.json'
    report = json.loads(report_file.read_text(encoding='utf-8')) if report_file.exists() else {'results': []}
    done = {r['source']: r for r in report['results'] if r['status'] in ('ok', 'partial')}
    results = []
    for i, item in enumerate(selection['files'], 1):
        source = Path(item['path'])
        print(f'[{i}/{len(selection["files"])}] {source.name}', flush=True)
        if source.is_file() and sha(source) == item['sha256'] and str(source) in done and Path(done[str(source)].get('manifest', '')).is_file():
            result = done[str(source)]
            print('  reused', flush=True)
        elif not source.is_file() or sha(source) != item['sha256']:
            result = {'source': str(source), 'status': 'failed', 'error': 'Source missing or changed since selection'}
        else:
            command = [str(ROOT/'doc2md/.conda/python.exe'), '-m', 'doc2md', 'parse', str(source),
                       '--output', str(output), '--offline', '--category', 'Zotero论文']
            p = subprocess.run(command, cwd=ROOT/'doc2md', capture_output=True)
            (batch / f'parse_{i:02}.log').write_bytes(p.stdout + b'\n' + p.stderr)
            if p.returncode in (0, 2):
                parsed = json.loads((output/'batch_report.json').read_text(encoding='utf-8'))
                result = parsed['results'][0]
            else:
                result = {'source': str(source), 'status': 'failed', 'error': f'Parser exit {p.returncode}; see parse_{i:02}.log'}
            print('  '+result['status'], flush=True)
        results.append(result)
        report = {'selection': str(selection_file), 'offline': True, 'profile': 'bm25-only',
                  'collection': args.batch, 'parsed_output': str(output), 'catalog': str(catalog), 'results': results}
        save(report_file, report)
    commands = [
        ['scan', '--input', str(output), '--catalog', str(catalog)],
        ['build', '--catalog', str(catalog), '--collection', args.batch, '--profile', 'bm25-only', '--incremental'],
        ['query', '--collection', args.batch, '--profile', 'bm25-only', '--question', 'vision language model', '--top-k', '3'],
    ]
    stages = []
    for number, command in enumerate(commands):
        print('DocBase '+command[0], flush=True)
        p = subprocess.run([str(ROOT/'DocBase/.conda/python.exe'), '-m', 'docbase', *command], cwd=ROOT/'DocBase', capture_output=True)
        (batch / f'docbase_{command[0]}.log').write_bytes(p.stdout + b'\n' + p.stderr)
        try:
            data = json.loads(p.stdout.decode('utf-8-sig'))
        except ValueError:
            data = {'status': 'failed', 'error': 'Invalid JSON; see log'}
        stages.append({'command': command[0], 'exit_code': p.returncode, 'result': data})
        report['stages'] = stages
        save(report_file, report)
        if p.returncode not in (0, 2):
            raise RuntimeError('DocBase stage failed; see import report')
    build = stages[1]['result']
    report['summary'] = {'selected': selection['selected_count'],
                         'parsed_ok': sum(r['status']=='ok' for r in results),
                         'parsed_partial': sum(r['status']=='partial' for r in results),
                         'failed': sum(r['status']=='failed' for r in results),
                         'indexed_documents': build.get('document_count'), 'chunks': build.get('chunk_count'),
                         'index_version': build['index_version']}
    save(report_file, report)
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
