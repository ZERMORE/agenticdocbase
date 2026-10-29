import json
import time
import httpx
import pytest
from docagent.client import DeepSeekClient
from docagent.common import AgentError
from docagent.config import Settings
from docagent.runner import AgentRunner
from test_docagent import row,FakeClient,adapter_for,answer,tool

def judgment(status='supported'):
    return [tool('submit_review',{'judgments':[{'claim_index':0,'status':status,'reason':'原句与结论关系测试'}]})]

def test_support_check_records_model_judgment(row,tmp_path):
    client=FakeClient([[tool('submit_answer',answer())],judgment()])
    result=AgentRunner(Settings(),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('版本',verify_support=True)
    assert result['status']=='answered' and result['support_check']=='model_reviewed'
    assert result['claim_checks'][0]['human_verified'] is False
    assert result['usage']['model_calls']==2

def test_contradicted_claim_dropped_after_one_repair(row,tmp_path):
    client=FakeClient([[tool('submit_answer',answer())],judgment('contradicted'),
                       [tool('submit_answer',answer())],judgment('contradicted')])
    result=AgentRunner(Settings(),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('版本',verify_support=True)
    assert result['status']=='insufficient_evidence' and not result['claims'] and not result['citations']
    assert result['usage']['support_repairs']==1 and result['usage']['model_calls']==4

def test_checker_protocol_failure_does_not_claim_verified(row,tmp_path):
    client=FakeClient([[tool('submit_answer',answer())],[]])
    result=AgentRunner(Settings(),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('版本',verify_support=True)
    assert result['status']=='answered' and result['support_check']=='unchecked'
    assert not result['claim_checks'] and any('未完成' in x for x in result['limitations'])

def test_checker_respects_call_budget(row,tmp_path):
    client=FakeClient([[tool('submit_answer',answer())]])
    result=AgentRunner(Settings(max_model_calls=1),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('版本',verify_support=True)
    assert result['support_check']=='unchecked' and result['usage']['model_calls']==1

@pytest.mark.parametrize('kind',['timeout','500'])
def test_unknown_billing_requests_not_replayed(kind):
    def transport(request):
        if kind=='timeout':raise httpx.ReadTimeout('test')
        return httpx.Response(500)
    c=DeepSeekClient(Settings(api_key='fake'),transport=httpx.MockTransport(transport),sleeper=lambda _:None)
    with pytest.raises(AgentError):c.complete([],[],time.monotonic()+10)
    assert c.stats['requests']==1
    c.close()

def test_retry_after_header_honored():
    count=[0];slept=[]
    def transport(request):
        count[0]+=1
        if count[0]==1:return httpx.Response(429,headers={'retry-after':'2'})
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'ok'}}]})
    c=DeepSeekClient(Settings(api_key='fake'),transport=httpx.MockTransport(transport),sleeper=slept.append)
    c.complete([],[],time.monotonic()+10)
    assert slept==[2.0] and c.stats['requests']==2
    c.close()


def test_checker_cannot_pass_claim_while_admitting_missing_support(row,tmp_path):
    client=FakeClient([[tool('submit_answer',answer())],
        [tool('submit_review',{'judgments':[{'claim_index':0,'status':'supported','reason':'另一项机制未提供证据，但其余要点有据。'}]})]])
    result=AgentRunner(Settings(max_model_calls=2),client=client,adapter_factory=adapter_for(row),storage=tmp_path).ask('版本',verify_support=True)
    assert result['status']=='insufficient_evidence' and not result['claims']
