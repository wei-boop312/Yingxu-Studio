import { api } from "../../../scripts/api.js";
import { openCompilePanel } from "./compile_panel.js";

export function openApiSupportPanel(){
  document.querySelector('.wa-api-overlay')?.waDispose?.();
  const el=(tag,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
  const overlay=el('div');overlay.className='wa-overlay wa-api-overlay open';const panel=el('section');panel.className='wa-panel';panel.setAttribute('role','dialog');
  const header=el('header'),close=el('button','关闭');header.append(el('h2','模型/API支持 · 明确配置与单次规划'),close);
  const main=el('main'),notice=el('p','本地检索与手动ChatGPT模式可用，不代表已部署本地语言模型。Chat/Responses参数能力依模型而异。下方仅检查非密钥配置，不保存或启用；密钥不要粘贴。联网须宿主明确配置凭证读取器和独立费用批准，默认关闭。token上限不是供应商账单金额硬上限。');
  const statusButton=el('button','读取连接状态'),text=el('textarea');text.maxLength=4000;text.setAttribute('aria-label','API规划需求');text.placeholder='只填写文本需求，不填密钥或私人资料';
  const config=el('textarea');config.maxLength=4000;config.setAttribute('aria-label','非密钥API配置');config.value=JSON.stringify({endpoint:'https://hubway.cc/v1/responses',model:'gpt-5.5',protocol:'responses',token_parameter:'max_output_tokens',max_output_tokens:2048,timeout_seconds:30},null,2);
  const budgetNotice=el('p','已记录用户授权：gpt-5.5，单次最多0.02 USD。尚无可信分组价格、token上界和供应商限额证据，保持未连接；不能把密钥总余额当作每次硬限额。');
  const check=el('button','检查配置与请求结构（不联网）'),plan=el('button','单次API规划（可能收费，不生图）'),next=el('button','绑定并编译API方案');plan.disabled=next.disabled=true;
  const consent=el('input');consent.type='checkbox';const label=el('label','我明确批准向已配置目标发送本次文本并可能收费，理解失败/超时不证明未计费；不自动重试。');label.prepend(consent);
  const message=el('p','尚未读取状态，未连接。'),report=el('pre');report.style.whiteSpace='pre-wrap';report.style.overflowWrap='anywhere';
  let epoch=0,active,configured=false,accepted,profileDigest=null;
  function clear(){epoch++;active?.abort();accepted=null;next.disabled=true;consent.checked=false;plan.disabled=!configured;}
  overlay.waDispose=()=>{clear();overlay.remove();};close.addEventListener('click',overlay.waDispose);text.addEventListener('input',clear);config.addEventListener('input',()=>{clear();message.textContent='草稿配置改变不会启用或替换宿主已配置目标。';});
  async function request(path,body,signal){const response=await api.fetchApi('/workflow-architect/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store',redirect:'error',signal});if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('后端接口未加载，请重启ComfyUI');if(!response.body)throw new Error('正文缺失');const reader=response.body.getReader();let size=0;const chunks=[];try{while(true){const r=await reader.read();if(r.done)break;size+=r.value.length;if(size>2*1024*1024||chunks.length>=4096)throw new Error('响应超限');chunks.push(r.value);}const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));if(!response.ok||!value.ok)throw new Error(value.error?.message||'接口拒绝');return value.data;}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}}
  async function action(fn){clear();const e=epoch,controller=new AbortController();active=controller;const timer=setTimeout(()=>controller.abort(),32000);statusButton.disabled=check.disabled=plan.disabled=true;try{await fn(controller,e);}catch(error){if(e===epoch&&overlay.isConnected)message.textContent=`未完成：${error.message}`;}finally{clearTimeout(timer);if(e===epoch){statusButton.disabled=check.disabled=false;plan.disabled=!configured;}}}
  statusButton.addEventListener('click',()=>action(async(c,e)=>{const value=await request('api/status',{},c.signal);if(e!==epoch||c.signal.aborted||!overlay.isConnected)return;configured=value.configured===true;profileDigest=value.profile_digest;report.textContent=JSON.stringify(value,null,2);message.textContent=configured?'宿主已配置，但不代表站点实测通过。请核对上方实际profile，草稿配置不会改目标。':'API未配置：还缺准确模型ID/完整接口/费用批准及安全凭证端口。不会读取浏览器/环境密钥。';}));
  check.addEventListener('click',()=>action(async(c,e)=>{let profile;try{profile=JSON.parse(config.value);}catch{throw new Error('配置须为完整JSON');}const value=await request('api/profile-check',{text:text.value,profile_config:profile},c.signal);if(e!==epoch||c.signal.aborted||!overlay.isConnected)return;report.textContent=JSON.stringify(value,null,2);message.textContent='请求结构检查通过；未保存、未配置密钥、未发请求，不是连接成功。';}));
  plan.addEventListener('click',async()=>{
    if(!configured||!consent.checked||!text.value.trim()){message.textContent='先确认实际配置目标、文本与单次可能收费授权。';return;}
    await action(async(c,e)=>{const context=await request('planner/context',{text:text.value,profile:'stable'},c.signal);if(e!==epoch||c.signal.aborted||!overlay.isConnected)return;const value=await request('api/propose',{text:text.value,profile:'stable',context_digest:context.context_digest,profile_digest:profileDigest,confirm_external_text:true,confirm_one_paid_call:true},c.signal);if(e!==epoch||c.signal.aborted||!overlay.isConnected)return;if(value.api_called!==true||value.execution_authorized!==false)throw new Error('API方案合同错误');accepted=value;next.disabled=false;report.textContent=JSON.stringify(value,null,2);message.textContent='API方案已通过本地模板/来源/节点校验，尚未生图。费用仍以站点账单为准。';});
  });
  next.addEventListener('click',()=>{if(accepted)openCompilePanel({text:text.value,proposal:accepted.proposal});});
  main.append(notice,budgetNotice,statusButton,text,config,check,label,plan,next,message,report);panel.append(header,main);overlay.append(panel);document.body.append(overlay);
}
