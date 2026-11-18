import { api } from "../../../scripts/api.js";
import { app } from "../../../scripts/app.js";

// Preparation only: deliberately no queuePrompt or /prompt call.
export function openTryonPanel() {
  document.querySelector(".wa-tryon-overlay")?.waDispose?.();
  const el = (tag, text = "") => { const e = document.createElement(tag); e.textContent = text; return e; };
  const overlay = el("div"); overlay.className = "wa-overlay wa-tryon-overlay open";
  const panel = el("section"); panel.className = "wa-panel"; panel.setAttribute("role", "dialog");
  const header = el("header"), close = el("button", "关闭"); header.append(el("h2", "Klein 双图换装 · 实验"), close);
  const main = el("main"), warning = el("p", "实验模板：能运行不等于商品保真。花纹、脸部、手脚、背景可能改变；没有像素锁定。只编译/插入，不自动运行或下载。单张4步，CPU文字编码较慢。请先通过ComfyUI上传两张不同的图片到input，再检查依赖。建议英文描述服装细节。");
  const refresh = el("button", "检查依赖并刷新图片"), options = el("div");
  const text = el("textarea"); text.maxLength = 2000; text.setAttribute("aria-label", "换装细节");
  text.value = "Match the reference outfit structure, color, fabric, seams and pattern. Keep waistband below navel for low-rise garments. No extra skirt or shorts. Fully opaque lining.";
  const consent = el("input"); consent.type = "checkbox";
  const label = el("label", "确认有权使用人物/服装图，人物为成年人，理解实验不保证保真。只准备单张，手动排队。"); label.prepend(consent);
  const build = el("button", "编译双图模板（不运行）"), insert = el("button", "插入画布（替换当前图，不运行）"), download = el("button", "导出JSON");
  build.disabled = insert.disabled = download.disabled = true;
  const status = el("p", "先检查依赖。"), report = el("pre"); report.style.whiteSpace = "pre-wrap"; report.style.overflowWrap = "anywhere";
  let epoch = 0, active, preview, compiled, person, garment;
  overlay.waDispose = () => { epoch++; active?.abort(); overlay.remove(); };
  close.addEventListener("click", overlay.waDispose);
  const invalidate = () => { epoch++; active?.abort(); compiled = null; insert.disabled = download.disabled = true; build.disabled = !preview; refresh.disabled = false; };
  text.addEventListener("input", invalidate); consent.addEventListener("change", invalidate);
  async function request(path, payload, signal) {
    const response = await api.fetchApi("/workflow-architect/" + path, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload), signal, cache:"no-store", redirect:"error" });
    if (!response.body) throw new Error("接口无正文");
    const reader = response.body.getReader(); let total = 0; const chunks = [];
    try {
      while (true) { const {done,value} = await reader.read(); if(done) break; total += value.length; if(total > 2*1024*1024 || chunks.length >= 4096) throw new Error("响应超限"); chunks.push(value); }
      const bytes = new Uint8Array(total); let offset = 0; for(const c of chunks) {bytes.set(c,offset);offset+=c.length;}
      const envelope = JSON.parse(new TextDecoder("utf-8", {fatal:true}).decode(bytes));
      if(!response.ok || !envelope.ok) throw new Error(envelope.error?.message || "接口未加载，请重启ComfyUI");
      return envelope.data;
    } finally {await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  const select = (title, images) => { const wrapper = el("label", title), field = el("select"); field.setAttribute("aria-label", title); const blank = el("option", "请明确选择");blank.value="";field.append(blank);for(const name of images){const option=el("option",name);option.value=name;field.append(option);}field.addEventListener("change",()=>{consent.checked=false;invalidate();});wrapper.append(field);options.append(wrapper);return field; };
  refresh.addEventListener("click", async()=>{
    invalidate(); preview=null; build.disabled=true; options.replaceChildren();consent.checked=false;
    const e=epoch, controller=new AbortController();active=controller;const timer=setTimeout(()=>controller.abort(),30000);refresh.disabled=true;
    try {
      await request("refresh",{},controller.signal);
      const value=await request("people/tryon/prepare",{},controller.signal);
      if(e!==epoch || controller.signal.aborted || !overlay.isConnected)return;
      if(value.route!=="klein-tryon-experimental" || value.execution_authorized!==false || value.quality_approved!==false)throw new Error("实验合同不匹配");
      report.textContent=JSON.stringify({models:value.selection.models,missing_nodes:value.missing_nodes,missing_models:value.missing_models},null,2);
      if(value.missing_nodes.length || value.missing_models.length){status.textContent="依赖缺失，不自动下载或替换模型。";return;}
      preview=value;person=select("图1 人物原图",value.selection.images);garment=select("图2 服装参考图",value.selection.images);build.disabled=false;status.textContent="选择两张不同图片。固定576×768/4步/单张；输出仅为候选。";
    } catch(error){if(e===epoch && overlay.isConnected)status.textContent=`检查失败：${error.message}`;}
    finally{clearTimeout(timer);if(e===epoch)refresh.disabled=false;}
  });
  build.addEventListener("click",async()=>{
    if(!preview || !consent.checked || !person?.value || !garment?.value || person.value===garment.value || !text.value.trim()){status.textContent="请选不同的人物和服装图、填写细节并确认授权。";return;}
    invalidate();const e=epoch,controller=new AbortController();active=controller;const timer=setTimeout(()=>controller.abort(),30000);build.disabled=true;refresh.disabled=true;
    try{
      const value=await request("people/tryon/prepare",{build_draft:true,expected:preview.expected,authorized:true,model_overrides:preview.selection.models,person_image:person.value,garment_image:garment.value,prompt:text.value,width:576,height:768},controller.signal);
      if(e!==epoch || controller.signal.aborted || !overlay.isConnected)return;
      if(value.execution_authorized!==false || value.quality_approved!==false)throw new Error("实验合同不匹配");
      compiled=value.compiled;insert.disabled=download.disabled=!compiled?.ui_workflow;report.textContent=JSON.stringify(value.validation,null,2);status.textContent=compiled?"模板已编译，尚未运行。请保存当前画布后插入，再手动检查/排队。":"校验未通过，没有可插入模板。";
    }catch(error){if(e===epoch && overlay.isConnected){preview=null;status.textContent=`编译失败：${error.message}，请重新检查依赖。`;}}
    finally{clearTimeout(timer);if(e===epoch){build.disabled=!preview;refresh.disabled=false;}}
  });
  insert.addEventListener("click",async()=>{if(!compiled?.ui_workflow)return;if(!confirm("将替换当前画布，请先保存。此操作不会运行任务，是否继续？"))return;try{await app.loadGraphData(JSON.parse(JSON.stringify(compiled.ui_workflow)));status.textContent="已插入实验模板，未排队。人工检查两图、模型和提示词后再运行。";}catch{status.textContent="插入失败，可导出JSON手动载入。";}});
  download.addEventListener("click",()=>{if(!compiled?.ui_workflow)return;const url=URL.createObjectURL(new Blob([JSON.stringify(compiled.ui_workflow,null,2)],{type:"application/json"}));const a=el("a");a.href=url;a.download="klein-tryon-experimental.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  main.append(warning,refresh,options,text,label,build,insert,download,status,report);panel.append(header,main);overlay.append(panel);document.body.append(overlay);
}
