"""Trusted service entry point. No HTTP listener and no changes to CLI ask."""
import argparse
import json
from pathlib import Path
import threading
import time
from .runner import AgentRunner
from .common import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request-file', type=Path, required=True)
    parser.add_argument('--result-file', type=Path, required=True)
    parser.add_argument('--events-file', type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request_file.read_text(encoding='utf-8'))
    cancel = threading.Event()
    def progress(event):
        with args.events_file.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
    from .config import Settings
    from dataclasses import replace
    cfg = Settings.load()
    budget = request.get('model_call_budget')
    if budget is not None:
        if type(budget) is not int or not 1 <= budget <= 6: raise ValueError('invalid model budget')
        cfg = replace(cfg,max_model_calls=min(cfg.max_model_calls,budget))
    result = AgentRunner(cfg,storage=args.result_file.parent).ask(
        request['question'], collection=request['collection'], index_version=request.get('index_version'),
        retrieval_profile='bm25-only', recent_messages=request.get('recent_messages'),
        preferences=request.get('preferences'), allowed_doc_ids=request.get('allowed_doc_ids'),
        conversation_summary=request.get('conversation_summary'),
        docbase_storage=request.get('docbase_storage'),
        verify_support=request.get('verify_support', True), support_level=request.get('support_check_level','advisory'), cancel=cancel, progress=progress)
    write_json(args.result_file, result)


if __name__ == '__main__':
    main()
