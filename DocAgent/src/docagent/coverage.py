import re

def needs_overview(question):
    text=question.strip().lower()
    group=bool(re.search(r'这些|所有|全部|整个|全库|库中|库里|知识库|本库|多篇|各篇|all (?:papers|documents)|these (?:papers|documents)|collection|corpus',text))
    intent=bool(re.search(r'概括|概览|综述|研究方向|研究主题|主要方向|研究领域|总结|归纳|overview|summari[sz]|research (?:directions|topics)|themes',text))
    specific=bool(re.search(r'这篇|该论文|这份|本文|\bthis (?:paper|document)\b',text))
    # Single-paper summaries remain topic searches. Explicit collection/all scopes win.
    return group and intent and (not specific or bool(re.search(r'所有|全部|全库|整个|all papers',text)))

def coverage_report(overview,ledger,claims):
    base=dict(overview)
    eligible=base.get('eligible_documents',0)
    represented={r['doc_id'] for r in ledger.records.values() if r.get('evidence_kind')=='overview_excerpt'}
    cited={ledger.require(s.evidence_id)['doc_id'] for c in claims for s in c.supports}
    required=min(eligible,8)
    base.update(represented_documents=len(represented),cited_documents=len(cited),
                required_cited_documents=required,complete_sampling=len(represented)==eligible,
                sufficient_citation_breadth=len(cited)>=required)
    return base
