import { api } from '../../../scripts/api.js';
import { openCompilePanel } from './compile_panel.js';

export function openOfflineAiPanel(){
  document.querySelector('.wa-offline-overlay')?.waDispose?.();
  const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const overlay=el('div');overlay.className='wa-overlay wa-offline-overlay open';const panel=el('section');panel.className='wa-panel';panel.setAttribute('role','dialog');
  const header=el('header'),close=el('button','关闭');header.append(el('h2','本地离线AI · 知识问答与受控规划'),close);
  const main=el('main');main.append(el('p','Qwen3-4B CPU本地推理，首次模型下载后无需云端。资料由SQLite检索提供，不是训练进模型或Embedding。只发本次文本和选中的内置资料到127.0.0.1，不读取凭证、图片或历史。模型可能答错，结果需审核；不自动安装节点或运行生图。'));
  const status=el('p','先由本机启动工具开启离线模型。此面板不启动程序。'),check=el('button','检查本地模型'),mode=el('select');mode.setAttribute('aria-label','离线助手模式');
  for(const [v,t] of [['knowledge','知识问答（带检索引用）'],['plan','工作流规划（仅当前兼容模板）']]){const o=el('option',t);o.value=v;mode.append(o);}
  const text=el('textarea');text.maxLength=1000;text.rows=5;text.setAttribute('aria-label','离线助手需求');
  const consent=el('input');consent.type='checkbox';const label=el('label','确认仅把本次非敏感文本发送给已启动的本机模型，允许CPU推理；不代表允许执行工作流。');label.prepend(consent);
  const ask=el('button','单次离线推理（不生图）'),next=el('button','交给原编译器绑定图片与模型'),report=el('pre');report.style.whiteSpace='pre-wrap';report.style.overflowWrap='anywhere';next.disabled=true;
  let epoch=0,active,busy=false,accepted=null;
  function clear(){epoch++;active?.abort();accepted=null;next.disabled=true;report.textContent='';}
  text.addEventListener('input',clear);mode.addEventListener('change',clear);consent.addEventListener('change',clear);
  overlay.waDispose=()=>{clear();overlay.remove();};close.addEventListener('click',overlay.waDispose);
  async function request(path,body,signal){
    const response=await api.fetchApi('/workflow-architect/offline/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store',redirect:'error',signal});
    if(!response.headers.get('content-type')?.includes('application/json'))throw Error('离线接口未加载，请保存画布后重启ComfyUI');
    if(!response.body)throw Error('缺少正文');const reader=response.body.getReader(),chunks=[];let length=0;
    try{while(true){const r=await reader.read();if(r.done)break;if(!r.value.length)continue;length+=r.value.length;if(length>512*1024||chunks.length>=4096)throw Error('响应超限');chunks.push(r.value);}
      const bytes=new Uint8Array(length);let at=0;for(const c of chunks){bytes.set(c,at);at+=c.length;}
      const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));if(!response.ok||!value.ok)throw Error(value.error?.message||'本地接口拒绝');
      if(value.data.execution_authorized!==false||value.data.external_api_called!==false)throw Error('结果违反只读本地合同');
      if(value.data.status==='local-answer-not-executed'&&value.data.answer_kind!=='verified-source-excerpts')throw Error('后端仍为旧版自由回答，请保存画布后重启ComfyUI加载原文核验版');
      return value.data;
    }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  async function run(fn){if(busy)return;clear();busy=true;check.disabled=ask.disabled=true;const e=epoch,c=new AbortController();active=c;const timer=setTimeout(()=>c.abort(),124000);
    const current=()=>e===epoch&&!c.signal.aborted&&overlay.isConnected;
    try{await fn(c.signal,current);}catch(error){if(current())status.textContent='未完成：'+error.message;}
    finally{clearTimeout(timer);busy=false;check.disabled=ask.disabled=false;}
  }
  check.addEventListener('click',()=>run(async(signal,current)=>{const data=await request('status',{},signal);if(!current())return;status.textContent=data.ready?'本机模型已响应，可手动提交单次文本。':'本机模型未就绪；不会下载、启动或回退到云端。';report.textContent=JSON.stringify(data,null,2);}));
  ask.addEventListener('click',()=>{
    if(!text.value.trim()||text.value.length>1000||!consent.checked){status.textContent='先填写1至1000字符需求并确认本机推理。';return;}
    const submitted={text:text.value,mode:mode.value,confirmed:true};
    return run(async(signal,current)=>{status.textContent='CPU推理中，最多120秒；不自动重试。';const data=await request('assist',submitted,signal);if(!current())return;report.textContent=JSON.stringify(data,null,2);
      if(data.status==='grounded-local-plan-not-executed'&&data.accepted?.proposal){accepted={text:submitted.text,proposal:data.accepted.proposal};next.disabled=false;}
      status.textContent=data.status==='blocked-no-compatible-template'?'当前没有兼容模板，只保留资料，不伪造工作流。':'已完成本机推理；回答和引用仍须审核，未生成图片或执行任务。';});
  });
  next.addEventListener('click',()=>{if(accepted)openCompilePanel(accepted);});
  main.append(check,mode,text,label,ask,next,status,report);panel.append(header,main);overlay.append(panel);document.body.append(overlay);
}
