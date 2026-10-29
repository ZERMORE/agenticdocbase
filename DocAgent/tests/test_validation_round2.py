import copy,json
import pytest
from pydantic import ValidationError
from test_docagent import row,FakeClient,adapter_for,answer,tool
from docagent.models import Submission
from docagent.config import Settings
from docagent.runner import AgentRunner
from docagent.protocol import normalize_arguments
from docagent.support_check import review

def test_extra_terminal_brace_repairs_envelope_only(row,tmp_path):
    call=tool('submit_answer',answer());original=call['function']['arguments']
    call['function']['arguments']+='}'
    result=AgentRunner(Settings(),client=FakeClient([[call]]),adapter_factory=adapter_for(row),storage=tmp_path).ask('version')
    assert result['status']=='answered' and result['usage']['schema_repairs']==0
    events=[json.loads(s) for s in open(result['trace_path'],encoding='utf-8')]
    assert any(e['event']=='protocol_normalized' for e in events)
    assert not any(e['event']=='validation_failure' for e in events)
    fixed,metadata=normalize_arguments(original+'}}')
    assert json.loads(fixed)==json.loads(original) and metadata['removed_characters']==2

@pytest.mark.parametrize('suffix',[' some prose',' {}',']','}}}'])
def test_normalizer_rejects_ambiguous_trailing_content(suffix):
    raw=json.dumps(answer())+suffix
    fixed,metadata=normalize_arguments(raw)
    assert fixed==raw and metadata is None
    with pytest.raises(ValidationError):Submission.model_validate_json(fixed)

def test_normalizer_does_not_synthesize_truncated_json():
    raw=json.dumps(answer())[:-8]
    assert normalize_arguments(raw)==(raw,None)

def test_review_sees_same_document_tail_and_names_not_unrelated_sources(row):
    citation=dict(copy.deepcopy(row),evidence_id='E1')
    tail=dict(copy.deepcopy(row),evidence_id='E2',text='We propose conditional dynamic prompts for unseen classes.')
    unrelated=dict(copy.deepcopy(row),evidence_id='E3',doc_id='other',text='PRIVATE unrelated material')
    client=FakeClient([[tool('submit_review',{'judgments':[{'claim_index':0,'status':'supported','reason':'同文档贡献片段提供依据'}]})]])
    out=review(client,Submission.model_validate(answer()).claims,[citation],999999999,
        evidence_records={'E1':citation,'E2':tail,'E3':unrelated},task_mode='overview')
    data=json.loads(client.messages[0][-1]['content'])
    assert {e['evidence_id'] for e in data['evidence']}=={'E1','E2'}
    assert 'dynamic prompts' in json.dumps(data) and 'PRIVATE unrelated' not in json.dumps(data)
    assert data['evidence'][0]['files'] and out[0]['method']=='model-review-v3'

def test_three_of_eight_topics_cannot_be_answered_even_with_eight_cited_docs(row,tmp_path):
    base=adapter_for(row)
    class Adapter(base):
        def overview(self,budget):
            rows=[dict(copy.deepcopy(row),artifact_id=f'a{i}',doc_id=f'd{i}',chunk_id=None,evidence_kind='overview_excerpt') for i in range(8)]
            return {'results':rows,'query_id':'ov','coverage':{'eligible_documents':8},'documents':[],'note':'overview'}
    submission=answer();submission['claims']=[]
    for i in range(8):
        ids=[1,2,3] if i==0 else ([4,5,6] if i==3 else ([7,8] if i==7 else [1]))
        submission['claims'].append({'text':f'主题{i+1}：topic','kind':'direct',
           'supports':[{'evidence_id':f'E{j}','quote':'1729'} for j in ids]})
    judgments=[{'claim_index':i,'status':'supported' if i in {0,3,7} else 'insufficient',
                'reason':'原文直接支持' if i in {0,3,7} else '证据不足'} for i in range(8)]
    client=FakeClient([[tool('submit_answer',submission)],[tool('submit_review',{'judgments':judgments})]])
    result=AgentRunner(Settings(max_model_calls=2),client=client,adapter_factory=Adapter,storage=tmp_path).ask('概括这些论文的主要研究方向',verify_support=True)
    assert result['status']=='insufficient_evidence'
    assert result['retrieval_coverage']['cited_documents']==8
    assert result['retrieval_coverage']['answer_complete'] is False
    summary=result['answer_completeness']
    assert (summary['proposed_claims'],summary['accepted_claims'],summary['omitted_claims'])==(8,3,5)
    assert len(summary['unresolved'])==5 and len(result['claims'])==3

def test_reasonable_inference_is_labelled_instead_of_deleted(row,tmp_path):
    client=FakeClient([[tool('submit_answer',answer())],
        [tool('submit_review',{'judgments':[{'claim_index':0,'status':'inference','reason':'根据原文作合理归纳'}]})]])
    result=AgentRunner(Settings(max_model_calls=2),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('version',verify_support=True)
    assert result['status']=='answered' and result['claims'][0]['kind']=='inference'
    assert result['answer_completeness']['omitted_claims']==0
