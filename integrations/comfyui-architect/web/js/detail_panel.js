import { api } from '../../../scripts/api.js';
import { runDetailJobs,renderDetailPage,validateJournal } from './detail_runner.js';

export function openDetailPanel(){
  document.querySelector('.wa-detail-overlay')?.waDispose?.();
  const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const overlay=el('div');overlay.className='wa-overlay wa-detail-overlay open';const panel=el('section');panel.className='wa-panel';
  const main=el('main'),header=el('header'),close=el('button','关闭');header.append(el('h2','六模块详情页 · 本机连续生成'),close);
  main.append(el('p','首版固定商品展示/卖点/细节/场景/规格/总览，均为商品场景合成，不是自动细节裁切或专业文字规格表。资料由你提供，文字后期排版；GPT只可选事实编号和场景。真实付费规划未启用。六张图最多十二次本机采样，缓存命中可能减少。关闭只停止后续提交，不取消已提交任务。'));
  const name=el('input');name.placeholder='商品名称（不要密钥）';name.maxLength=60;
  const facts=el('textarea');facts.placeholder='真实资料，每行一条，1至12条，每条最多100字；规格、材质请提供准确值';
  const prepare=el('button','读取环境并生成六模块草案'),exportPrompt=el('button','导出给GPT的提示词'),validate=el('button','校验方案并预览文案');
  const plan=el('textarea');plan.placeholder='可使用本地默认草案，或粘贴GPT返回的严格JSON';plan.maxLength=32768;
  name.setAttribute('aria-label','详情商品名称');facts.setAttribute('aria-label','详情真实资料');plan.setAttribute('aria-label','详情六模块方案');
  const apiPlan=el('button','API规划六模块（可能收费）');
  const options=el('div'),consent=el('input');consent.type='checkbox';const label=el('label','已核对全部事实、素材权利及六张本机任务，确认编译后再运行。');label.prepend(consent);
  const compile=el('button','编译六份真实工作流'),exportBundle=el('button','导出任务包（非画布文件）'),workflow=el('button','导出六份画布工作流'),start=el('button','确认后连续运行六张'),stop=el('button','暂停后续提交'),recover=el('button','只读恢复上次进度'),long=el('button','拼接排版并导出长图'),clear=el('button','清理本机进度指针');
  const status=el('p','先填写真实商品资料。'),report=el('pre');report.style.whiteSpace='pre-wrap';
  const retry=el('button','仅明确失败模块：允许人工重试一次');
  const split=el('button','导出六张原始分图');
  let epoch=0,active=null,busy=false,closed=false,context=null,bundle=null,modelFields=[],moduleFields=[],imageSelect=null,journal=null;
  const key='workflow-architect-detail-journal-v1';
  const product=()=>({name:name.value,facts:facts.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean)});
  const buttons=()=>{for(const b of [prepare,validate,compile,exportPrompt,exportBundle,workflow,start,recover,long,clear,apiPlan,retry,split])b.disabled=busy;};
  function reset(){epoch++;active?.abort();context=bundle=null;options.replaceChildren();report.textContent='';consent.checked=false;}
  for(const input of [name,facts])input.addEventListener('input',reset);
  plan.addEventListener('input',()=>{epoch++;active?.abort();bundle=null;});
  function save(value){const raw=JSON.stringify(value);if(raw.length>16384)throw new Error('进度超限');localStorage.setItem(key,raw);if(localStorage.getItem(key)!==raw)throw new Error('进度保存回读失败，不继续提交');journal=value;}
  function read(){const raw=localStorage.getItem(key);if(!raw)return null;if(raw.length>16384)throw new Error('进度超限');return validateJournal(JSON.parse(raw));}
  function show(){report.textContent=JSON.stringify(journal||bundle?.sections||{},null,2);}
  async function bounded(response,limit){if(!response.body)throw new Error('响应无正文');const reader=response.body.getReader(),chunks=[];let size=0;try{while(true){const part=await reader.read();if(part.done)break;size+=part.value.length;if(size>limit||chunks.length>=4096)throw new Error('响应超限');chunks.push(part.value);}const bytes=new Uint8Array(size);let at=0;for(const chunk of chunks){bytes.set(chunk,at);at+=chunk.length;}return bytes;}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}}
  async function request(path,body,signal){const controller=new AbortController(),abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();const timer=setTimeout(abort,30000);try{const r=await api.fetchApi(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',redirect:'error',signal:controller.signal});const bytes=await bounded(r,4*1024*1024);let data;try{data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));}catch{throw new Error('后端未加载新接口或JSON错误，请重启ComfyUI');}if(!r.ok||data.ok===false)throw new Error(data.error?.message||'请求失败');return path.startsWith('/workflow-architect/')?data.data:data;}finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}}
  async function action(fn){if(busy)return;busy=true;buttons();const current=epoch;active=new AbortController();const timer=setTimeout(()=>active?.abort(),30000);try{await fn(active.signal,current);}catch(e){if(!closed)status.textContent=e.message;}finally{clearTimeout(timer);busy=false;buttons();}}
  const valid=(current,signal)=>{if(closed||current!==epoch||signal.aborted)throw new Error('页面或资料已变化，丢弃旧响应');};
  function download(value,filename,type='application/json'){const url=URL.createObjectURL(new Blob([typeof value==='string'?value:JSON.stringify(value,null,2)],{type}));const a=el('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  prepare.addEventListener('click',()=>action(async(signal,e)=>{
    await request('/workflow-architect/refresh',{},signal);const value=await request('/workflow-architect/detail/context',{product:product()},signal);valid(e,signal);context=value;bundle=null;plan.value=JSON.stringify(value.default_plan,null,2);options.replaceChildren();modelFields=[];moduleFields=[];
    function select(choices,title){const node=el('select');node.setAttribute('aria-label',title);const empty=el('option','请选择'+title);empty.value='';node.append(empty);for(const value of choices){const opt=el('option',value);opt.value=value;node.append(opt);}options.append(el('p',title),node);node.addEventListener('change',()=>{bundle=null;});return node;}
    for(const field of value.selection.models)modelFields.push([field.binding,select(field.choices,field.binding)]);imageSelect=select(value.selection.images,'原始商品图');status.textContent='草案已生成，请核对六模块事实编号与风格；默认不外发、不运行。';report.textContent=JSON.stringify(value.context.product,null,2);
    for(const id of ['hero','benefits','details','scene','specs','closing'])moduleFields.push([id,select(value.selection.images,id+'独立原图（未选则沿用主图）')]);
  }));
  exportPrompt.addEventListener('click',()=>{if(context)download(context.manual_prompt,'detail-page-gpt-prompt.txt','text/plain');});
  apiPlan.addEventListener('click',()=>action(async(signal,e)=>{
    if(!context)throw new Error('请先读取环境并生成上下文');
    const state=await request('/workflow-architect/api/status',{},signal);valid(e,signal);
    if(!state.configured)throw new Error('API未配置或缺可信费用来源；保持不联网，可用本地草案或手动GPT交换');
    if(!confirm('向实际配置目标发送商品名称及事实进行单次可能收费规划？不会上传图片。目标：'+JSON.stringify(state.profile)))return;
    const value=await request('/workflow-architect/detail/propose',{product:product(),context_digest:context.context_digest,profile_digest:state.profile_digest,confirm_external_text:true,confirm_one_paid_call:true},signal);valid(e,signal);bundle=null;plan.value=JSON.stringify(value.proposal,null,2);report.textContent=JSON.stringify(value.sections,null,2);status.textContent='API方案已做事实引用校验，仍需人工核对后编译；不代表生图质量通过。';
  }));
  validate.addEventListener('click',()=>action(async(signal,e)=>{const value=await request('/workflow-architect/detail/validate',{product:product(),plan:JSON.parse(plan.value)},signal);valid(e,signal);report.textContent=JSON.stringify(value.sections,null,2);status.textContent='事实引用通过；请人工核对内容真实性。';}));
  compile.addEventListener('click',()=>action(async(signal,e)=>{
    if(!consent.checked||!context)throw new Error('请先读取环境并人工确认');
    const value=await request('/workflow-architect/detail/compile',{product:product(),plan:JSON.parse(plan.value),models:Object.fromEntries(modelFields.map(([id,node])=>[id,node.value])),image:imageSelect.value,module_images:Object.fromEntries(moduleFields.map(([id,node])=>[id,node.value||imageSelect.value])),confirmed:true},signal);valid(e,signal);bundle=value;show();status.textContent='六份真实工作流已编译，但尚未运行。';
  }));
  exportBundle.addEventListener('click',()=>{if(bundle)download(bundle,'detail-page-job-bundle.json');});
  workflow.addEventListener('click',()=>{if(bundle)for(const job of bundle.jobs)download(job.ui_workflow,'detail-'+job.id+'-workflow.json');});
  start.addEventListener('click',async()=>{
    if(busy||!bundle||!consent.checked)return;
    if(!confirm('开始最多六张本机生成？每张两个采样分支。失败暂停，不自动重试。不调用供应商API。'))return;
    busy=true;buttons();const e=epoch;active=new AbortController();
    try{
      const previous=read();if(previous&&(previous.bundle_sha256!==bundle.bundle_sha256||previous.entries.some(v=>!['ready','done'].includes(v.status))))throw new Error('已有进度不匹配或存在未结清任务；先只读恢复，不自动补投');
      const q=await request('/queue');if(q.queue_running?.length||q.queue_pending?.length)throw new Error('ComfyUI队列非空，请先完成原任务');
      if(closed||epoch!==e||active.signal.aborted)throw new Error('界面已变化，不提交');
      journal=previous||{version:1,id:crypto.randomUUID(),plan_sha256:bundle.plan_sha256,bundle_sha256:bundle.bundle_sha256,entries:bundle.jobs.map(j=>({id:j.id,status:'ready'}))};save(journal);
      await runDetailJobs({jobs:bundle.jobs,journal,save,submit:(prompt,client_id)=>request('/prompt',{prompt,client_id}),
        history:async id=>(await request('/history/'+id))[id],wait:()=>new Promise(r=>setTimeout(r,2000)),stopped:()=>closed||epoch!==e||active.signal.aborted,changed:show});
      status.textContent=journal.entries.every(v=>v.status==='done')?'六张完成，可排版导出。':'后续提交已暂停；已提交任务可能仍在运行。';
    }catch(error){status.textContent=error.message;}finally{busy=false;buttons();show();}
  });
  stop.addEventListener('click',()=>{epoch++;active?.abort();status.textContent='暂停后续提交；不取消服务器已提交任务。';});
  recover.addEventListener('click',()=>action(async(signal,e)=>{journal=read();if(!journal)throw new Error('没有进度');for(const entry of journal.entries){if(entry.status==='submitted'&&/^[0-9a-f-]{36}$/i.test(entry.prompt_id)){const value=(await request('/history/'+entry.prompt_id,undefined,signal))[entry.prompt_id];valid(e,signal);if(value?.status?.status_str==='success'){const images=Object.values(value.outputs||{}).flatMap(v=>v.images||[]);if(images.length===1){entry.image=images[0];entry.status='done';}}else if(value)entry.status='failed';}}save(journal);show();status.textContent='只读恢复完成；没有补交任务。';}));
  retry.addEventListener('click',()=>action(async(signal,e)=>{
    journal=read();if(!bundle||journal?.bundle_sha256!==bundle.bundle_sha256)throw new Error('先编译原绑定并恢复进度');
    const failed=journal.entries.filter(v=>v.status==='failed');if(failed.length!==1||failed[0].retry_count)throw new Error('仅允许一个已明确失败模块人工重试一次；未知提交不适用');
    const entry=failed[0];if(!/^[0-9a-f-]{36}$/i.test(entry.prompt_id))throw new Error('缺原任务编号');
    const value=(await request('/history/'+entry.prompt_id,undefined,signal))[entry.prompt_id];valid(e,signal);
    if(value?.status?.status_str!=='error')throw new Error('原任务未明确失败，不重试');
    if(!confirm('原任务已明确失败。允许这一模块再提交一次？可能再次消耗本机GPU，其他完成模块不重跑。'))return;
    entry.previous_prompt_id=entry.prompt_id;delete entry.prompt_id;entry.retry_count=1;entry.status='ready';save(journal);show();status.textContent='已保留原失败编号；点连续运行继续未完成模块，不会自动提交。';
  }));
  clear.addEventListener('click',()=>{if(!busy&&confirm('只清浏览器指针，不取消服务器任务。请先核对ComfyUI队列和历史，避免重复出图。')){localStorage.removeItem(key);journal=null;show();}});
  async function verifiedOutputs(signal,e){
    journal=read();if(!bundle||!journal||journal.bundle_sha256!==bundle.bundle_sha256)throw new Error('须重新编译同一方案/商品图/模型并恢复六张结果，才能排版');
    if(journal.entries.some(v=>v.status!=='done'))throw new Error('六张尚未全部完成，不导出部分详情页');
    for(const entry of journal.entries){
      if(!/^[0-9a-f-]{36}$/i.test(entry.prompt_id))throw new Error('缺原输出任务编号');
      const value=(await request('/history/'+entry.prompt_id,undefined,signal))[entry.prompt_id];valid(e,signal);
      const images=Object.values(value?.outputs||{}).flatMap(v=>v.images||[]);
      if(value?.status?.status_str!=='success'||images.length!==1||['filename','subfolder','type'].some(k=>images[0][k]!==entry.image?.[k]))throw new Error('当前服务器输出关系与本机进度不符，不拼接');
    }
  }
  split.addEventListener('click',()=>action(async(signal,e)=>{
    await verifiedOutputs(signal,e);
    for(const entry of journal.entries){const response=await api.fetchApi('/view?'+new URLSearchParams({filename:entry.image.filename,subfolder:entry.image.subfolder,type:entry.image.type}),{cache:'no-store',redirect:'error',signal});if(!response.ok)throw new Error('分图读取失败');const bytes=await bounded(response,20*1024*1024);valid(e,signal);const url=URL.createObjectURL(new Blob([bytes],{type:'image/png'})),a=el('a');a.href=url;a.download='detail-'+entry.id+'.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    status.textContent='已请求导出六张分图；浏览器可能要求允许多文件下载。';
  }));
  long.addEventListener('click',()=>action(async(signal,e)=>{
    await verifiedOutputs(signal,e);
    const canvas=document.createElement('canvas');await renderDetailPage({canvas,bundle,entries:journal.entries,load:async image=>{
      const response=await api.fetchApi('/view?'+new URLSearchParams({filename:image.filename,subfolder:image.subfolder,type:image.type}),{cache:'no-store',redirect:'error',signal});if(!response.ok)throw new Error('原图读取失败');const bytes=await bounded(response,20*1024*1024);return createImageBitmap(new Blob([bytes]));
    }});valid(e,signal);const blob=await new Promise(r=>canvas.toBlob(r,'image/png'));if(!blob)throw new Error('长图编码失败');const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download='detail-page-768x6720.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='长图已导出，请检查商品、文案与规格。';
  }));
  overlay.waDispose=()=>{closed=true;epoch++;active?.abort();overlay.remove();};close.addEventListener('click',overlay.waDispose);
  main.append(name,facts,prepare,exportPrompt,apiPlan,plan,validate,options,label,compile,exportBundle,workflow,start,stop,recover,retry,long,split,clear,status,report);panel.append(header,main);overlay.append(panel);document.body.append(overlay);
}
