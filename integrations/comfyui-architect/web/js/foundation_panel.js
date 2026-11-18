import { api } from "../../../scripts/api.js";

export function openFoundationPanel() {
  document.querySelector(".wa-foundation-overlay")?.waDispose?.();
  const el=(tag,text="")=>{const e=document.createElement(tag);e.textContent=text;return e;};
  const overlay=el("div");overlay.className="wa-overlay wa-foundation-overlay open";
  const panel=el("section");panel.className="wa-panel";panel.setAttribute("role","dialog");
  const header=el("header"),close=el("button","关闭");header.append(el("h2","第一阶段 · 本地知识与环境报告"),close);
  const main=el("main"),notice=el("p","只读取本地资料和当前节点签名/模型选项，不加载小模型、不调用线上API、不生成图片。静态兼容不代表模型文件完整或质量合格。报告可导出，供后续规划使用。");
  const text=el("textarea");text.maxLength=4000;text.setAttribute("aria-label","底座验收需求");text.placeholder="例如：电商主图／局部重绘精修／参考图换装";
  const run=el("button","扫描并生成来源与兼容报告"),download=el("button","导出底座报告JSON");download.disabled=true;
  const status=el("p","输入需求后扫描。本入口不编译或排队。"),report=el("pre");report.style.whiteSpace="pre-wrap";report.style.overflowWrap="anywhere";
  let epoch=0,active,value;
  const clear=()=>{epoch++;active?.abort();value=null;download.disabled=true;run.disabled=false;report.textContent="";};
  overlay.waDispose=()=>{clear();overlay.remove();};close.addEventListener("click",overlay.waDispose);text.addEventListener("input",clear);
  async function request(path,body,signal){
    const response=await api.fetchApi("/workflow-architect/"+path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal,cache:"no-store",redirect:"error"});
    if(!response.body)throw new Error("接口无正文");const reader=response.body.getReader();const chunks=[];let size=0;
    try{while(true){const r=await reader.read();if(r.done)break;size+=r.value.length;if(size>2*1024*1024 || chunks.length>=4096)throw new Error("报告超限");chunks.push(r.value);}const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}const result=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));if(!response.ok || !result.ok)throw new Error(result.error?.message||"报告接口不可用，请重启后端");return result.data;}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  run.addEventListener("click",async()=>{
    clear();if(!text.value.trim()){status.textContent="请填写需求。";return;}
    const e=epoch,controller=new AbortController();active=controller;const timer=setTimeout(()=>controller.abort(),30000);run.disabled=true;
    try{await request("refresh",{},controller.signal);const result=await request("foundation/report",{text:text.value,profile:"stable"},controller.signal);if(e!==epoch||controller.signal.aborted||!overlay.isConnected)return;if(result.phase!==1||result.api_called!==false||result.execution_authorized!==false)throw new Error("底座合同不匹配");value=result;download.disabled=false;status.textContent=`本地文档${result.document_count}，当前节点${result.registry_node_count}；具体候选${result.candidates.length}。请查看issues阻塞项与sources来源；不是执行许可。`;report.textContent=JSON.stringify(result,null,2);}catch(error){if(e===epoch&&overlay.isConnected)status.textContent=`检查失败：${error.message}`;}finally{clearTimeout(timer);if(e===epoch)run.disabled=false;}
  });
  download.addEventListener("click",()=>{if(!value)return;const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));const a=el("a");a.href=url;a.download="workflow-architect-foundation-report.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  main.append(notice,text,run,download,status,report);panel.append(header,main);overlay.append(panel);document.body.append(overlay);
}
