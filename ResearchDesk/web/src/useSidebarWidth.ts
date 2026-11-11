import {useEffect,useRef,useState} from 'react';
import type {KeyboardEvent, PointerEvent} from 'react';

type PanelWidthOptions = {
  storageKey: string;
  defaultWidth: number;
  minWidth: number;
  maxWidth: (viewport: number) => number;
  edge: 'left' | 'right';
};

export function usePanelWidth({storageKey,defaultWidth,minWidth,maxWidth,edge}:PanelWidthOptions){
  const [preferredWidth,setPreferredWidth]=useState(()=>{
    try {
      const saved=Number(localStorage.getItem(storageKey));
      return Number.isFinite(saved)&&saved>=minWidth?saved:defaultWidth;
    } catch {return defaultWidth;}
  });
  const [viewport,setViewport]=useState(()=>window.innerWidth);
  const maximum=Math.max(minWidth,Math.round(maxWidth(viewport)));
  const clamp=(value:number)=>Math.round(Math.max(minWidth,Math.min(value,maximum)));
  const width=clamp(preferredWidth);
  const drag=useRef<{pointer:number,x:number,width:number}|null>(null);
  const direction=edge==='left'?1:-1;

  useEffect(()=>{
    try {localStorage.setItem(storageKey,String(preferredWidth));} catch {}
  },[storageKey,preferredWidth]);
  useEffect(()=>{
    const resize=()=>setViewport(window.innerWidth);
    window.addEventListener('resize',resize);
    return ()=>window.removeEventListener('resize',resize);
  },[]);

  const stop=()=>{
    if(!drag.current)return;
    drag.current=null;
    document.body.classList.remove('resizing-sidebar');
  };
  useEffect(()=>()=>{if(drag.current)document.body.classList.remove('resizing-sidebar');},[]);
  return {width,minimum:minWidth,maximum,separator:{
    onPointerDown:(event:PointerEvent<HTMLDivElement>)=>{
      if(event.button!==0||!event.isPrimary)return;
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current={pointer:event.pointerId,x:event.clientX,width};
      document.body.classList.add('resizing-sidebar');
    },
    onPointerMove:(event:PointerEvent<HTMLDivElement>)=>{
      const start=drag.current;
      if(start?.pointer===event.pointerId)setPreferredWidth(clamp(start.width+direction*(event.clientX-start.x)));
    },
    onPointerUp:stop,onPointerCancel:stop,onLostPointerCapture:stop,
    onDoubleClick:()=>setPreferredWidth(defaultWidth),
    onKeyDown:(event:KeyboardEvent<HTMLDivElement>)=>{
      const steps:Record<string,number>={ArrowRight:width+direction*20,ArrowLeft:width-direction*20,Home:minWidth,End:maximum};
      if(!Object.hasOwn(steps,event.key))return;
      event.preventDefault();
      setPreferredWidth(clamp(steps[event.key]));
    }
  }};
}

export function useSidebarWidth(){
  return usePanelWidth({
    storageKey:'researchdesk.sidebar-width',defaultWidth:300,minWidth:220,
    maxWidth:viewport=>viewport>850?Math.min(560,viewport*.4):560,edge:'left'
  });
}
