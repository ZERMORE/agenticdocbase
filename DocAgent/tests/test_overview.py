import copy
import pytest
from test_docagent import row,FakeClient,adapter_for,answer,tool
from docagent.runner import AgentRunner
from docagent.config import Settings
from docagent.coverage import needs_overview
from docagent.evidence import match_quote,EvidenceLedger
from docagent.models import Submission

@pytest.mark.parametrize('q,expected',[
 ('概括这些论文的主要研究方向',True),('总结知识库中的研究主题',True),('Summarize all papers in this collection',True),
 ('总结这篇论文',False),('概括CLIP-Adapter论文的方法',False),('CLIP-Adapter如何调整预训练模型？',False)])
def test_overview_intent(q,expected):assert needs_overview(q)==expected

@pytest.mark.parametrize('source,quote,ok',[
 ('我们进行手语识\n别模型训练。','我们进行手语识别模型训练。',True),
 ('有效地解决了模型推理\n速度慢、输入音视频时长必须匹配。','有效地解决了模型推理速度慢、输入音视频时长必须匹配。',True),
 ('模型不\n能改善结果','模型能改善结果',False),('数字为1\n00','数字为100',False),
 ('从1.0变为2.0','从10变为20',False),('进行模型训练','进行算法训练',False)])
def test_chinese_layout_only(source,quote,ok):
    matched=match_quote(source,quote);assert bool(matched)==ok
    if matched:assert source[matched[0]:matched[1]]

def overview_adapter(row):
    base=adapter_for(row)
    class Adapter(base):
        def overview(self,budget):
            rows=[dict(copy.deepcopy(row),doc_id='doc_'+str(i),artifact_id='a'+str(i),
                       chunk_id=None,evidence_kind='overview_excerpt') for i in range(3)]
            rows=[x for x in rows if self.allowed_doc_ids is None or x['doc_id'] in self.allowed_doc_ids]
            return {'results':rows,'query_id':'overview','coverage':{'eligible_documents':len(rows)},
                    'documents':[{'doc_id':x['doc_id']} for x in rows],'note':'representative excerpts'}
        def search(self,*a,**kw):raise AssertionError('Overview must not start from language-biased BM25')
    return Adapter

def test_overview_cannot_accept_single_document_as_complete(row,tmp_path):
    client=FakeClient([[tool('submit_answer',answer())],[tool('submit_answer',answer())]])
    result=AgentRunner(Settings(),client=client,adapter_factory=overview_adapter(row),storage=tmp_path).ask('概括这些论文的主要研究方向')
    assert result['status']=='insufficient_evidence'
    assert result['retrieval_coverage']['represented_documents']==3
    assert result['retrieval_coverage']['cited_documents']==1
    assert result['usage']['coverage_repairs']==1

def test_overview_accepts_multi_document_citations_and_preserves_scope(row,tmp_path):
    submission=answer();submission['claims'][0]['supports']=[{'evidence_id':f'E{i}','quote':'1729'} for i in (1,2,3)]
    client=FakeClient([[tool('submit_answer',submission)]])
    result=AgentRunner(Settings(),client=client,adapter_factory=overview_adapter(row),storage=tmp_path).ask('概括这些论文的主要研究方向')
    assert result['status']=='answered' and result['retrieval_coverage']['cited_documents']==3
    single=AgentRunner(Settings(),client=FakeClient([[tool('submit_answer',answer())]]),adapter_factory=overview_adapter(row),storage=tmp_path).ask(
        '概括这些论文的主要研究方向',allowed_doc_ids=['doc_1'])
    assert single['retrieval_coverage']['eligible_documents']==1 and single['status']=='answered'

def test_repair_reports_all_invalid_quotes_and_exact_options(row):
    ledger=EvidenceLedger();ledger.add(row)
    submission=answer();submission['claims'][0]['supports']+=[{'evidence_id':'E1','quote':'wrong'}]
    submission['claims'].append({'text':'second','kind':'direct','supports':[{'evidence_id':'E999','quote':'missing'}]})
    errors=ledger.quote_errors(Submission.model_validate(submission))
    assert len(errors)==2 and errors[0]['exact_quote_options']
    assert all(q in row['text'] for q in ledger.public('E1')['quote_options'])


def test_truncated_output_is_not_treated_as_invalid_schema(row,tmp_path):
    class Truncated(FakeClient):
        def complete(self,*args):
            result=super().complete(*args)
            if self.stats['requests']==1:result['finish_reason']='length'
            return result
    client=Truncated([[tool('submit_answer',answer())],[tool('submit_answer',answer())]])
    result=AgentRunner(Settings(),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('version')
    assert result['status']=='answered' and result['usage']['output_repairs']==1
    assert result['usage']['schema_repairs']==0

def test_repeated_truncation_has_precise_failure(row,tmp_path):
    class Truncated(FakeClient):
        def complete(self,*args):
            result=super().complete(*args);result['finish_reason']='length';return result
    result=AgentRunner(Settings(),client=Truncated([[tool('submit_answer',answer())]]*2),adapter_factory=adapter_for(row),storage=tmp_path).ask('version')
    assert result['code']=='MODEL_OUTPUT_LIMIT' and result['usage']['schema_repairs']==0

def test_http_request_budget_counts_rate_limit_attempts():
    import time,httpx
    from docagent.client import DeepSeekClient
    from docagent.common import AgentError
    count=[0]
    def transport(req):
        count[0]+=1
        if count[0]==1:return httpx.Response(429)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'ok'}}]})
    client=DeepSeekClient(Settings(api_key='fake',max_model_calls=2),transport=httpx.MockTransport(transport),sleeper=lambda _:None)
    assert client.complete([],[],time.monotonic()+10)['finish_reason']=='stop'
    with pytest.raises(AgentError) as error:client.complete([],[],time.monotonic()+10)
    assert error.value.code=='MODEL_BUDGET' and count[0]==2
    client.close()


@pytest.mark.parametrize('source,quote,expected',[
 ('UPL is the ﬁrst work','UPL is the first work',True),
 ('diffi\u00ad\nculties in detecting','difficulties in detecting',True),
 ('An eﬀective method','An effective method',True),
 ('oﬁce','of',False),('value 1\u00ad00','value 100',False),
 ('does not improve','does improve',False)])
def test_pdf_presentation_forms_keep_original_spans(source,quote,expected):
    source=source.replace('\\u00ad','\u00ad').replace('\\n','\n')
    match=match_quote(source,quote)
    assert bool(match)==expected
    if match:assert source[match[0]:match[1]]
