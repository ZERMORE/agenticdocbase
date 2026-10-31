import {useEffect,useRef,useState} from 'react';
const KEY='researchdesk.sidebar-width';
const bound=(value:number)=>Math.round(Math.max(220,Math.min(value,560,(window.innerWidth>850?window.innerWidth*.4:560))));
export function useSidebarWidth(){
 const [width,setWidth]=useState(()=>{try{const saved=Number(localStorage.getItem(KEY));return bound(Number.isFinite(saved)&&saved>=220?saved:300);}catch{return bound(300);}});
 const drag=useRef<{pointer:number,x:number,width:number}|null>(null);
 useEffect(()=>{try{localStorage.setItem(KEY,String(width));}catch{}},[width]);
 useEffect(()=>{const resize=()=>{if(innerWidth>850)setWidth(w=>bound(w));};window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize);},[]);
 const stop=()=>{drag.current=null;document.body.classList.remove('resizing-sidebar');};
 useEffect(()=>()=>document.body.classList.remove('resizing-sidebar'),[]);
 return {width,separator:{
  onPointerDown:(e:React.PointerEvent<HTMLDivElement>)=>{if(e.button!==0)return;e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);drag.current={pointer:e.pointerId,x:e.clientX,width};document.body.classList.add('resizing-sidebar');},
  onPointerMove:(e:React.PointerEvent<HTMLDivElement>)=>{if(drag.current?.pointer===e.pointerId)setWidth(bound(drag.current.width+e.clientX-drag.current.x));},
  onPointerUp:stop,onPointerCancel:stop,onLostPointerCapture:stop,
  onDoubleClick:()=>setWidth(bound(300)),
  onKeyDown:(e:React.KeyboardEvent<HTMLDivElement>)=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();setWidth(w=>bound(e.key==='Home'?220:e.key==='End'?560:w+(e.key==='ArrowRight'?20:-20)));}}
 }};
}
