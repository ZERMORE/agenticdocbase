import React from 'react';
import {useSidebarWidth} from './useSidebarWidth';
import {HistoryList} from './HistoryList';
import {createRoot} from 'react-dom/client';
import {useWorkspaceController} from './controller';
import {WorkspaceContext} from './WorkspaceContext';
import {Icon,date} from './ui';
import {SavedPage} from './V2Chat';
import {ChatPage} from './ChatPage';
import {LibraryPage} from './LibraryPage';
import {TasksPage} from './TasksPage';
import {SettingsPage} from './SettingsPage';
import {Overlays} from './Overlays';
import './style.css';
function App(){const sidebar=useSidebarWidth();const ctx=useWorkspaceController();const {ready,page,libraries,convos,conversation,jobs,error,setError,notice,setNotice,input,health,filter,navOpen,setNavOpen,name,offline,historySearch,setHistorySearch,bottom,current,total,selected,navigate,chooseConversation,newChat}=ctx;
  return <WorkspaceContext.Provider value={ctx}><div className="shell" style={{'--sidebar-width':`${sidebar.width}px`} as React.CSSProperties}>
    <aside className={'sidebar '+(navOpen?'open':'')}>
      <a className="brand" href="#" onClick={e=>{e.preventDefault();newChat();}}><span className="brand-mark" aria-hidden="true">[<i/>]</span><span>研知<small>Research Desk</small></span></a>
      <button className="new-chat" onClick={newChat}><Icon name="plus"/> 新建研究对话 <span>↗</span></button>
      <nav aria-label="主导航">{[['chat','chat','研究对话'],['library','book','知识库'],['saved','book','收藏'],['jobs','jobs','任务中心'],['settings','settings','个性化设置']].map(([p,i,t])=><button key={p} className={page===p?'selected':''} onClick={()=>navigate(p)}><Icon name={i}/>{t}{p==='jobs'&&jobs.some(j=>['running','queued'].includes(j.state))&&<span className="dot"/>}</button>)}</nav>
      <HistoryList/>
      <div className="sidebar-bottom"><span className={'connection '+(!health.worker_alive?'offline':'')}/><span>{health.worker_alive?'本地工作台已连接':'等待后台连接'}<small>{libraries.length} 个知识库 · {total} 份资料</small></span><span className="local">本机</span></div>
    </aside>
    <div className="sidebar-resizer" role="separator" aria-label="调整边栏宽度" aria-orientation="vertical" aria-valuemin={220} aria-valuemax={Math.max(220,Math.min(560,Math.round(innerWidth*.4)))} aria-valuenow={sidebar.width} tabIndex={0} title="拖动调整边栏宽度；双击恢复默认；方向键微调" {...sidebar.separator}/>
    {navOpen&&<button className="nav-scrim" aria-label="关闭导航" onClick={()=>setNavOpen(false)}/>}
    <main>
      <header className="topbar"><button className="icon mobile-menu" aria-label="打开导航" onClick={()=>setNavOpen(!navOpen)}><Icon name="menu"/></button><div className="breadcrumb">工作空间 <span>/</span> {page==='chat'?'研究对话':page==='library'?'知识库':page==='jobs'?'任务中心':page==='saved'?'收藏':'个性化设置'}</div><span className="top-note">文献为据 · 思考有迹</span></header>
      {error&&<div className="alert" role="alert"><span>{error}</span><button onClick={()=>setError('')} aria-label="关闭错误"><Icon name="close" size={16}/></button></div>}
      {notice&&<div className="notice" role="status"><span>{notice}</span><button aria-label="关闭提示" onClick={()=>setNotice('')}><Icon name="close" size={16}/></button></div>}
      {!ready?<div className="loading">连接本地工作空间…</div>:<>
      {page==='chat'&&<ChatPage/>}
{page==='library'&&<LibraryPage/>}
{page==='jobs'&&<TasksPage/>}
{page==='settings'&&<SettingsPage/>}{page==='saved'&&<SavedPage/>}
      </>}
    </main>
    <Overlays/>
  </div></WorkspaceContext.Provider>;
}
createRoot(document.getElementById('root')!).render(<App/>);
