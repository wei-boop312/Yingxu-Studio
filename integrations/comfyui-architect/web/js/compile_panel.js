import { api } from "../../../scripts/api.js";
import { app } from "../../../scripts/app.js";

export function openCompilePanel(initial = null) {
  document.querySelector('.wa-compile-overlay')?.waDispose?.();
  const el=(tag,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
  const overlay=el('div');overlay.className='wa-overlay wa-compile-overlay open';const panel=el('section');panel.className='wa-panel';panel.setAttribute('role','dialog');
  const header=el('header'),close=el('button','关闭');header.append(el('h2','第三阶段 · 具体工作流编译'),close);
  const main=el('main'),notice=el('p','导入第二阶段方案后重新核对本机环境。不重新扫描，避免无意义改变上下文；若已过期请重新生成方案。此处导出的才是ComfyUI工作流。编译/插入不会运行；插入会替换画布，请先保存。仅支持受控模板，花纹/身份/背景不保证。');
  const text=el('textarea');text.maxLength=4000;text.setAttribute('aria-label','编译原需求');
  const proposal=el('textarea');proposal.maxLength=65536;proposal.setAttribute('aria-label','待编译方案JSON');
  if(initial){text.value=initial.text;proposal.value=JSON.stringify(initial.proposal,null,2);}
  const check=el('button','核验方案并读取可选图片/模型'),options=el('div');
  const prompt=el('textarea');prompt.maxLength=2000;prompt.setAttribute('aria-label','实际工作流提示词');prompt.placeholder='请按实际任务填写提示词，不默认套用换装内容';
  const width=el('input'),height=el('input');
  for(const [input,title] of [[width,'实际输出宽度'],[height,'实际输出高度']]){input.type='number';input.min='256';input.max='1024';input.step='16';input.setAttribute('aria-label',title);input.addEventListener('input',()=>{consent.checked=false;invalidate();});}
  const consent=el('input');consent.type='checkbox';const label=el('label','确认素材使用权、成年人像及实验限制；只编译，不自动运行。');label.prepend(consent);
  const build=el('button','生成真实工作流（不运行）'),insert=el('button','插入真实工作流（替换画布）'),download=el('button','导出ComfyUI工作流JSON'),apiDownload=el('button','导出API任务JSON（非画布文件）');
  build.disabled=insert.disabled=download.disabled=apiDownload.disabled=true;
  const feedback=el('select');feedback.setAttribute('aria-label','显式修订原因');for(const [v,t] of [['','首次编译'],['INPUT_SELECTION','修改图片选择'],['MODEL_SELECTION','修改模型选择'],['DIMENSIONS','修改输出尺寸'],['PROMPT','修改提示词']]){const o=el('option',t);o.value=v;feedback.append(o);}
  const status=el('p','先核验方案，再选择图片和模型。'),report=el('pre');report.style.whiteSpace='pre-wrap';report.style.overflowWrap='anywhere';
  let epoch=0,active,preview,compiled,fields=[],attempts=0;
  const clearGraph=()=>{compiled=null;insert.disabled=download.disabled=apiDownload.disabled=true;};
  function invalidate(){epoch++;active?.abort();clearGraph();check.disabled=false;build.disabled=!preview || attempts>=3;}
  function reset(){invalidate();preview=null;build.disabled=true;options.replaceChildren();fields=[];attempts=0;consent.checked=false;width.value=height.value='';}
  overlay.waDispose=()=>{reset();overlay.remove();};close.addEventListener('click',overlay.waDispose);
  text.addEventListener('input',()=>{prompt.value='';reset();});proposal.addEventListener('input',()=>{prompt.value='';reset();});prompt.addEventListener('input',invalidate);consent.addEventListener('change',invalidate);feedback.addEventListener('change',invalidate);
  async function request(body,signal){
    const response=await api.fetchApi('/workflow-architect/planner/compile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store',redirect:'error',signal});
    if(!response.headers.get('content-type')?.includes('application/json'))throw new Error(`后端接口未加载或返回非JSON（HTTP ${response.status}），请重启ComfyUI并强制刷新`);
    if(!response.body)throw new Error('接口无正文');const reader=response.body.getReader();const chunks=[];let total=0;
    try{while(true){const r=await reader.read();if(r.done)break;total+=r.value.length;if(total>2*1024*1024||chunks.length>=4096)throw new Error('响应超限');chunks.push(r.value);}const bytes=new Uint8Array(total);let at=0;for(const c of chunks){bytes.set(c,at);at+=c.length;}const envelope=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));if(!response.ok||!envelope.ok)throw new Error(envelope.error?.message||'编译被拒绝');return envelope.data;}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  function base(){let p;try{p=JSON.parse(proposal.value);}catch{throw new Error('请输入完整方案JSON，不是上下文');}return {text:text.value,profile:'stable',proposal:p.proposal||p};}
  function select(title,choices,kind,key){const wrapper=el('label',title),field=el('select');field.setAttribute('aria-label',title);const blank=el('option','请明确选择');blank.value='';field.append(blank);for(const name of choices){const o=el('option',name);o.value=name;field.append(o);}field.addEventListener('change',()=>{consent.checked=false;invalidate();});wrapper.append(field);options.append(wrapper);fields.push({field,kind,key});}
  check.addEventListener('click',async()=>{
    reset();let body;try{body=base();}catch(error){status.textContent=error.message;return;}
    const e=epoch,controller=new AbortController();active=controller;const timer=setTimeout(()=>controller.abort(),30000);check.disabled=true;
    try{const value=await request(body,controller.signal);if(e!==epoch||controller.signal.aborted||!overlay.isConnected)return;if(value.phase!==3||value.execution_authorized!==false)throw new Error('编译合同错误');
      const output=value.selection?.output;if(!Number.isInteger(output?.width)||!Number.isInteger(output?.height)||typeof value.selection.prompt_default!=='string')throw new Error('后端缺少任务尺寸/提示词合同，请重启新版后端，不能猜测尺寸');
      preview=value;width.value=String(output.width);height.value=String(output.height);if(!prompt.value.trim())prompt.value=value.selection.prompt_default;
      for(const m of value.selection.models)select('模型 '+m.binding,m.choices,'models',m.binding);for(const role of value.selection.image_roles)select(role==='person_image'?'人物原图':role==='garment_image'?'服装参考图':'原图（重绘须含正确蒙版）',value.selection.images,'images',role);build.disabled=false;status.textContent='请核对任务尺寸 '+output.width+'×'+output.height+'，明确选择图片/模型并确认。每边256至1024、16倍数且最多786432像素；不符合时明确修改，不静默缩放。最多首次编译加两次人工修订，不自动运行。';}catch(error){if(e===epoch&&overlay.isConnected)status.textContent=`核验失败：${error.message}`;}finally{clearTimeout(timer);if(e===epoch)check.disabled=false;}
  });
  build.addEventListener('click',async()=>{
    if(!preview || attempts>=3 || !consent.checked || fields.some(f=>!f.field.value) || !prompt.value.trim()){status.textContent='请完整选择图片/模型并确认；超过修订上限须重新人工核验。';return;}
    if(attempts>0&&!feedback.value){status.textContent='再次编译须选择具体修订原因。';return;}
    const w=Number(width.value),h=Number(height.value);if(!Number.isInteger(w)||!Number.isInteger(h)||w<256||h<256||w>1024||h>1024||w%16||h%16||w*h>786432){status.textContent='请明确填写符合原范围的完整尺寸：256至1024、16倍数、最多786432像素；不自动调整。';return;}
    let body;try{body=base();}catch(error){status.textContent=error.message;return;}
    const models={},images={};for(const f of fields)(f.kind==='models'?models:images)[f.key]=f.field.value;
    invalidate();const e=epoch,controller=new AbortController();active=controller;const timer=setTimeout(()=>controller.abort(),30000);build.disabled=check.disabled=true;
    const attempt=attempts++;body={...body,build:true,authorized:true,models,images,prompt:prompt.value,width:w,height:h,repair_attempt:attempt,feedback_code:feedback.value||null};
    try{const value=await request(body,controller.signal);if(e!==epoch||controller.signal.aborted||!overlay.isConnected)return;if(value.phase!==3||value.execution_authorized!==false)throw new Error('编译合同错误');const graph=value.compiled?.ui_workflow;if(value.status!=='workflow-compiled-not-executed'||!graph?.nodes?.length||!graph?.links?.length)throw new Error('没有生成非空工作流');compiled=value.compiled;insert.disabled=download.disabled=apiDownload.disabled=false;report.textContent=JSON.stringify({validation:value.validation,nodes:graph.nodes.length,links:graph.links.length,workflow_sha256:value.workflow_sha256,repair_attempt:value.repair_attempt},null,2);status.textContent='真实工作流已生成，可插入或导出ComfyUI画布JSON。仍未运行；请先保存当前画布。';}catch(error){if(e===epoch&&overlay.isConnected)status.textContent=`编译失败：${error.message}。不会自动重试。`;}finally{clearTimeout(timer);if(e===epoch){check.disabled=false;build.disabled=attempts>=3;}}
  });
  insert.addEventListener('click',async()=>{if(!compiled?.ui_workflow||!confirm('替换当前画布？请先保存。不会运行任务。'))return;try{await app.loadGraphData(JSON.parse(JSON.stringify(compiled.ui_workflow)));status.textContent='已插入工作流，未排队；检查原图、服装、模型、提示词后手动运行。';}catch{status.textContent='插入失败，请导出ComfyUI工作流JSON手动载入。';}});
  function save(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  download.addEventListener('click',()=>{if(compiled)save(compiled.ui_workflow,'comfyui-workflow.json');});apiDownload.addEventListener('click',()=>{if(compiled)save(compiled.api_prompt,'comfyui-api-prompt.json');});
  main.append(notice,text,proposal,check,options,el('p','实际输出宽度 / 高度（核对后可明确修改）'),width,height,prompt,label,feedback,build,insert,download,apiDownload,status,report);panel.append(header,main);overlay.append(panel);document.body.append(overlay);
}
