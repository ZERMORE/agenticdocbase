"""OpenAI-compatible SSE assembly. Expose only submit_answer.claims[].text drafts."""
import json,time
import httpx
from .common import AgentError

def draft_claims(raw):
    output=[];decoder=json.JSONDecoder()
    def whitespace(pos):
        while pos<len(raw) and raw[pos].isspace():pos+=1
        return pos
    def string(pos):
        start=pos;pos+=1
        while pos<len(raw):
            if raw[pos]=='\\':
                if pos+1>=len(raw):break
                if raw[pos+1]=='u' and pos+6>len(raw):break
                pos+=6 if raw[pos+1]=='u' else 2;continue
            if raw[pos]=='"':
                try:return json.loads(raw[start:pos+1]),pos+1,True
                except ValueError:return '',pos,False
            pos+=1
        try:text=json.loads(raw[start:pos]+'"')
        except ValueError:text=''
        return text.encode('utf-8',errors='replace').decode('utf-8'),pos,False
    def walk(pos,path):
        pos=whitespace(pos)
        if pos>=len(raw):return pos,False
        char=raw[pos]
        if char=='"':
            text,end,done=string(pos)
            if len(path)==3 and path[0]=='claims' and type(path[1]) is int and path[2]=='text':output.append(text)
            return end,done
        if char=='{':
            pos+=1
            while True:
                pos=whitespace(pos)
                if pos>=len(raw):return pos,False
                if raw[pos]=='}':return pos+1,True
                if raw[pos]!='"':return pos,False
                key,pos,done=string(pos)
                if not done:return pos,False
                pos=whitespace(pos)
                if pos>=len(raw) or raw[pos]!=':':return pos,False
                pos,done=walk(pos+1,path+[key])
                if not done:return pos,False
                pos=whitespace(pos)
                if pos<len(raw) and raw[pos]==',':pos+=1;continue
                if pos<len(raw) and raw[pos]=='}':return pos+1,True
                return pos,False
        if char=='[':
            pos+=1;index=0
            while True:
                pos=whitespace(pos)
                if pos>=len(raw):return pos,False
                if raw[pos]==']':return pos+1,True
                pos,done=walk(pos,path+[index])
                if not done:return pos,False
                pos=whitespace(pos)
                if pos<len(raw) and raw[pos]==',':pos+=1;index+=1;continue
                if pos<len(raw) and raw[pos]==']':return pos+1,True
                return pos,False
        try:_,end=decoder.raw_decode(raw,pos);return end,True
        except ValueError:return pos,False
    walk(0,[])
    return '\n\n'.join(output)

def complete_stream(client,messages,tools,deadline):
    cfg=client.cfg
    if not cfg.api_key:raise AgentError('MODEL_NOT_CONFIGURED','模型Key未配置')
    if client.stats['requests']>=cfg.max_model_calls:raise AgentError('MODEL_BUDGET','本轮模型请求预算已用完')
    remaining=deadline-time.monotonic()
    if remaining<=0:raise AgentError('TIMEOUT','问答总时限已到')
    client.stats['requests']+=1
    body={'model':cfg.model,'messages':messages,'tools':tools,'stream':True,
          'stream_options':{'include_usage':True},'thinking':{'type':'disabled'},'max_tokens':cfg.max_output_tokens}
    callback=getattr(client,'stream_event',None);cancel=getattr(client,'stream_check',lambda:None)
    try:
        with client.http.stream('POST',cfg.base_url.rstrip('/')+'/chat/completions',json=body,
            headers={'Authorization':'Bearer '+cfg.api_key},timeout=min(cfg.request_timeout_seconds,remaining)) as response:
            if response.status_code!=200:
                response.read()
                if response.status_code in {400,404,415,422} and 'stream' in response.text.lower():
                    client.stream_capability='unsupported'
                    if callback:callback('stream_fallback',None)
                    return None
                code={401:'MODEL_AUTH',403:'MODEL_AUTH',402:'MODEL_QUOTA',429:'MODEL_BUSY'}.get(response.status_code,'MODEL_HTTP')
                raise AgentError(code,f'模型流请求失败（HTTP {response.status_code}）')
            if 'text/event-stream' not in response.headers.get('content-type',''):
                response.read();data=response.json();message=data['choices'][0]['message']
                client.stream_capability='ignored';client.stats['responses']+=1;client.stats['usage_records'].append(data.get('usage'))
                return {'message':message,'model':data.get('model'),'usage':data.get('usage'),'finish_reason':data['choices'][0].get('finish_reason')}
            calls={};content='';model=None;usage=None;finish=None;last_emit=0;last_draft='';started=False;done=False
            for line in response.iter_lines():
                cancel()
                if time.monotonic()>=deadline:raise AgentError('TIMEOUT','模型流已达到问答总时限')
                if not line.startswith('data:'):continue
                payload=line[5:].strip()
                if payload=='[DONE]':done=True;break
                try:data=json.loads(payload)
                except ValueError:raise AgentError('MODEL_PROTOCOL','模型流包含无效事件')
                model=data.get('model') or model;usage=data.get('usage') or usage
                for choice in data.get('choices',[]):
                    finish=choice.get('finish_reason') or finish;delta=choice.get('delta') or {}
                    # reasoning_content is deliberately ignored, never delivered as a draft.
                    content+=delta.get('content') or ''
                    for fragment in delta.get('tool_calls') or []:
                        index=fragment.get('index',0);call=calls.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
                        if fragment.get('id'):call['id']=fragment['id']
                        fn=fragment.get('function') or {};call['function']['name']+=fn.get('name') or '';call['function']['arguments']+=fn.get('arguments') or ''
                    for call in calls.values():
                        if call['function']['name']=='submit_answer':
                            text=draft_claims(call['function']['arguments'])
                            if text and callback and text!=last_draft and time.monotonic()-last_emit>=.06:
                                if not started:callback('draft_reset','');started=True
                                callback('draft',text);last_draft=text;last_emit=time.monotonic()
            if not done and not finish:raise AgentError('MODEL_NETWORK','模型流意外中断；计费状态未知，未自动重放')
            for call in calls.values():
                if call['function']['name']=='submit_answer' and callback:
                    text=draft_claims(call['function']['arguments'])
                    if text and text!=last_draft:
                        if not started:callback('draft_reset','')
                        callback('draft',text)
            client.stream_capability='supported';client.stats['responses']+=1;client.stats['usage_records'].append(usage)
            return {'message':{'role':'assistant','content':content or None,'tool_calls':[calls[k] for k in sorted(calls)]},
                    'model':model,'usage':usage,'finish_reason':finish}
    except httpx.TransportError as exc:raise AgentError('MODEL_NETWORK','模型流网络中断；未自动重放请求') from exc
    except (KeyError,TypeError,ValueError) as exc:raise AgentError('MODEL_PROTOCOL','模型流响应格式不兼容') from exc
