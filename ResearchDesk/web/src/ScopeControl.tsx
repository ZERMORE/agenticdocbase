import React,{useRef,useState} from 'react';
import {api,post,patch} from './api';
export function ScopeControl({kb,conversation,onConversation,onError}:any){
  const dialog=useRef<HTMLDialogElement>(null);
  const [rows,setRows]=useState<any[]>([]),[all,setAll]=useState(true),[selected,setSelected]=useState<string[]>([]),[id,setId]=useState('');
  const open=async()=>{try{
    let key=conversation;
    if(!key){const c=await post('/conversations',{kb});key=c.id;onConversation(key);}
    setId(key);
    const docs=await api(`/knowledge-bases/${kb}/documents?state=active&limit=100`);
    setRows(docs.items);
    const scope=await api(`/conversations/${key}/scope`);
    setAll(scope.document_ids===null);setSelected(scope.document_ids||[]);
    dialog.current?.showModal();
  }catch(e){onError(e.message);}};
  return <><button className="scope-button" disabled={!kb} onClick={open}>限定资料 ↗</button><dialog ref={dialog} aria-labelledby="scope-title"><div className="modal-title"><h2 id="scope-title">本次会话的检索范围</h2><button aria-label="关闭资料范围" onClick={()=>dialog.current?.close()}>关闭</button></div><p className="muted small">默认在整个知识库自主检索，也可以仅选择指定资料。更改范围会停止尚未完成的回答。</p><label className="checkbox"><input type="checkbox" checked={all} onChange={e=>setAll(e.target.checked)}/>使用此知识库中的全部有效资料</label><div className="scope-documents">{rows.map(d=><label key={d.doc} className="checkbox"><input type="checkbox" checked={all||selected.includes(d.doc)} disabled={all} onChange={e=>setSelected(e.target.checked?[...selected,d.doc]:selected.filter(x=>x!==d.doc))}/>{d.name}</label>)}</div><div className="modal-actions"><button onClick={()=>dialog.current?.close()}>取消</button><button className="primary" onClick={async()=>{try{await patch(`/conversations/${id}/scope`,{document_ids:all?null:selected});dialog.current?.close();}catch(e){onError(e.message);}}}>保存范围</button></div></dialog></>;
}
