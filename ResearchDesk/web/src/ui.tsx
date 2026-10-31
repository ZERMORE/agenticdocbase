import React,{useEffect,useRef} from 'react';
export const labels:any = {synchronizing:'同步 Zotero',zotero_sync:'Zotero 同步',model_probe:'模型连接测试',testing_connection:'测试连接',draft:'生成草稿',draft_reset:'调整草稿',claim_verified:'引用已匹配',stream_fallback:'整段返回',validation_failure:'核验与修复',protocol_normalized:'整理模型返回格式',output_truncated:'缩短超长回答',support_repair:'修正证据不足的结论',document_overview:'逐文档概览',revise:'内容修订',revising:'修订内容',checking_support:'核查结论支持',backup:'资料备份',backing_up:'备份中',queued:'排队中',running:'处理中',succeeded:'已完成',failed:'失败',interrupted:'已中断',cancelled:'已停止',
  ready:'可检索',uploaded:'已上传',indexing:'待入库',removed:'已移出',pending:'等待回答',sent:'已发送',answered:'已回答',insufficient_evidence:'证据不足',
  parsing:'解析文档',parsed:'解析完成',published:'索引已发布',retrieving:'检索资料',initial_search:'检索资料',pinned:'准备资料',
  model_response:'组织回答',tool_call:'补充证据',answer_committed:'引用校验完成',finished:'已完成',parse:'文档解析',reindex:'更新索引',chat:'Agent 问答'};
export const supportLevels:any={off:'关闭',advisory:'宽松提示',balanced:'标准',strict:'严格'};
export const label = (s:string)=>labels[s]||s;
export const date = (n:number)=>new Date(n*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
export const bytes = (n:number)=>n>1048576 ? (n/1048576).toFixed(1)+' MB' : Math.ceil(n/1024)+' KB';
export function Icon({name,size=18}:{name:string,size?:number}) {
  const paths:any={plus:<path d="M12 5v14M5 12h14"/>,chat:<path d="M5 5h14v11H9l-4 4V5z"/>,book:<><path d="M4 4h7v16H4zM13 4h7v16h-7z"/></>,jobs:<><path d="M5 5h14v15H5zM8 2v5M16 2v5M8 11h8M8 15h5"/></>,settings:<><circle cx="12" cy="12" r="4"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4M5 5l3 3m8 8 3 3M5 19l3-3M16 8l3-3"/></>,arrow:<path d="M5 12h14m-5-5 5 5-5 5"/>,up:<path d="m6 11 6-6 6 6M12 5v15"/>,close:<path d="m6 6 12 12M18 6 6 18"/>,upload:<><path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/></>,search:<><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>,menu:<path d="M4 6h16M4 12h16M4 18h16"/>,stop:<path d="M6 6h12v12H6z"/>};
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">{paths[name]||paths.arrow}</svg>;
}
export function Modal({title,onClose,children}:any) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const previous=document.activeElement as HTMLElement; ref.current?.showModal(); return()=>previous?.focus();},[]);
  return <dialog ref={ref} onCancel={e=>{e.preventDefault();onClose();}}><div className="modal-title"><h2>{title}</h2><button className="icon" aria-label="关闭" onClick={onClose}><Icon name="close"/></button></div>{children}</dialog>;
}
