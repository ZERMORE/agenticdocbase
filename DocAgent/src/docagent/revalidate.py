"""Replay an overview submission after validator fixes; never calls a language model."""
import copy
import json
from pathlib import Path
import time
import uuid
from .common import AgentError
from .config import Settings
from .docbase_adapter import DocBaseAdapter
from .evidence import EvidenceLedger
from .models import Submission
from .coverage import coverage_report
from .reporting import save_run

def revalidate(job_folder,output=None):
    job_folder=Path(job_folder).resolve()
    request=json.loads((job_folder/'request.json').read_text(encoding='utf-8'))
    original=json.loads((job_folder/'result.json').read_text(encoding='utf-8'))
    trace_path=Path(original['trace_path'])
    if not trace_path.resolve().is_relative_to(job_folder):raise AgentError('REVALIDATE','运行记录路径越界')
    events=[json.loads(x) for x in trace_path.read_text(encoding='utf-8').splitlines() if x.strip()]
    submissions=[e['arguments'] for e in events if e.get('event')=='tool_call' and e.get('name')=='submit_answer']
    if not submissions:raise AgentError('REVALIDATE','此运行没有完整结构化提交，不能离线恢复')
    submission=Submission.model_validate(submissions[-1])
    evidence=json.loads((trace_path.parent/'evidence.json').read_text(encoding='utf-8'))
    if any(r.get('evidence_kind')!='overview_excerpt' for r in evidence.values()):
        raise AgentError('REVALIDATE','此离线重验仅支持可重新校验的概览片段')
    cfg=Settings.load()
    adapter=DocBaseAdapter(cfg,original['collection'],'bm25-only',time.monotonic()+180)
    adapter.allowed_doc_ids=request.get('allowed_doc_ids')
    adapter.storage=request.get('docbase_storage')
    adapter.pin(original['index_version'])
    fresh=adapter.overview(max(120,int(cfg.max_evidence_chars*.85)))
    def identity(row):return json.dumps([row['artifact_id'],row['doc_id'],row['block_ranges'],row['text']],sort_keys=True,ensure_ascii=False)
    identities={identity(r) for r in fresh['results']}
    ledger=EvidenceLedger(cfg.max_evidence_chars)
    for eid,row in evidence.items():
        if identity(row) not in identities:raise AgentError('REVALIDATE','原证据与重新校验的固定索引不一致')
        added=ledger.add(row)
        if not added or added['evidence_id']!=eid:raise AgentError('REVALIDATE','证据台账编号不一致')
    citations=ledger.validate(submission)
    coverage=coverage_report(fresh['coverage'],ledger,submission.claims)
    if not coverage['complete_sampling'] or not coverage['sufficient_citation_breadth']:
        submission.status='insufficient_evidence'
        submission.limitations.append('概览取样或引用覆盖不足，不能代表全部资料。')
    result=copy.deepcopy(original)
    for key in ('error','code','answer','answer_md_path','answer_json_path','trace_path'):result.pop(key,None)
    key='revalidated_'+uuid.uuid4().hex[:16]
    result.update(run_id=key,status=submission.status,claims=[c.model_dump() for c in submission.claims],
        citations=citations,limitations=submission.limitations+[
            '此结果由已返回的模型输出离线重验恢复，未增加模型请求；PDF排版匹配通过，语义支持性未进行独立复核。',
            f"概览取样{coverage['represented_documents']}/{coverage['eligible_documents']}份，回答引用{coverage['cited_documents']}份；不是逐篇全文审阅。"],
        support_check='unchecked',claim_checks=[],retrieval_coverage=coverage,
        revalidation={'source_run_id':original['run_id'],'source_job':job_folder.name,'method':'fixed-index-and-original-span-replay','new_model_requests':0},
        generation_usage=original['usage'],usage={'model_calls':0,'provider':{'requests':0}})
    destination=Path(output or (job_folder/'revalidations'/key))
    return save_run(destination,result,[{'event':'offline_revalidated','source_run':original['run_id']}],ledger,
                    (trace_path.parent/'policy_snapshot.md').read_text(encoding='utf-8'),cfg.api_key)

def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--job-folder',type=Path,required=True)
    args=p.parse_args();result=revalidate(args.job_folder)
    print(json.dumps({'status':result['status'],'answer_json_path':result['answer_json_path'],
                      'coverage':result['retrieval_coverage'],'new_model_requests':0},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
