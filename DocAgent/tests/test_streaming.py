import json,time
import httpx,pytest
from docagent.config import Settings
from docagent.client import DeepSeekClient
from docagent.common import AgentError
from docagent.streaming import draft_claims

def test_draft_exposes_claim_text_only_and_handles_partial_unicode():
    raw=json.dumps({'private':'secret reasoning','claims':[{'text':'你好 world','supports':[{'quote':'secret quote claims'}]}]},ensure_ascii=True)
    for i in range(len(raw)+1):
        text=draft_claims(raw[:i]);assert 'secret' not in text
    assert draft_claims(raw)=='你好 world'
    assert draft_claims('{"claims":[{"text":"draft\\u4f')=='draft'

def client(handler,budget=6):
    cfg=Settings(api_key='synthetic-not-real',stream_mode='auto',max_model_calls=budget)
    return DeepSeekClient(cfg,transport=httpx.MockTransport(handler))

def test_fragmented_tool_arguments_drafts_and_reasoning_redaction():
    arguments=json.dumps({'claims':[{'text':'Final text','supports':[{'quote':'private quote'}]}]})
    events=[]
    for i,part in enumerate([arguments[:20],arguments[20:40],arguments[40:]]):
        events.append({'model':'synthetic','choices':[{'delta':{'reasoning_content':'NEVER SHOW','tool_calls':[{'index':0,'id':'c1' if i==0 else None,'function':{'name':'submit_answer' if i==0 else '', 'arguments':part}}]}}]})
    text=''.join('data: '+json.dumps(x)+'\n\n' for x in events)+'data: [DONE]\n\n'
    c=client(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},text=text))
    drafts=[];c.stream_event=lambda event,text:drafts.append((event,text))
    result=c.complete([],[],time.monotonic()+5)
    assert json.loads(result['message']['tool_calls'][0]['function']['arguments'])['claims'][0]['text']=='Final text'
    assert drafts[-1]==('draft','Final text')
    assert 'NEVER' not in str(drafts) and 'private quote' not in str(drafts)
    assert c.stats['requests']==1;c.close()

def test_unsupported_stream_falls_back_once_and_counts_both_requests():
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        if body['stream']:return httpx.Response(400,text='stream not supported')
        return httpx.Response(200,json={'model':'synthetic','choices':[{'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}]})
    c=client(handler);assert c.complete([],[],time.monotonic()+5)['message']['content']=='ok'
    assert len(calls)==2 and c.stats['requests']==2
    c.complete([],[],time.monotonic()+5);assert len(calls)==3 and not calls[-1]['stream'];c.close()

def test_truncated_stream_never_replays_request():
    calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: {"choices":[{"delta":{"content":"hello"}}]}\n\n')
    c=client(handler)
    with pytest.raises(AgentError) as error:c.complete([],[],time.monotonic()+5)
    assert error.value.code=='MODEL_NETWORK' and len(calls)==1;c.close()

def test_cancel_stops_stream_without_replay():
    c=client(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: {}\n\n'))
    def cancel():raise AgentError('CANCELLED','cancelled')
    c.stream_check=cancel
    with pytest.raises(AgentError):c.complete([],[],time.monotonic()+5)
    assert c.stats['requests']==1;c.close()

def test_query_terms_are_opt_in_and_never_replace_constraints():
    from docagent.query_terms import expand_query
    query='2024年少样本图像分类，不使用训练样本'
    assert expand_query(query)==query
    assert expand_query(query,'glossary').startswith(query+'\n')
    assert 'few-shot' in expand_query(query,'glossary')
    assert expand_query('unrelated','glossary')=='unrelated'
