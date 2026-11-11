import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time
from .common import AgentError, sanitize
from .config import ROOT, Settings
from .client import DeepSeekClient
from .docbase_adapter import DocBaseAdapter
from .policy import load_policy
from .runner import AgentRunner


def doctor(cfg):
    packages = {}
    for name in ('httpx', 'python-dotenv', 'pydantic'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = 'missing'
    _, digest, policy_path = load_policy()
    base_python = cfg.base_root / '.conda' / 'python.exe'
    return {'status': 'ok' if base_python.is_file() and 'missing' not in packages.values() else 'failed',
            'python': sys.version.split()[0], 'executable': sys.executable, 'project': str(ROOT),
            'packages': packages, 'model': cfg.model, 'base_url': cfg.base_url,
            'key_configured': bool(cfg.api_key.strip()), 'policy_path': policy_path, 'policy_sha256': digest,
            'docbase_python': str(base_python), 'docbase_python_exists': base_python.is_file(),
            'collection': cfg.collection, 'profile': cfg.retrieval_profile,
            'note': 'doctor不调用模型；Key留空时可检查本地环境，实际问答需要填写Key。'}


def probe(cfg):
    client = DeepSeekClient(cfg)
    deadline = time.monotonic() + cfg.total_timeout_seconds
    tools = [{'type': 'function', 'function': {'name': 'probe_echo', 'description': '返回合成测试值',
              'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}}}]
    messages = [{'role': 'user', 'content': 'API兼容性测试。必须调用probe_echo，value为1729，不要直接回答。'}]
    try:
        first = client.complete(messages, tools, deadline)
        calls = first['message'].get('tool_calls') or []
        if len(calls) != 1 or calls[0]['function']['name'] != 'probe_echo' or json.loads(calls[0]['function']['arguments']) != {'value': '1729'}:
            raise AgentError('MODEL_PROTOCOL', '模型未按要求生成测试工具调用')
        messages += [first['message'], {'role': 'tool', 'tool_call_id': calls[0]['id'], 'content': '{"value":"1729"}'},
                     {'role': 'user', 'content': '工具执行完毕，请仅回答工具返回的值，不再调用工具。'}]
        second = client.complete(messages, tools, deadline)
        if '1729' not in (second['message'].get('content') or ''):
            raise AgentError('MODEL_PROTOCOL', '工具结果返回后未获得预期回答')
        return {'status': 'ok', 'model_requested': cfg.model, 'model_returned': second.get('model'),
                'tool_roundtrip': True, 'usage': client.stats, 'note': '仅使用合成数据；未检索论文，也未验证图像输入。'}
    finally:
        client.close()


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='DocAgent：带证据引用的本地论文问答')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'probe', 'ask', 'evaluate', 'inspect'):
        p = sub.add_parser(name)
        p.add_argument('--env-file', type=Path)
        if name in ('ask', 'evaluate', 'inspect'):
            p.add_argument('--collection')
            p.add_argument('--profile', choices=['bm25-only', 'hybrid', 'full'])
            p.add_argument('--index-version')
            p.add_argument('--category')
        if name in ('ask', 'inspect'):
            p.add_argument('--question', required=True)
        if name in ('ask', 'evaluate'):
            p.add_argument('--storage', type=Path, default=ROOT / 'storage')
            p.add_argument('--verify-support', action='store_true', help='在总调用预算内额外核查结论支持性')
            p.add_argument('--support-level',choices=['off','advisory','balanced','strict'],default='strict')
        if name == 'evaluate':
            p.add_argument('--dataset', type=Path, required=True)
    args = parser.parse_args(argv)
    cfg = None
    try:
        cfg = Settings.load(args.env_file)
        if args.command == 'doctor':
            result = doctor(cfg)
        elif args.command == 'probe':
            result = probe(cfg)
        else:
            options = dict(collection=args.collection or cfg.collection, retrieval_profile=args.profile or cfg.retrieval_profile,
                           index_version=args.index_version, category=args.category)
            if args.command == 'inspect':
                adapter = DocBaseAdapter(cfg, options['collection'], options['retrieval_profile'],
                                         time.monotonic() + cfg.total_timeout_seconds)
                adapter.category = args.category
                adapter.pin(args.index_version)
                result = adapter.search(args.question)
                result['note'] = '仅检查检索，不调用DeepSeek，不生成答案。'
            else:
                options['verify_support'] = args.verify_support
                options['support_level'] = args.support_level
                runner = AgentRunner(cfg, storage=args.storage.resolve())
                if args.command == 'ask':
                    result = runner.ask(args.question, **options)
                else:
                    from .evaluator import evaluate
                    if not cfg.api_key.strip():
                        raise AgentError('MODEL_NOT_CONFIGURED', '评测前请填写DocAgent/.env中的DOCAGENT_API_KEY')
                    result = evaluate(runner, args.dataset, **options)
        print(json.dumps(sanitize(result, cfg.api_key), ensure_ascii=False, indent=2))
        return {'failed': 1, 'insufficient_evidence': 2, 'cancelled': 130}.get(result.get('status'), 0)
    except (AgentError, OSError, ValueError, KeyError) as exc:
        data = {'status': 'failed', 'code': getattr(exc, 'code', 'LOCAL_ERROR'),
                'error': str(exc) if isinstance(exc, AgentError) else '配置、文件或JSON格式错误'}
        print(json.dumps(sanitize(data, cfg.api_key if cfg else ''), ensure_ascii=False, indent=2))
        return 1
    except KeyboardInterrupt:
        print('{"status":"cancelled","code":"CANCELLED"}')
        return 130
