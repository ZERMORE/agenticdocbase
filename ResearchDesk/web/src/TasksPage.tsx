import React from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {api,post,patch} from './api';
import {Icon,Modal,label,date,bytes,supportLevels} from './ui';
import {useWorkspace} from './WorkspaceContext';
import {WatchPanel} from './WatchPanel';
import {ScopeControl} from './ScopeControl';
import {QualityPanel,OperationsPanel,EvidenceLocation} from './QualityPanel';

export function TasksPage(){
 const {page,libraries,kb,jobs,error,health,name,safe}=useWorkspace();
 return (<section className="workspace-page"><div className="page-heading"><div><div className="overline">后台进程</div><h1>任务中心<span className="red-period">.</span></h1><p>每一次解析、入库与回答，都有迹可循。</p></div><span className="muted">{health.worker_alive?'Worker 在线':'Worker 未连接'}</span></div><div className="job-list">{jobs.map(j=><div className="job-row" key={j.id}><div><strong>{label(j.kind)}</strong><small>{libraries.find(l=>l.id===j.kb)?.name||'知识库'} · {date(j.created)}</small>{j.result&&j.kind==='model_probe'&&<p className="small">返回模型：{JSON.parse(j.result).model_returned||'服务未返回模型名'}</p>}{j.error&&<p className="error-text">{j.error}</p>}{j.failure?.advice&&<p className="small muted">{j.failure.advice}</p>}</div><span>{label(j.stage)}</span><span className={j.state==='failed'?'bad':''}>{label(j.state)}</span>{['queued','running'].includes(j.state)?<button onClick={()=>safe(()=>post(`/jobs/${j.id}/cancel`))}>停止</button>:['failed','interrupted','cancelled'].includes(j.state)&&j.kind!=='chat'?<button onClick={()=>safe(()=>post(`/jobs/${j.id}/retry`))}>重试</button>:<span/>}</div>)}{!jobs.length&&<p className="empty-list">暂无后台任务。</p>}</div></section>);
}
