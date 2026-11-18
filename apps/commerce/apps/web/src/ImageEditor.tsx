import {useEffect,useRef,useState} from 'react';
import {Stage,Layer,Group,Image as KonvaImage,Rect,Text} from 'react-konva';
import {Button,Segmented,Tooltip,App as AntApp} from 'antd';
import {DragOutlined,SelectOutlined,ZoomInOutlined,ZoomOutOutlined,ExpandOutlined} from '@ant-design/icons';
import type {KonvaEventObject} from 'konva/lib/Node';
import type {Region} from './api';
import {useEditor} from './store';

type Props={url:string,width:number,height:number,view:'source'|'target',disabled?:boolean};
const overlap=(a:Region,b:Region)=>Math.max(a.target_x,b.target_x)<Math.min(a.target_x+a.width,b.target_x+b.width)&&Math.max(a.target_y,b.target_y)<Math.min(a.target_y+a.height,b.target_y+b.height);
export function ImageEditor({url,width,height,view,disabled=false}:Props){
 const {message}=AntApp.useApp();const host=useRef<HTMLDivElement>(null);const [box,setBox]=useState({width:500,height:360});
 const [image,setImage]=useState<HTMLImageElement>();const [failed,setFailed]=useState(false);const [reload,setReload]=useState(0);
 const [pan,setPan]=useState({x:0,y:0});const [draft,setDraft]=useState<{x:number,y:number,width:number,height:number}>();const start=useRef<{x:number,y:number}|null>(null);
 const {regions,zoom,mode,setRegions,setZoom,setMode}=useEditor();
 useEffect(()=>{if(!host.current)return;const observer=new ResizeObserver(([entry])=>setBox({width:entry.contentRect.width,height:entry.contentRect.height}));observer.observe(host.current);return()=>observer.disconnect()},[]);
 useEffect(()=>{setImage(undefined);setFailed(false);setPan({x:0,y:0});const im=new window.Image();im.crossOrigin='anonymous';im.onload=()=>setImage(im);im.onerror=()=>setFailed(true);im.src=url;return()=>{im.onload=null;im.onerror=null}},[url,reload]);
 const fit=Math.min((box.width-40)/width,(box.height-40)/height),scale=fit*zoom;
 const origin={x:(box.width-width*scale)/2+pan.x,y:(box.height-height*scale)/2+pan.y};
 const point=(e:KonvaEventObject<MouseEvent|TouchEvent>)=>{const p=e.target.getStage()?.getPointerPosition();return p?{x:Math.max(0,Math.min(width,Math.round((p.x-origin.x)/scale))),y:Math.max(0,Math.min(height,Math.round((p.y-origin.y)/scale)))}:null};
 const down=(e:KonvaEventObject<MouseEvent|TouchEvent>)=>{if(disabled||!image||view!=='source'||mode!=='draw')return;start.current=point(e)};
 const move=(e:KonvaEventObject<MouseEvent|TouchEvent>)=>{if(!start.current)return;const p=point(e);if(p)setDraft({x:Math.min(start.current.x,p.x),y:Math.min(start.current.y,p.y),width:Math.abs(p.x-start.current.x),height:Math.abs(p.y-start.current.y)})};
 const up=()=>{start.current=null;if(!draft){return}const r:Region={source_x:draft.x,source_y:draft.y,width:draft.width,height:draft.height,target_x:draft.x,target_y:draft.y};setDraft(undefined);if(!r.width||!r.height)return;if(regions.length>=20){message.warning('最多可添加 20 个保护区域');return}if(regions.some(other=>overlap(r,other))){message.warning('目标保护区域不能重叠，请调整选区');return}setRegions([...regions,r])};
 return <div className="editor-panel"><div className="editor-heading"><span><b>{view==='source'?'01 原图选区':'02 目标位置'}</b><small>{view==='source'?'拖动框选需保留的产品细节':'拖动绿色选区，仅做等尺寸平移'}</small></span><span className="pixel-label">{width} × {height}</span></div>
 <div ref={host} className="canvas-host" aria-label={view==='source'?'原图保护画布':'目标映射画布'} data-testid={'canvas-'+view}>
 {failed?<div className="canvas-placeholder">预览暂未加载<Button onClick={()=>setReload(x=>x+1)}>重试</Button></div>:!image?<div className="canvas-placeholder">正在加载原图…</div>:<Stage width={box.width} height={box.height} onMouseDown={down} onTouchStart={down} onMouseMove={move} onTouchMove={move} onMouseUp={up} onMouseLeave={up} onTouchEnd={up} onWheel={e=>{e.evt.preventDefault();setZoom(Math.max(.4,Math.min(4,zoom*(e.evt.deltaY>0?.9:1.1))))}}>
 <Layer><Group x={origin.x} y={origin.y} scaleX={scale} scaleY={scale} draggable={mode==='pan'} onDragEnd={e=>{if(e.target.getClassName()==='Group')setPan({x:e.target.x()-(box.width-width*scale)/2,y:e.target.y()-(box.height-height*scale)/2})}}>
 <KonvaImage image={image} width={width} height={height}/>
 {regions.map((r,i)=><Group key={i}><Rect x={view==='source'?r.source_x:r.target_x} y={view==='source'?r.source_y:r.target_y} width={r.width} height={r.height} stroke="#42a46c" strokeWidth={2/scale} fill="rgba(49,142,85,.16)" draggable={view==='target'&&mode!=='pan'&&!disabled} onDragEnd={e=>{const next={...r,target_x:Math.max(0,Math.min(width-r.width,Math.round(e.target.x()))),target_y:Math.max(0,Math.min(height-r.height,Math.round(e.target.y())))};if(regions.some((other,j)=>j!==i&&overlap(next,other))){e.target.position({x:r.target_x,y:r.target_y});message.warning('目标保护区域不能重叠');return}e.target.position({x:next.target_x,y:next.target_y});setRegions(regions.map((old,j)=>j===i?next:old))}}/><Text x={view==='source'?r.source_x:r.target_x} y={(view==='source'?r.source_y:r.target_y)-15/scale} text={'保护 '+(i+1)} fontSize={11/scale} fill="#1d6a3c" listening={false}/></Group>)}
 {draft&&<Rect {...draft} stroke="#42a46c" strokeWidth={2/scale} fill="rgba(49,142,85,.16)" listening={false}/>}
 </Group></Layer></Stage>}
 </div><div className="canvas-toolbar"><Segmented size="small" aria-label="画布工具" value={mode} onChange={v=>setMode(v as 'draw'|'pan')} options={[{value:'draw',icon:<SelectOutlined/>,label:'选区'},{value:'pan',icon:<DragOutlined/>,label:'平移'}]}/><div><Tooltip title="缩小"><Button type="text" aria-label="缩小" icon={<ZoomOutOutlined/>} onClick={()=>setZoom(Math.max(.4,zoom-.2))}/></Tooltip><span>{Math.round(scale*100)}%</span><Button type="text" aria-label="放大" icon={<ZoomInOutlined/>} onClick={()=>setZoom(Math.min(4,zoom+.2))}/><Button type="text" aria-label="适应画布" icon={<ExpandOutlined/>} onClick={()=>{setZoom(1);setPan({x:0,y:0})}}/></div></div></div>
}
