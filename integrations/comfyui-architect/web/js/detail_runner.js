// Dependency-injected serial runner. Journal must persist before every POST.
export async function runDetailJobs({jobs,journal,save,submit,history,wait,stopped,changed}) {
  if(!Array.isArray(jobs)||jobs.length!==6||journal.entries.length!==6)throw new Error('必须六个完整任务');
  for(let i=0;i<6;i++) {
    if(stopped())return;
    const entry=journal.entries[i];
    if(entry.status==='done')continue;
    if(entry.status!=='ready')throw new Error('有未知/失败/已提交任务；只读恢复，不自动重投');
    entry.status='submitting';save(journal);changed();
    if(stopped())return;
    let result;
    try{result=await submit(jobs[i].api_prompt,journal.id);}catch{entry.status='unknown';save(journal);throw new Error('提交响应不明：已停止，不能自动重试');}
    if(!result||typeof result.prompt_id!=='string'||!/^[0-9a-f-]{36}$/i.test(result.prompt_id)){entry.status='unknown';save(journal);throw new Error('提交编号异常；不再提交');}
    entry.prompt_id=result.prompt_id;entry.status='submitted';save(journal);changed();
    const deadline=Date.now()+15*60*1000;
    while(true){
      if(stopped())return;
      if(Date.now()>deadline)throw new Error('观察到期；任务可能继续，只读恢复，不重试');
      const found=await history(entry.prompt_id);
      if(found){
        if(found.status?.status_str!=='success'){entry.status='failed';save(journal);throw new Error('模块执行失败，已暂停，不重试');}
        const images=Object.values(found.outputs||{}).flatMap(v=>v.images||[]);
        if(images.length!==1||!validImage(images[0])){entry.status='unknown';save(journal);throw new Error('输出关系异常，已暂停');}
        entry.image=images[0];entry.status='done';save(journal);changed();break;
      }
      await wait();
    }
  }
}
export function validImage(image){return Boolean(image&&Object.keys(image).sort().join(',')==='filename,subfolder,type'&&typeof image.filename==='string'&&/^[^/\\\u0000-\u001f]{1,200}$/.test(image.filename)&&image.type==='output'&&typeof image.subfolder==='string'&&image.subfolder.length<200&&!/[\\:\u0000-\u001f]/.test(image.subfolder)&&!image.subfolder.startsWith('/')&&!image.subfolder.split('/').includes('..'));}
export function validateJournal(j){
  const ids=['hero','benefits','details','scene','specs','closing'];
  if(!j||j.version!==1||!/^[0-9a-f-]{36}$/i.test(j.id)||!/^[0-9a-f]{64}$/.test(j.bundle_sha256)||!/^[0-9a-f]{64}$/.test(j.plan_sha256)||!Array.isArray(j.entries)||j.entries.length!==6)throw new Error('进度结构异常');
  for(let i=0;i<6;i++){
    const e=j.entries[i];if(!e||e.id!==ids[i]||!['ready','submitting','unknown','submitted','failed','done'].includes(e.status)||Object.keys(e).some(k=>!['id','status','prompt_id','image','retry_count','previous_prompt_id'].includes(k)))throw new Error('模块进度不合法');
    if(['submitted','failed','done'].includes(e.status)&&!/^[0-9a-f-]{36}$/i.test(e.prompt_id))throw new Error('缺原任务编号');
    if(e.status==='done'&&!validImage(e.image))throw new Error('输出编号不合法');
    if(e.retry_count!==undefined&&e.retry_count!==1)throw new Error('重试计数不合法');
  }
  return j;
}

export function wrapCopy(ctx,text,width){
  const lines=[];let line='';
  for(const char of text){if(ctx.measureText(line+char).width>width&&line){lines.push(line);line='';}line+=char;}
  if(line)lines.push(line);return lines;
}
export async function renderDetailPage({canvas,bundle,entries,load}){
  if(entries.length!==6||entries.some(e=>e.status!=='done'||!validImage(e.image)))throw new Error('须六张已核对输出');
  canvas.width=768;canvas.height=6*1120;const ctx=canvas.getContext('2d');
  ctx.fillStyle='#ffffff';ctx.fillRect(0,0,canvas.width,canvas.height);
  for(let i=0;i<6;i++){
    const y=i*1120;const image=await load(entries[i].image);
    try{if(image.width!==512||image.height!==640)throw new Error('输出尺寸不符，拒绝错图拼接');ctx.drawImage(image,0,y+160,768,960);}finally{image.close?.();}
    ctx.fillStyle='#111827';ctx.font='bold 28px sans-serif';ctx.fillText(bundle.product_name+' · '+bundle.sections[i].title,24,y+38,720);
    ctx.font='19px sans-serif';let lineY=y+67;
    for(const copy of bundle.sections[i].copy){for(const line of wrapCopy(ctx,copy,720)){if(lineY>y+150)throw new Error('文案超过版面，请减少每模块事实；不会静默截断');ctx.fillText(line,24,lineY);lineY+=22;}}
  }
  return canvas;
}
