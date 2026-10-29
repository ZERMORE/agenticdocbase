import React,{useEffect,useState} from 'react';
import {api,post,patch} from './api';
export function WatchPanel({libraries,onError}:any){
  const [items,setItems]=useState<any[]>([]),[path,setPath]=useState(''),[kb,setKb]=useState(libraries[0]?.id||'');
  const refresh=()=>api('/watches').then(setItems).catch(e=>onError(e.message));
  useEffect(()=>{refresh();},[]);
  return <div className="settings-note"><h3>资料目录监控</h3><p>每分钟检查一次，只读源目录、复制新资料并离线入库。目录不可达或文件消失时，不自动删除已入库资料。</p><form className="watch-form" onSubmit={async e=>{e.preventDefault();try{await post('/watches',{kb,path});setPath('');refresh();}catch(err){onError(err.message);}}}><label>目标知识库<select aria-label="监控目标知识库" value={kb} onChange={e=>setKb(e.target.value)}>{libraries.map((l:any)=><option value={l.id} key={l.id}>{l.name}</option>)}</select></label><label>本机资料目录<input aria-label="监控目录路径" required value={path} onChange={e=>setPath(e.target.value)} placeholder="例如 C:\Users\Lenovo\Documents\Papers"/></label><button type="submit" disabled={!kb||!path.trim()}>添加目录</button></form>{items.map(w=><div className="watch-row" key={w.id}><span>{w.path}<small>{w.error||`${libraries.find((l:any)=>l.id===w.kb)?.name||'知识库'} · ${w.enabled?'监控中':'已暂停'}`}</small></span><button onClick={async()=>{try{await patch('/watches/'+w.id,{enabled:!w.enabled});refresh();}catch(e){onError(e.message);}}}>{w.enabled?'暂停':'恢复'}</button></div>)}</div>;
}
