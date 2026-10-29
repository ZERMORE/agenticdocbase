import {useEffect,useState} from 'react';
import {api,post,patch} from './api';

export function QualityPanel({kb,doc,onError,onChanged}:any){
  const [data,setData]=useState<any>(null),[block,setBlock]=useState(''),[text,setText]=useState(''),[reason,setReason]=useState('');
  const [page,setPage]=useState(1),[offline,setOffline]=useState(true),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
  const load=()=>api(`/knowledge-bases/${kb}/documents/${doc}/quality`).then(setData).catch(e=>onError(e.message));
  useEffect(()=>{load();},[kb,doc]);
  async function submit(action:string){
    if(!reason.trim()) {onError('请填写修订原因');return;}
    setBusy(true);
    try{await post(`/knowledge-bases/${kb}/documents/${doc}/revisions`,{
      action,reason,expected_revision:data.artifact_revision,request_id:crypto.randomUUID(),
      block_id:block||null,page_index:page-1,text:action==='edit'?text:null,offline:action==='edit'?true:offline});
      setNotice('修订已排队。完成后将生成新版本并更新索引；旧引用保留。');onChanged?.();
    }catch(e){onError(e.message);}finally{setBusy(false);}
  }
  if(!data)return <p role="status">读取解析质量…</p>;
  return <section className="quality-panel"><h3>解析质量与修订</h3><button onClick={load}>刷新质量报告</button><p>{data.page_count??'未知'} 页 · {data.block_count} 个块 · {data.pending_blocks} 个待处理块</p>
    <p className="muted small">{data.note}</p>
    {!!data.warnings.length&&<ul>{data.warnings.map((x:string,i:number)=><li key={i}>{x}</li>)}</ul>}
    <div className="quality-issues">{data.issues.length?data.issues.map((x:any,i:number)=><p key={i}><strong>第 {x.page_index+1} 页 · {x.heuristic?'待人工核对':'已检测'}</strong> {x.message}</p>):<p>未检测到上述风险；这不代表解析准确率为 100%。</p>}</div>
    <details><summary>查看页级统计</summary><table><thead><tr><th>页</th><th>文字字符</th><th>图像</th><th>表格</th><th>待处理块</th></tr></thead><tbody>{data.pages.map((p:any)=><tr key={p.page_index}><td>{p.display_page}</td><td>{p.text_chars}</td><td>{p.images}</td><td>{p.tables}</td><td>{p.pending_blocks}</td></tr>)}</tbody></table></details>
    {data.editable&&<><h3>修订所选内容</h3><p className="small muted">文字纠错保留人工修订标记；重解析会生成新产物，历史版本不覆盖。</p>
      <label>文字块或图像区域<select aria-label="选择修订块" value={block} onChange={e=>{setBlock(e.target.value);setText(data.blocks.find((b:any)=>b.block_id===e.target.value)?.text||'');}}><option value="">请选择</option>{data.blocks.map((b:any)=><option value={b.block_id} key={b.block_id}>{b.has_page?`第${b.page_index+1}页`:'无页码'} · {b.type} · {b.text.slice(0,55)}</option>)}</select></label>
      <label>修正文字<textarea aria-label="修正文字" rows={5} maxLength={50000} value={text} onChange={e=>setText(e.target.value)}/></label>
      <label>修订原因<input aria-label="修订原因" maxLength={500} value={reason} onChange={e=>setReason(e.target.value)}/></label>
      <div className="quality-actions"><button disabled={busy||!block||!text.trim()||data.blocks.find((b:any)=>b.block_id===block)?.type==='image'} onClick={()=>submit('edit')}>保存文字纠错</button>
        <button disabled={busy||data.blocks.find((b:any)=>b.block_id===block)?.type!=='image'} onClick={()=>submit('image')}>补识别所选图像</button></div>
      <label>重解析页序号<input type="number" aria-label="重解析页序号" min={1} max={data.page_count||1} value={page} onChange={e=>setPage(Number(e.target.value))}/></label>
      <label className="checkbox"><input type="checkbox" checked={offline} onChange={e=>setOffline(e.target.checked)}/>仅离线解析；取消后会对所需图像调用外部模型</label>
      <button disabled={busy||!data.page_count} onClick={()=>submit('page')}>仅重解析此页</button>
    </>}{notice&&<p role="status">{notice}</p>}</section>;
}

export function OperationsPanel({onError}:any){
  const [data,setData]=useState<any>(null),[note,setNote]=useState('');
  const load=()=>api('/operations').then(setData).catch(e=>onError(e.message));
  useEffect(()=>{load();},[]);
  if(!data)return null;
  const policy=data.backup_policy;
  return <section className="operations-panel"><h2>备份与恢复</h2><p>可用空间 {(data.disk_free_bytes/1024**3).toFixed(1)} GB · 上次备份 {data.last_backup?new Date(data.last_backup.created*1000).toLocaleString('zh-CN'):'暂无记录'}</p>
    <p className="small muted">自动备份仅在队列空闲时执行。保留策略只清理系统登记的自动备份，手动备份保留。</p>
    {data.backup_error&&<p role="alert">{data.backup_error}</p>}
    <label className="checkbox"><input type="checkbox" checked={policy.enabled} onChange={e=>setData({...data,backup_policy:{...policy,enabled:e.target.checked}})}/>启用定期备份</label>
    <div className="quality-actions"><label>间隔（小时）<input type="number" min={1} max={720} value={policy.interval_hours} onChange={e=>setData({...data,backup_policy:{...policy,interval_hours:Number(e.target.value)}})}/></label>
    <label>保留自动备份数<input type="number" min={1} max={100} value={policy.keep} onChange={e=>setData({...data,backup_policy:{...policy,keep:Number(e.target.value)}})}/></label></div>
    <div className="quality-actions"><button onClick={async()=>{try{await patch('/operations/backup-policy',policy);setNote('备份策略已保存。');}catch(e){onError(e.message);}}}>保存备份策略</button>
    <button onClick={async()=>{try{await post('/operations/backup');setNote('备份已排队，可在任务中心查看。');}catch(e){onError(e.message);}}}>立即备份</button></div><p role="status">{note}</p>
  </section>;
}

export function EvidenceLocation({citation}:any){
  const [data,setData]=useState<any>(null),[error,setError]=useState('');
  useEffect(()=>{let active=true;setData(null);setError('');if(citation.locator_url)api(citation.locator_url).then(x=>{if(active)setData(x);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[citation.locator_url]);
  if(error)return <p className="small muted">定位视图暂不可用：{error}。可继续打开原文。</p>;
  if(!data)return <p className="small muted">{citation.locator_url?'正在定位原文…':'旧记录未提供精确定位。'}</p>;
  return <div className="evidence-location"><p className="small">{data.note}</p>{data.image_asset_id&&<a href={`/api/sources/${data.image_asset_id}`} target="_blank" rel="noreferrer"><img alt={`原文第 ${data.display_page} 页定位，${data.note}`} src={`/api/sources/${data.image_asset_id}`}/></a>}</div>;
}
