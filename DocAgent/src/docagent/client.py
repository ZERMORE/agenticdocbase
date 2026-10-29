import time
import httpx
from .common import AgentError


class DeepSeekClient:
    def __init__(self, cfg, transport=None, sleeper=time.sleep):
        self.cfg, self.sleeper = cfg, sleeper
        self.http = httpx.Client(transport=transport, follow_redirects=False)
        self.stats = {'requests': 0, 'responses': 0, 'usage_records': []}

    def close(self):
        self.http.close()

    def complete(self, messages, tools, deadline):
        if not self.cfg.api_key.strip():
            raise AgentError('MODEL_NOT_CONFIGURED', '请在 DocAgent/.env 填写 DOCAGENT_API_KEY')
        body = {'model': self.cfg.model, 'messages': messages, 'tools': tools, 'stream': False,
                'thinking': {'type': 'disabled'}, 'max_tokens': self.cfg.max_output_tokens}
        for attempt in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentError('TIMEOUT', '问答总时限已到')
            if self.stats['requests'] >= self.cfg.max_model_calls:
                raise AgentError('MODEL_BUDGET', '本轮模型请求预算已用完')
            self.stats['requests'] += 1
            try:
                response = self.http.post(self.cfg.base_url.rstrip('/') + '/chat/completions', json=body,
                    headers={'Authorization': 'Bearer ' + self.cfg.api_key},
                    timeout=min(self.cfg.request_timeout_seconds, remaining))
            except httpx.TransportError as exc:
                raise AgentError('MODEL_NETWORK', '模型请求超时或网络不可用；计费状态未知，未自动重试') from exc
            if response.status_code == 429:
                if attempt < 2:
                    self._pause(attempt, deadline, response.headers.get('retry-after'))
                    continue
                raise AgentError('MODEL_BUSY', f'模型服务暂不可用（HTTP {response.status_code}）')
            if response.status_code in (401, 403):
                raise AgentError('MODEL_AUTH', f'模型认证/权限失败（HTTP {response.status_code}）')
            if response.status_code == 402:
                raise AgentError('MODEL_QUOTA', '模型账户余额或资源不足（HTTP 402）')
            if response.status_code != 200:
                # Raw server payload may include private input; do not echo it.
                lowered = response.text.lower()
                if any(word in lowered for word in ('insufficient balance', 'insufficient_quota', '没有可用的计费资源', '余额不足')):
                    raise AgentError('MODEL_QUOTA', '模型账户余额或计费资源不足')
                raise AgentError('MODEL_HTTP', f'模型请求失败（HTTP {response.status_code}），请核对模型、账户资源及协议')
            try:
                data = response.json()
                message = data['choices'][0]['message']
                if message.get('role') != 'assistant' or not isinstance(message.get('content', ''), (str, type(None))):
                    raise ValueError('invalid assistant')
                calls = message.get('tool_calls', []) or []
                if not isinstance(calls, list):
                    raise ValueError('invalid tool calls')
                ids = []
                for call in calls:
                    if call['type'] != 'function' or not isinstance(call['id'], str) or not call['id']:
                        raise ValueError('invalid tool')
                    if not isinstance(call['function']['name'], str) or not isinstance(call['function']['arguments'], str):
                        raise ValueError('invalid function')
                    ids.append(call['id'])
                if len(set(ids)) != len(ids):
                    raise ValueError('duplicate call id')
                self.stats['responses'] += 1
                self.stats['usage_records'].append(data.get('usage'))
                return {'finish_reason': data['choices'][0].get('finish_reason'),
                        'message': {'role': 'assistant', 'content': message.get('content'), 'tool_calls': calls},
                        'model': data.get('model'), 'usage': data.get('usage')}
            except (KeyError, IndexError, ValueError, TypeError, AttributeError) as exc:
                raise AgentError('MODEL_PROTOCOL', '模型响应不符合工具调用协议') from exc

    def _pause(self, attempt, deadline, retry_after=None):
        delay = 0.5 * (2 ** attempt)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                try:
                    from email.utils import parsedate_to_datetime
                    delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
        if time.monotonic() + delay >= deadline:
            raise AgentError('TIMEOUT', '重试前已达到问答总时限')
        self.sleeper(delay)
