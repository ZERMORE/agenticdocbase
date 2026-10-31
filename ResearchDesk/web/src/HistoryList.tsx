import React,{useState} from 'react';
import {api,post} from './api';
import {date,Icon} from './ui';
import {useWorkspace} from './WorkspaceContext';
export function HistoryList(){
 const {convos,conversation,historySearch,setHistorySearch,chooseConversation,safe,refresh,newChat,setNotice}=useWorkspace();
 const [archived,setArchived]=useState(false);
 const rows=convos.filter((c:any)=>!!c.archived===archived&&c.title.includes(historySearch));
 return <><div className="history-heading"><span>{archived?'已归档对话':'最近对话'} · {rows.length}</span><button onClick={()=>setArchived(!archived)}>{archived?'返回最近':'查看归档'}</button></div>
 <label className="history-filter"><Icon name="search" size={14}/><input aria-label="搜索对话" placeholder="搜索对话" value={historySearch} onChange={e=>setHistorySearch(e.target.value)}/></label>
 <div className="history-list">{rows.map((c:any)=><div className="history-entry" key={c.id}><button title={c.title} className={conversation===c.id?'current':''} onClick={()=>chooseConversation(c)}>{c.title}<small>{date(c.updated)}</small></button><div className="history-actions"><button aria-label={(archived?'恢复对话：':'归档对话：')+c.title} onClick={()=>safe(async()=>{await post(`/conversations/${c.id}/${archived?'unarchive':'archive'}`);if(!archived&&conversation===c.id)await newChat();await refresh();setNotice(archived?'对话已恢复':'对话已归档，可在“查看归档”中恢复');})}>{archived?'恢复':'归档'}</button><button aria-label={'删除对话：'+c.title} onClick={()=>safe(async()=>{if(!confirm(`永久删除对话“${c.title}”及其全部分支、消息和收藏？无法撤销，知识库文档不受影响。`))return;await api(`/conversations/${c.id}`,{method:'DELETE'});if(conversation===c.id)await newChat();await refresh();setNotice('对话已删除');})}>删除</button></div></div>)}{!rows.length&&<p className="muted small">{archived?'没有已归档对话。':'没有匹配的对话。'}</p>}</div></>;
}
