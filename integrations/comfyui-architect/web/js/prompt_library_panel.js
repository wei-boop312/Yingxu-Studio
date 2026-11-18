import { api } from '../../../scripts/api.js';

export function openPromptLibraryPanel(){
  document.querySelector('.wa-prompts-overlay')?.waDispose?.();
  const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const overlay=el('div');overlay.className='wa-overlay wa-prompts-overlay open';
  const panel=el('section');panel.className='wa-panel';panel.setAttribute('role','dialog');
  const close=el('button','关闭'),header=el('header');header.append(el('h2','AI提示词库 · 电商制作配方'),close);
  const main=el('main');main.append(el('p','本地原创配方，不调用大模型、不上传图片、不排队。示例变量须按实际素材修改；不要填写密钥或敏感资料。提示词/排版说明不是可导入ComfyUI的工作流，也不保证商品保真。'));
  const load=el('button','读取提示词库'),query=el('input'),category=el('select'),cards=el('select');
  query.setAttribute('aria-label','提示词关键词');query.placeholder='如：换装、精修、接口、视频';
  category.setAttribute('aria-label','提示词分类');cards.setAttribute('aria-label','提示词卡');
  const detail=el('div'),variables=el('div'),compose=el('button','填写并生成可复制提示词'),download=el('button','导出提示词资料JSON（不是工作流）');
  const output=el('textarea');output.readOnly=true;output.setAttribute('aria-label','已填写提示词与检查清单');output.rows=18;
  const status=el('p','点击读取后选择制作任务。');compose.disabled=download.disabled=true;
  let library=null,fields=[],filled=null,epoch=0,active,busy=false;
  function invalidate(){epoch++;active?.abort();filled=null;output.value='';download.disabled=true;}
  overlay.waDispose=()=>{invalidate();overlay.remove();};close.addEventListener('click',overlay.waDispose);
  async function request(path,body,signal){
    const response=await api.fetchApi('/workflow-architect/prompts/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store',redirect:'error',signal});
    if(!response.ok)throw Error('提示词接口未就绪或请求被拒绝（'+response.status+'），新版本须本人保存画布后重启');
    if(!response.body)throw Error('缺少响应正文');
    const reader=response.body.getReader(),chunks=[];let length=0;
    try{while(true){const r=await reader.read();if(r.done)break;if(!r.value.length)continue;length+=r.value.length;if(length>512*1024||chunks.length>=4096)throw Error('响应超限');chunks.push(r.value);}
      const bytes=new Uint8Array(length);let at=0;for(const c of chunks){bytes.set(c,at);at+=c.length;}
      const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));if(!value.ok)throw Error(value.error?.message||'提示词请求失败');
      if(value.data?.execution_authorized!==false||value.data?.api_called!==false)throw Error('提示词库只允许无执行资料');return value.data;
    }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  function show(){
    invalidate();fields=[];detail.replaceChildren();variables.replaceChildren();compose.disabled=true;
    const card=library?.cards.find(c=>c.id===cards.value);if(!card)return;
    detail.append(el('h3',card.title),el('p','用途：'+card.engine+'；'+card.note));
    detail.append(el('p','素材要求：'+card.inputs.join('；')),el('p','验收：'+card.checks.join('；')));
    const refs=library.research?.card_sources?.[card.id]||[];
    for(const source of library.research?.sources||[])if(refs.includes(source.id))detail.append(el('p','方法参考（非效果证明）：'+source.title+' — '+source.url+'；'+source.summary));
    if(['layout-only','planning-only','vton-input-only'].includes(card.engine))detail.append(el('p','此卡是制作/输入核对说明，不要直接送入采样器。'));
    for(const [key,value] of Object.entries(card.variables)){const label=el('label',key),input=el('input');input.value=value;input.maxLength=240;input.setAttribute('aria-label','变量 '+key);input.addEventListener('input',invalidate);label.append(input);variables.append(label);fields.push({key,input});}
    compose.disabled=false;
  }
  function filter(){
    const previous=cards.value;cards.replaceChildren();cards.value='';
    const terms=query.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    for(const c of library?.cards||[]){if(category.value&&c.category!==category.value)continue;if(!terms.every(t=>JSON.stringify(c).toLowerCase().includes(t)))continue;const o=el('option',c.title);o.value=c.id;cards.append(o);}
    if([...cards.children].some(o=>o.value===previous))cards.value=previous;
    show();status.textContent=cards.children.length+'条匹配；没有结果时请减少关键词。';
  }
  query.addEventListener('input',filter);category.addEventListener('change',filter);cards.addEventListener('change',show);
  async function action(fn){if(busy)return;invalidate();const e=epoch,c=new AbortController();active=c;busy=true;load.disabled=compose.disabled=true;const timer=setTimeout(()=>c.abort(),30000);
    const current=()=>{if(c.signal.aborted||e!==epoch||!overlay.isConnected)throw Error('界面已变化，不采用旧结果');};
    try{await fn(c.signal,current);}catch(err){if(e===epoch&&overlay.isConnected)status.textContent='未完成：'+err.message;}
    finally{clearTimeout(timer);busy=false;load.disabled=false;compose.disabled=!library?.cards.some(c=>c.id===cards.value);}
  }
  load.addEventListener('click',()=>action(async(signal,current)=>{
    const data=await request('search',{},signal);current();
    if(!Array.isArray(data.cards)||data.cards.length>100||!/^[0-9a-f]{64}$/.test(data.content_sha256))throw Error('提示词库合同错误');
    library=data;category.replaceChildren();const all=el('option','全部分类');all.value='';category.append(all);
    for(const name of new Set(data.cards.map(c=>c.category))){const o=el('option',name);o.value=name;category.append(o);}filter();
  }));
  compose.addEventListener('click',()=>action(async(signal,current)=>{
    if(!library||!cards.value)throw Error('请先选择卡片');
    const values=Object.fromEntries(fields.map(f=>[f.key,f.input.value]));
    if(Object.values(values).some(v=>!v.trim()||v.length>240||/[{}\x00-\x1f]/.test(v)))throw Error('变量须为1至240字符，不含花括号或控制字符');
    const id=cards.value,sha=library.content_sha256;
    const data=await request('render',{id,expected_sha256:sha,variables:values},signal);current();
    if(data.card?.id!==id||data.content_sha256!==sha)throw Error('返回卡片或库摘要不匹配');
    filled=data;output.value=[data.card.positive,data.card.negative?'\n可选负面（仅支持时使用）：\n'+data.card.negative:'','\n素材：'+data.card.inputs.join('；'),'\n验收：'+data.card.checks.join('；'),'\n'+data.card.note,...(data.card.references||[]).map(s=>'\n方法参考：'+s.title+' '+s.url),'\n'+data.notice].join('\n');download.disabled=false;
    status.textContent='可手动复制到相应提示词端口；用途为说明的卡不要送入采样器。未编译、未发送、未执行。';
  }));
  download.addEventListener('click',()=>{if(!filled)return;const url=URL.createObjectURL(new Blob([JSON.stringify(filled,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='prompt-recipe-'+filled.card.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  main.append(load,query,category,cards,detail,variables,compose,download,status,output);panel.append(header,main);overlay.append(panel);document.body.append(overlay);
}
