import { api } from "../../../scripts/api.js";
import { openCompilePanel } from "./compile_panel.js";

// Manual ChatGPT/API exchange: no browser-session access or external calls.
export function openPlannerPanel() {
  document.querySelector('.wa-planner-overlay')?.waDispose?.();
  const el=(tag,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
  const overlay=el('div');overlay.className='wa-overlay wa-planner-overlay open';const panel=el('section');panel.className='wa-panel';panel.setAttribute('role','dialog');
  const header=el('header'),close=el('button','关闭');header.append(el('h2','第二阶段 · 大模型规划交换（本地校验）'),close);
  const main=el('main'),notice=el('p','本入口不调用付费API、不读取ChatGPT登录。导出上下文后由你发给ChatGPT，再粘贴纯JSON回复校验。需求文本会写入导出文件，请勿填写密钥或敏感资料；不包含图片和input文件列表。当前不执行/编译/排队，后续须第三阶段编译。');
  const text=el('textarea');text.maxLength=4000;text.setAttribute('aria-label','规划需求');text.placeholder='例如：参考图换装';
  const prepare=el('button','生成可交给大模型的上下文'),exportContext=el('button','导出给ChatGPT的提示词'),validate=el('button','校验导入方案（不运行）'),exportPlan=el('button','导出规划记录（不是ComfyUI工作流）');
  const compile=el('button','下一步：绑定图片并编译真实工作流');compile.disabled=true;
  exportContext.disabled=validate.disabled=exportPlan.disabled=true;
  const proposal=el('textarea');proposal.maxLength=32768;proposal.setAttribute('aria-label','大模型JSON方案');proposal.placeholder='粘贴模型返回的完整JSON，不要Markdown代码块';
  const status=el('p','先填写需求，生成上下文。'),report=el('pre');report.style.whiteSpace='pre-wrap';report.style.overflowWrap='anywhere';
  let epoch=0,active,context,accepted;
  function clearPlan(){accepted=null;exportPlan.disabled=compile.disabled=true;}
  function reset(){epoch++;active?.abort();context=null;clearPlan();exportContext.disabled=validate.disabled=true;prepare.disabled=false;report.textContent='';}
  overlay.waDispose=()=>{reset();overlay.remove();};close.addEventListener('click',overlay.waDispose);text.addEventListener('input',reset);
  proposal.addEventListener('input',()=>{epoch++;active?.abort();clearPlan();prepare.disabled=false;validate.disabled=!context;});
  async function request(path,body,signal){
    const response=await api.fetchApi('/workflow-architect/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store',redirect:'error',signal});
    if(!response.headers.get('content-type')?.includes('application/json'))throw new Error(`后端未加载新接口或返回非JSON（HTTP ${response.status}），请重启ComfyUI`);
    if(!response.body)throw new Error('接口无正文');const reader=response.body.getReader();let total=0;const chunks=[];
    try{while(true){const r=await reader.read();if(r.done)break;total+=r.value.length;if(total>2*1024*1024||chunks.length>=4096)throw new Error('响应超限');chunks.push(r.value);}const bytes=new Uint8Array(total);let at=0;for(const c of chunks){bytes.set(c,at);at+=c.length;}const result=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));if(!response.ok||!result.ok)throw new Error(result.error?.message||'新接口未加载，请重启后端');return result.data;}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  prepare.addEventListener('click',async()=>{
    reset();proposal.value='';if(!text.value.trim()){status.textContent='请填写需求';return;}
    const e=epoch,controller=new AbortController();active=controller;const timer=setTimeout(()=>controller.abort(),30000);prepare.disabled=true;
    try{await request('refresh',{},controller.signal);const value=await request('planner/context',{text:text.value,profile:'stable'},controller.signal);if(e!==epoch||controller.signal.aborted||!overlay.isConnected)return;if(value.phase!==2||value.api_called!==false||value.execution_authorized!==false)throw new Error('规划上下文合同错误');context=value;const ready=value.status==='ready-for-proposal';exportContext.disabled=validate.disabled=!ready;report.textContent=JSON.stringify(value.context,null,2);status.textContent=ready?'上下文已就绪：导出提示词，交给ChatGPT，再粘贴纯JSON回复。没有自动外发。':'无兼容具体模板；先解决依赖，不让模型编造节点。';}catch(error){if(e===epoch&&overlay.isConnected)status.textContent=`生成失败：${error.message}`;}finally{clearTimeout(timer);if(e===epoch)prepare.disabled=false;}
  });
  validate.addEventListener('click',async()=>{
    if(!context)return;clearPlan();let parsed;try{parsed=JSON.parse(proposal.value);}catch{status.textContent='请输入完整纯JSON对象';return;}
    const e=++epoch,controller=new AbortController();active?.abort();active=controller;const timer=setTimeout(()=>controller.abort(),30000);validate.disabled=true;prepare.disabled=true;
    try{const value=await request('planner/validate',{text:text.value,profile:'stable',proposal:parsed},controller.signal);if(e!==epoch||controller.signal.aborted||!overlay.isConnected)return;if(value.phase!==2||value.execution_authorized!==false)throw new Error('方案合同错误');accepted=value;exportPlan.disabled=compile.disabled=false;report.textContent=JSON.stringify(value,null,2);status.textContent='方案通过校验。请点下一步绑定图片并编译；规划记录不能直接载入ComfyUI。';}catch(error){if(e===epoch&&overlay.isConnected)status.textContent=`校验拒绝：${error.message}`;}finally{clearTimeout(timer);if(e===epoch){validate.disabled=!context;prepare.disabled=false;}}
  });
  function save(value,name,type){const url=URL.createObjectURL(new Blob([value],{type}));const a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  exportContext.addEventListener('click',()=>{if(context)save(context.manual_prompt,'workflow-planner-prompt.txt','text/plain');});
  exportPlan.addEventListener('click',()=>{if(accepted)save(JSON.stringify(accepted,null,2),'workflow-planner-validated.json','application/json');});
  compile.addEventListener('click',()=>{if(accepted)openCompilePanel({text:text.value,proposal:accepted.proposal});});
  const copyPrompt=el('button','复制完整提示词（含回复格式）');
  copyPrompt.addEventListener('click',async()=>{if(!context){status.textContent='请先生成上下文';return;}try{await navigator.clipboard.writeText(context.manual_prompt);status.textContent='已复制完整提示词；不要只复制候选列表。';}catch{status.textContent='剪贴板不可用，请导出提示词文件。';}});
  main.append(notice,text,prepare,exportContext,copyPrompt,proposal,validate,compile,exportPlan,status,report);panel.append(header,main);overlay.append(panel);document.body.append(overlay);
}
