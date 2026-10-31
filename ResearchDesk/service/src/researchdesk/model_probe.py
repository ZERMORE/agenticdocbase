"""Synthetic-only connection probe. Never reads document content."""
import httpx
from .model_profiles import ModelProfiles

def probe(s,payload):
    role=payload['role'];manager=ModelProfiles(s)
    cfg=manager.get(role,private=True,revision=payload['model_snapshot'][role])
    if role=='support' and cfg.get('inherit'):cfg=manager.get('agent',private=True,revision=payload['model_snapshot']['agent'])
    if not cfg['api_key']:raise RuntimeError('MODEL_AUTH: 请先填写此角色的Key')
    base=cfg['base_url'].rstrip('/')
    if role=='embedding':path='/embeddings';body={'model':cfg['model'],'input':['Synthetic version number is 1729.']}
    elif role=='reranker':path='/rerank';body={'model':cfg['model'],'query':'version number','documents':['Version number is 1729.','A tree grows outside.']}
    else:path='/chat/completions';body={'model':cfg['model'],'messages':[{'role':'user','content':'Synthetic connection test. Reply only 1729.'}],'max_tokens':32,'stream':False}
    with httpx.Client(timeout=min(cfg['timeout'],30),follow_redirects=False) as client:
        response=client.post(base+path,headers={'Authorization':'Bearer '+cfg['api_key']},json=body)
        if response.status_code!=200:raise RuntimeError(f'连接测试失败（HTTP {response.status_code}），检查账户与模型配置')
        data=response.json()
    return {'role':role,'revision':cfg['id'],'status':'connected','model_returned':data.get('model'),
            'note':'仅合成文本连通性检查，不代表文档、图像、流协议或检索质量验证通过'}
