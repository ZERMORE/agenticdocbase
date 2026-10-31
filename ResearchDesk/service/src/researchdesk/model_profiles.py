"""Immutable model profiles with Windows DPAPI credentials. No plaintext secrets in DB/API."""
import base64,ctypes,json,os,re,time
from pathlib import Path
from urllib.parse import urlparse
from .store import WORKSPACE,uid,dump,atomic

ROLES=('agent','support','parser','embedding','reranker')

def protect(value,decode=False):
    if os.name!='nt':raise RuntimeError('此版本凭据保护需要Windows DPAPI')
    from ctypes import wintypes
    class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=base64.b64decode(value) if decode else value.encode('utf-8')
    buf=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw);src=Blob(len(raw),buf);dst=Blob()
    crypt=ctypes.windll.crypt32
    if decode:ok=crypt.CryptUnprotectData(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst))
    else:ok=crypt.CryptProtectData(ctypes.byref(src),ctypes.c_wchar_p('ResearchDesk'),None,None,None,1,ctypes.byref(dst))
    if not ok:raise RuntimeError('Windows凭据保护失败，请在当前Windows账户重新配置Key')
    try:result=ctypes.string_at(dst.data,dst.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(dst.data)
    return result.decode('utf-8') if decode else base64.b64encode(result).decode('ascii')

def envfile(path):
    values={}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line:continue
            key,value=line.split('=',1);value=value.strip()
            if len(value)>=2 and value[0]==value[-1] and value[0] in {'"',"'"}:value=value[1:-1]
            values[key.strip()]=value
    return values

class ModelProfiles:
    def __init__(self,s,workspace=WORKSPACE):self.s,self.workspace=s,Path(workspace)
    def defaults(self):
        a=envfile(self.workspace/'DocAgent/.env');p=envfile(self.workspace/'doc2md/.env');b=envfile(self.workspace/'DocBase/.env')
        def make(values,prefix,base,model):return {'base_url':values.get(prefix+'BASE_URL') or base,'model':values.get(prefix+'MODEL') or model,
            'timeout':float(values.get(prefix+'REQUEST_TIMEOUT_SECONDS') or 60),'max_calls':6,'max_tokens':2500,
            'stream_mode':'auto','api_key':values.get(prefix+'API_KEY','')}
        agent=make(a,'DOCAGENT_','https://api.deepseek.com','deepseek-flash')
        parser=make(p,'DOC2MD_','https://ai.gitee.com/v1','Qwen3.6-Plus');parser['max_tokens']=4096
        return {'agent':agent,'support':dict(agent,inherit=True),'parser':parser,
                'embedding':make(b,'DOCBASE_EMBED_','https://ai.gitee.com/v1','Qwen3-Embedding-8B'),
                'reranker':make(b,'DOCBASE_RERANK_','https://ai.gitee.com/v1','Qwen3-Reranker-8B')}
    def ensure(self):
        for role,config in self.defaults().items():
            if not self.s.one('SELECT role FROM model_profiles WHERE role=?',(role,)):self.save(role,config,initial=True)
    def save(self,role,value,initial=False):
        if role not in ROLES:raise ValueError('模型角色无效')
        current=self.get(role,private=True,ensure=False) or {}
        cfg={'timeout':60,'max_calls':6,'max_tokens':2500,'stream_mode':'auto'}|{k:v for k,v in current.items() if k not in {'id','role','api_key','created','key_configured','key_hint','key_error'}}
        cfg.update({k:v for k,v in value.items() if k in {'base_url','model','timeout','max_calls','max_tokens','stream_mode','inherit'}})
        if role!='support':cfg.pop('inherit',None)
        parsed=urlparse(cfg.get('base_url',''))
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('模型地址须为不含凭据和查询参数的HTTPS地址')
        if not cfg.get('model') or len(cfg['model'])>200:raise ValueError('模型名称无效')
        if not 1<=cfg.get('timeout',60)<=300 or not 1<=cfg.get('max_calls',6)<=6 or not 128<=cfg.get('max_tokens',2500)<=16000:raise ValueError('超时、调用或输出预算超出范围')
        if cfg.get('stream_mode','auto') not in {'auto','off'}:raise ValueError('流模式无效')
        if current.get('key_error') and not value.get('api_key') and not value.get('clear_key'):raise ValueError('原Key无法解密，请重新填写或清除')
        key=value.get('api_key') or current.get('api_key','')
        if value.get('clear_key'):key=''
        if current and parsed.netloc!=urlparse(current['base_url']).netloc and not value.get('api_key') and key:
            raise ValueError('更换服务域名时需重新填写Key，不能将旧Key自动发送到新地址')
        revision=uid('model');credential=None
        if key:
            credential=uid('secret');path=self.s.data/'secrets'/(credential+'.dpapi')
            atomic(path,{'dpapi':protect(key)})
        with self.s.connect() as c:
            c.execute('INSERT INTO model_profile_revisions VALUES(?,?,?,?,?)',(revision,role,dump(cfg),credential,time.time()))
            c.execute('INSERT OR REPLACE INTO model_profiles VALUES(?,?)',(role,revision))
        return self.get(role)
    def get(self,role,private=False,revision=None,ensure=True):
        if ensure and not self.s.one('SELECT role FROM model_profiles WHERE role=?',(role,)):self.ensure()
        row=self.s.one('SELECT r.* FROM model_profile_revisions r WHERE r.id=?',(revision,)) if revision else self.s.one('SELECT r.* FROM model_profiles p JOIN model_profile_revisions r ON r.id=p.revision WHERE p.role=?',(role,))
        if not row:return None
        cfg=json.loads(row['config']);key='';key_error=''
        if row['role']!=role:raise ValueError('配置版本角色不匹配')
        if row['credential']:
            try:key=protect(json.loads((self.s.data/'secrets'/(row['credential']+'.dpapi')).read_text())['dpapi'],decode=True)
            except (OSError,ValueError,RuntimeError,KeyError):key_error='此Windows账户无法解密原Key，请重新填写' 
        result=cfg|{'id':row['id'],'role':role,'created':row['created'],'key_configured':bool(key),'key_hint':('••••'+key[-4:]) if key else ''}
        if key_error:result['key_error']=key_error
        if private:result['api_key']=key
        return result
    def snapshot(self):
        self.ensure();return {role:self.get(role)['id'] for role in ROLES}
    def environment(self,snapshot,project):
        env={}
        mappings={'agent':('DocAgent','DOCAGENT_'),'support':('DocAgent','DOCAGENT_SUPPORT_'),
                  'parser':('doc2md','DOC2MD_'),'embedding':('DocBase','DOCBASE_EMBED_'),'reranker':('DocBase','DOCBASE_RERANK_')}
        for role,(owner,prefix) in mappings.items():
            if role not in snapshot or owner!=project:continue
            cfg=self.get(role,private=True,revision=snapshot[role])
            if role=='support' and cfg.get('inherit',True):continue
            if cfg.get('key_error'):raise RuntimeError(cfg['key_error'])
            if owner=='DocBase':env['DOCBASE_TIMEOUT']=str(cfg['timeout'])
            env[prefix+'BASE_URL']=cfg['base_url'];env[prefix+'MODEL']=cfg['model'];env[prefix+'API_KEY']=cfg['api_key']
            if role=='support':
                env['DOCAGENT_SUPPORT_REQUEST_TIMEOUT_SECONDS']=str(cfg['timeout']);env['DOCAGENT_SUPPORT_MAX_OUTPUT_TOKENS']=str(cfg['max_tokens']);env['DOCAGENT_SUPPORT_MAX_MODEL_CALLS']=str(cfg['max_calls'])
            if role=='agent':
                env['DOCAGENT_REQUEST_TIMEOUT_SECONDS']=str(cfg['timeout']);env['DOCAGENT_MAX_MODEL_CALLS']=str(cfg['max_calls']);env['DOCAGENT_MAX_OUTPUT_TOKENS']=str(cfg['max_tokens'])
                env['DOCAGENT_STREAM_MODE']=cfg.get('stream_mode','auto')
        return env
