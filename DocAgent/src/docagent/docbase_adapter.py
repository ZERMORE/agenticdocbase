import json
import os
import subprocess
import time
from .common import AgentError
from .config import checked_name


class DocBaseAdapter:
    def __init__(self, cfg, collection, profile, deadline, cancel=None):
        self.cfg, self.collection, self.profile = cfg, checked_name(collection), profile
        self.deadline, self.cancel = deadline, cancel
        self.version = None
        self.category = None
        self.allowed_doc_ids = None
        self.storage = None

    def call(self, command, *args):
        if command not in {'status', 'query', 'get', 'context', 'overview'}:
            raise AgentError('TOOL', '不允许调用写入命令')
        python = self.cfg.base_root / '.conda' / 'python.exe'
        if not python.is_file():
            raise AgentError('DOCBASE_ENV', '未找到 DocBase 独立 Conda Python')
        argv = [str(python), '-m', 'docbase', command, '--collection', self.collection]
        if self.storage:
            argv += ['--storage', str(self.storage)]
        if self.version:
            argv += ['--index-version', checked_name(self.version)]
        argv += list(args)
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env.pop('PYTHONPATH', None)
        with subprocess.Popen(argv, cwd=self.cfg.base_root, env=env, shell=False, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            while True:
                if self.cancel and self.cancel.is_set():
                    process.kill()
                    process.communicate()
                    raise AgentError('CANCELLED', '问答已取消')
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.communicate()
                    raise AgentError('TIMEOUT', '检索子进程超过总时限')
                try:
                    stdout, _ = process.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
                except KeyboardInterrupt:
                    process.kill()
                    process.communicate()
                    raise
            try:
                result = json.loads(stdout)
                if not isinstance(result, dict):
                    raise ValueError('not an object')
            except ValueError as exc:
                raise AgentError('DOCBASE_PROTOCOL', 'DocBase 未返回有效 JSON') from exc
            if process.returncode != 0 or result.get('status') == 'failed':
                raise AgentError('DOCBASE_' + str(result.get('code', 'ERROR')), 'DocBase 工具失败，请检查集合、索引与来源快照')
            if self.version and command != 'status' and result.get('index_version') != self.version:
                raise AgentError('VERSION_CHANGED', '工具返回的索引版本与本次问答不一致')
            return result

    def pin(self, version=None):
        if version:
            self.version = checked_name(version)
        result = self.call('status')
        manifest = result['manifest']
        self.version = checked_name(manifest['index_version'])
        if self.profile != 'bm25-only' and manifest['profile'] == 'bm25-only':
            raise AgentError('PROFILE_MISMATCH', '当前索引只支持 bm25-only，请显式选择该模式')
        return result

    def search(self, query, top_k=8, prefer_body=True):
        if self.allowed_doc_ids is not None:
            import tempfile
            with tempfile.TemporaryDirectory() as folder:
                from pathlib import Path
                scope = Path(folder) / 'scope.json'
                scope.write_text(json.dumps(self.allowed_doc_ids), encoding='utf-8')
                return self.call('query', '--profile', self.profile, '--question', query, '--top-k', str(top_k),
                    '--allowed-doc-ids-file', str(scope), *(['--prefer-body'] if prefer_body else []),
                    *(['--category', self.category] if self.category else []))
        return self.call('query', '--profile', self.profile, '--question', query, '--top-k', str(top_k),
                         *(['--prefer-body'] if prefer_body else []),
                         *(['--category', self.category] if self.category else []))

    def overview(self, budget=24000):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            args = ['--budget', str(budget)]
            if self.allowed_doc_ids is not None:
                scope = Path(folder) / 'scope.json'
                scope.write_text(json.dumps(self.allowed_doc_ids), encoding='utf-8')
                args += ['--allowed-doc-ids-file', str(scope)]
            if self.category: args += ['--category', self.category]
            return self.call('overview', *args)

    def get(self, chunk_id):
        return self.call('get', '--chunk-id', chunk_id)['evidence']

    def context(self, chunk_id, before=1, after=1):
        return self.call('context', '--chunk-id', chunk_id, '--before', str(before), '--after', str(after))['results']
