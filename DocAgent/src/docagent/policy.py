from .common import AgentError, sha
from .config import ROOT


def load_policy(path=None):
    path = path or ROOT / 'AGENTS.md'
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise AgentError('POLICY', '无法读取项目 AGENTS.md') from exc
    if not text.strip() or len(text) > 40000:
        raise AgentError('POLICY', 'AGENTS.md 为空或超过40000字符')
    return text, sha(text), str(path.resolve())
