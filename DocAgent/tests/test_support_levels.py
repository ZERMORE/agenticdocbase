import pytest
from test_docagent import row,FakeClient,adapter_for,answer,tool
from docagent.config import Settings
from docagent.runner import AgentRunner

def judged(status):
 return [tool('submit_review',{'judgments':[{'claim_index':0,'status':status,'reason':'证据支持有限' if status=='insufficient' else '原文存在明确相反陈述'}]})]

@pytest.mark.parametrize('level,status,expected_claims,expected_status',[
 ('advisory','insufficient',1,'answered'),('advisory','contradicted',1,'answered'),
 ('balanced','insufficient',1,'answered'),('balanced','contradicted',0,'insufficient_evidence'),
 ('strict','insufficient',0,'insufficient_evidence'),('strict','contradicted',0,'insufficient_evidence')])
def test_support_modes_control_semantic_enforcement(row,tmp_path,level,status,expected_claims,expected_status):
 client=FakeClient([[tool('submit_answer',answer())],judged(status)])
 result=AgentRunner(Settings(max_model_calls=2),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('version',verify_support=True,support_level=level)
 assert len(result['claims'])==expected_claims and result['status']==expected_status
 assert result['support_check_level']==level and result['support_warnings']
 if not expected_claims:assert result['answer_completeness']['omitted_claims']==1

def test_off_uses_no_semantic_model_call(row,tmp_path):
 client=FakeClient([[tool('submit_answer',answer())]])
 result=AgentRunner(Settings(),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('version',verify_support=True,support_level='off')
 assert result['status']=='answered' and client.stats['requests']==1
 assert result['support_check']=='disabled' and result['support_check_level']=='off'

@pytest.mark.parametrize('level',['off','advisory','balanced','strict'])
def test_no_mode_bypasses_original_quote_validation(row,tmp_path,level):
 client=FakeClient([[tool('submit_answer',answer('fabricated quote'))]]*2)
 result=AgentRunner(Settings(),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('version',verify_support=True,support_level=level)
 assert result['status']=='failed' and result['code']=='CITATION'
