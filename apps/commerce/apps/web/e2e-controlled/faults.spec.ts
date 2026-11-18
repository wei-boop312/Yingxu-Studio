import {test,expect,type Page} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,readdirSync,unlinkSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {createHash} from 'node:crypto';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const py=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const profile=JSON.parse(readFileSync(path.join(root,'.local/runtime.json'),'utf8'));
const evidence:Array<any>=[];const suiteStartedAt=new Date().toISOString();let browserVersion='not_observed';
function admin(args:string[]){return JSON.parse(execFileSync(py,['scripts/admin.py',...args],{cwd:root,env:{...process.env,ADMIN_DATABASE_URL:profile.ADMIN_DATABASE_URL},encoding:'utf8'}))}
function worker(task:string,mode:string){return JSON.parse(execFileSync(py,['scripts/run_fake_task.py','--task',task,'--mode',mode],{cwd:root,encoding:'utf8'}))}
async function setup(page:Page){
 const provision=JSON.parse(execFileSync(py,['scripts/create_e2e_account.py'],{cwd:root,encoding:'utf8'}));const account=JSON.parse(readFileSync(provision.account_file,'utf8'));
 await page.goto('/');await page.getByLabel('邮箱',{exact:true}).fill(account.email);await page.getByLabel('密码',{exact:true}).fill(account.password);await page.getByRole('button',{name:'进入工作台'}).click();await expect(page.getByRole('heading',{name:'视觉工作台',exact:true})).toBeVisible();
 const headers={'Origin':profile.APP_ORIGIN,'X-CSRF-Token':(await page.context().cookies()).find(c=>c.name==='visual_csrf')!.value};
 const product=await page.request.post('/api/v1/products',{headers,data:{name:'故障验收隔离产品',category:'generic'}});expect(product.status()).toBe(201);const p=await product.json();const data=readFileSync(new URL('../e2e/fixtures/product.png',import.meta.url));
 const intent=await page.request.post('/api/v1/assets/upload-intents',{headers,data:{product_id:p.id,filename:'fixture.png',mime:'image/png',size:data.length}});expect(intent.status()).toBe(201);const u=await intent.json();expect((await page.request.put(u.upload_url,{data,headers:u.headers})).status()).toBe(200);expect((await page.request.post('/api/v1/assets/upload-intents/'+u.intent_id+'/complete',{headers})).status()).toBe(200);
 await page.reload();await expect(page.getByText('已归档原图',{exact:true})).toBeVisible();await page.getByLabel('细节保护',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('无保护 · 不回贴原图像素',{exact:true}).click();return {account,headers};
}
async function submit(page:Page){
 await page.getByRole('button',{name:'获取报价并继续'}).click();const created=page.waitForResponse(r=>r.url().endsWith('/api/v1/tasks')&&r.request().method()==='POST'&&r.status()===201);await page.getByRole('dialog',{name:'确认本次生成'}).getByRole('button',{name:'确认并创建任务',exact:true}).click();const task=await(await created).json();
 await expect(page.getByRole('button',{name:'取消排队任务',exact:true})).toBeVisible();expect((await(await page.request.get('/api/v1/tasks/'+task.id)).json()).status).toBe('queued');return task;
}
async function balance(page:Page){return (await page.request.get('/api/v1/credits/balance')).json()}
async function ledger(page:Page,task:string){return (await(await page.request.get('/api/v1/credits/transactions')).json()).items.filter((x:any)=>x.task_id===task)}
async function capture(page:Page,name:string){await page.locator('.progress-card').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(root,'docs/evidence/browser-fault-'+name+'.png')})}

test.beforeAll(async({request,browser})=>{browserVersion=browser.version();expect(process.env.CONTROLLED_FAKE_BROWSER,'pause the owned local publisher/consumer before controlled tests').toBe('1');const html=await(await request.get('/')).text();expect(html).toContain('/assets/');expect(html).not.toContain('/@vite/client');expect(profile.PROVIDER_PROFILE).toBe('offline')});
test.afterAll(()=>writeFileSync(path.join(root,'docs/evidence/browser-controlled-faults.json'),JSON.stringify({started_at:suiteStartedAt,finished_at:new Date().toISOString(),base_commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),command:'CONTROLLED_FAKE_BROWSER=1 pnpm --dir apps/web exec playwright test -c playwright.faults.config.ts',environment:{node:process.version,platform:process.platform,browser:browserVersion,profile:'loopback/offline'},test_sha256:createHash('sha256').update(readFileSync(fileURLToPath(import.meta.url))).digest('hex'),production_assets:readdirSync(path.join(root,'apps/web/dist/assets')).filter(x=>x.endsWith('.js')).map(name=>({name,sha256:createHash('sha256').update(readFileSync(path.join(root,'apps/web/dist/assets',name))).digest('hex')})),expected_cases:6,passed:evidence.length===6,cases:evidence},null,2)));

test('排队取消和重复点击只释放一次积分',async({page})=>{
 const {headers}=await setup(page);const task=await submit(page);
 await page.getByRole('button',{name:'取消排队任务',exact:true}).dblclick();await expect(page.getByText('排队任务已取消，冻结积分已退回。',{exact:true})).toBeVisible();
 expect((await page.request.post('/api/v1/tasks/'+task.id+'/cancel',{headers})).status()).toBe(200);
 const b=await balance(page);expect([b.available,b.reserved,b.spent_total]).toEqual([200,0,0]);const rows=await ledger(page,task.id);expect(rows.filter((r:any)=>r.type==='freeze')).toHaveLength(1);expect(rows.filter((r:any)=>r.type==='refund')).toHaveLength(1);expect(admin(['inspect','--task',task.id]).attempts).toHaveLength(0);
 await expect(page.getByRole('button',{name:'获取报价并继续'})).toBeEnabled();
 await capture(page,'cancel');evidence.push({case:'queued_cancel',one_freeze:true,one_refund:true,provider_attempts:0,balance:b});
});

test('明确供应商失败释放积分，刷新不自动重试',async({page})=>{
 await setup(page);let posts=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/v1/tasks'))posts++});const task=await submit(page);expect(worker(task.id,'failed').result).toBe('stopped');
 await expect(page.getByText('任务未完成，冻结积分已退回。',{exact:true})).toBeVisible();expect(await page.getByRole('link',{name:'下载 PNG'}).count()).toBe(0);
 await page.reload();await expect(page.getByText('任务未完成，冻结积分已退回。',{exact:true})).toBeVisible();expect(posts).toBe(1);
 const b=await balance(page);expect([b.available,b.reserved,b.spent_total]).toEqual([200,0,0]);const rows=await ledger(page,task.id);expect(rows.filter((r:any)=>r.type==='refund')).toHaveLength(1);expect(rows.filter((r:any)=>r.type==='charge')).toHaveLength(0);
 await expect(page.getByRole('button',{name:'获取报价并继续'})).toBeEnabled();
 await capture(page,'failed');evidence.push({case:'explicit_failure',task_posts:posts,one_refund:true,no_charge:true,balance:b});
});

test('已创建意图的取消竞争返回409，未知保持冻结并接收运营结案',async({page})=>{
 await setup(page);const task=await submit(page);let release!:()=>void;const barrier=new Promise<void>(resolve=>release=resolve);const exact=new RegExp('/api/v1/tasks/'+task.id+'$');
 await page.route(exact,async route=>{await barrier;await route.continue()});
 try{
  expect(worker(task.id,'uncertain').result).toBe('stopped');
  const refused=page.waitForResponse(r=>r.url().endsWith('/'+task.id+'/cancel')&&r.status()===409);await page.getByRole('button',{name:'取消排队任务',exact:true}).click();const rejection=await(await refused).json();expect(rejection.error.request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f-]{27}$/);await expect(page.getByText('任务已开始，暂不支持取消（问题编号：'+rejection.error.request_id+'）',{exact:true})).toBeVisible();
 }finally{release();await page.unrouteAll({behavior:'wait'})}
 await expect(page.getByText('生成提交回执未确认，任务已暂停，当前不会自动出图。',{exact:true})).toBeVisible();
 const b=await balance(page);expect([b.available,b.reserved,b.spent_total]).toEqual([184,16,0]);expect(worker(task.id,'success').result).toBe('not_claimed');
 await page.reload();await expect(page.getByRole('button',{name:'等待对账后再生成'})).toBeDisabled();expect(await page.getByRole('button',{name:/重试/}).count()).toBe(0);await capture(page,'uncertain');
 admin(['reconcile','--task',task.id,'--action','fail','--reason','controlled offline fixture cannot deliver; release internal credits','--evidence-digest','a'.repeat(64),'--operation-id',crypto.randomUUID()]);
 await expect(page.getByText('本次交付已人工关闭，平台冻结积分已退回；供应商费用仍以账单为准。',{exact:true})).toBeVisible({timeout:15000});const after=await balance(page);expect([after.available,after.reserved,after.spent_total]).toEqual([200,0,0]);expect(admin(['inspect','--task',task.id]).attempts).toHaveLength(1);
 evidence.push({case:'unknown_cancel_race',cancel_status:409,unknown_balance:b,after_manual_close:after,no_resubmit:true,live_admin_update_without_reload:true});
});

test('晚到回执恢复查询和归档，原生成意图不重发',async({page})=>{
 await setup(page);const task=await submit(page);expect(worker(task.id,'late').result).toBe('stopped');await expect(page.getByRole('button',{name:'等待对账后再生成'})).toBeDisabled();
 const details=admin(['inspect','--task',task.id]);const attempt=details.attempts.find((x:any)=>x.step==='generate');const bytes=readFileSync(path.join(profile.FAKE_PROVIDER_DIR,'generate-'+attempt.id+'.json'));const record=JSON.parse(bytes.toString('utf8'));expect(record.offline).toBe(true);const digest=createHash('sha256').update(bytes).digest('hex');
 const receipt=admin(['accept-receipt','--task',task.id,'--attempt',attempt.id,'--provider-id',record.provider_id,'--response-digest',digest,'--reason','verified persisted offline late-result fixture','--evidence-digest',digest,'--operation-id',crypto.randomUUID()]);
 admin(['reconcile','--task',task.id,'--action','resume','--receipt',receipt.receipt_id,'--reason','resume existing supplier query only','--evidence-digest',digest,'--operation-id',crypto.randomUUID()]);expect(worker(task.id,'success').result).toBe('succeeded');
 await expect(page.getByRole('link',{name:'下载 PNG'})).toBeVisible({timeout:15000});const after=admin(['inspect','--task',task.id]);expect(after.attempts.filter((x:any)=>x.step==='generate')).toHaveLength(1);expect(after.attempts.filter((x:any)=>x.step==='detect')).toHaveLength(1);const b=await balance(page);expect([b.available,b.reserved,b.spent_total]).toEqual([184,0,16]);
 const rows=await ledger(page,task.id);expect(rows.filter((r:any)=>r.type==='charge')).toHaveLength(1);await capture(page,'late');evidence.push({case:'late_receipt',generate_attempts:1,detect_attempts:1,one_charge:true,balance:b,live_admin_update_without_reload:true});
});

test('提交响应丢失后再次确认沿用同一幂等键',async({page})=>{
 await setup(page);let first:any;const keys:string[]=[];let intercepted=0;let lossInjected=false;
 const requestFailures:string[]=[];const pageErrors:string[]=[];
 page.on('requestfailed',request=>{if(new URL(request.url()).pathname==='/api/v1/tasks')requestFailures.push(request.failure()?.errorText||'unknown')});
 page.on('pageerror',error=>pageErrors.push(error.name+': '+error.message));
 await page.route('**/api/v1/tasks',async route=>{
  if(route.request().method()!=='POST'){await route.continue();return}keys.push(route.request().headers()['idempotency-key']);intercepted++;
  if(intercepted===1){const response=await route.fetch();expect(response.status()).toBe(201);first=await response.json();await route.abort('failed');lossInjected=true}else await route.continue();
 });
 await page.getByRole('button',{name:'获取报价并继续'}).click();const dialog=page.getByRole('dialog',{name:'确认本次生成'});const confirm=dialog.getByRole('button',{name:'确认并创建任务',exact:true});await expect(confirm).toHaveAttribute('aria-label','确认并创建任务');await confirm.click();
 // Interception starts before the real POST commits. Wait for the injected loss,
 // not just interception, before measuring the UI's recovery from that loss.
 await expect.poll(()=>lossInjected,{timeout:30000}).toBe(true);expect(first.id).toBeTruthy();expect(intercepted).toBe(1);
 try{await expect(confirm).not.toHaveClass(/ant-btn-loading/)}catch(error){
  console.log(JSON.stringify({lost_response_diagnostic:{intercepted,lossInjected,has_committed_receipt:!!first?.id,requestFailures,pageErrors,login_visible:await page.getByRole('heading',{name:'登录你的工作台'}).isVisible(),dialogs:await page.getByRole('dialog').evaluateAll(nodes=>nodes.map(node=>({title:node.getAttribute('aria-labelledby'),buttons:Array.from(node.querySelectorAll('button')).map(button=>({text:button.textContent,disabled:button.disabled,classes:button.className,label:button.getAttribute('aria-label')}))})))}}));
  await page.screenshot({path:path.join(root,'docs/evidence/browser-fault-lost-response-diagnostic.png'),fullPage:true});throw error;
 }
 const replay=page.waitForResponse(r=>r.url().endsWith('/api/v1/tasks')&&r.request().method()==='POST'&&r.status()===200);await confirm.click();const recovered=await(await replay).json();expect(recovered.id).toBe(first.id);expect(keys).toHaveLength(2);expect(keys[0]).toBe(keys[1]);
 expect(worker(recovered.id,'success').result).toBe('succeeded');await expect(page.getByRole('link',{name:'下载 PNG'})).toBeVisible();const rows=await ledger(page,recovered.id);expect(rows.filter((r:any)=>r.type==='freeze')).toHaveLength(1);expect(rows.filter((r:any)=>r.type==='charge')).toHaveLength(1);expect((await(await page.request.get('/api/v1/tasks')).json()).items).toHaveLength(1);
 evidence.push({case:'lost_submit_response',http_replay_status:200,same_idempotency_key:true,unique_tasks:1,one_freeze:true,one_charge:true});
});


test('检测结果未知经运营审批交付，保留未知状态且只结算一次',async({page})=>{
 await setup(page);const task=await submit(page);expect(worker(task.id,'detect_uncertain').result).toBe('stopped');
 await expect(page.getByRole('button',{name:'等待对账后再生成'})).toBeDisabled();expect(worker(task.id,'success').result).toBe('not_claimed');
 function review(args:string[]){return JSON.parse(execFileSync(py,['scripts/detection_delivery.py',...args],{cwd:root,env:{...process.env,ADMIN_DATABASE_URL:profile.ADMIN_DATABASE_URL},encoding:'utf8'}))}
 const before=review(['inspect','--task',task.id]).context;expect(before.task.status).toBe('uncertain');expect(before.detect_attempt.status).toBe('uncertain');expect(before.final.ready).toBe(true);
 const reviewPath=path.join(root,'work','private-review-'+task.id+'.png');
 const exported=JSON.parse(execFileSync(py,['scripts/detection_delivery.py','export','--task',task.id,'--output',reviewPath],{cwd:root,env:{...process.env,...profile},encoding:'utf8'}));
 try{expect(exported.result).toBe('PRIVATE_REVIEW_EXPORTED');expect(exported.delivery_approved).toBe(false);expect(createHash('sha256').update(readFileSync(reviewPath)).digest('hex')).toBe(before.final.sha256)}finally{unlinkSync(reviewPath)}
 const digest=before.final.sha256;const op=crypto.randomUUID();const approved=review(['approve','--task',task.id,'--attempt',before.detect_attempt.id,'--final-sha256',digest,'--operation-id',op,'--reason','reviewed controlled offline final image; preserve unresolved detection','--evidence-digest',digest]);expect(approved.result).toBe('LOCAL_DELIVERY_APPROVED');
 expect(worker(task.id,'success').result).toBe('succeeded');await expect(page.getByRole('link',{name:'下载 PNG'})).toBeVisible({timeout:15000});
 const download=await page.request.get('/api/v1/tasks/'+task.id+'/download');expect(download.status()).toBe(200);expect(createHash('sha256').update(await download.body()).digest('hex')).toBe(digest);
 await expect(page.getByText('检测结果未取得，已按运营审核交付；请人工核对成品。',{exact:true})).toBeVisible();
 const report=await(await page.request.get('/api/v1/tasks/'+task.id+'/compliance')).json();expect(report.detection_degraded).toBe(true);expect(report.auto_overall).toBe('manual_review');
 await page.getByRole('button',{name:'查看检查报告'}).click();await expect(page.getByText('视觉检测未取得可用结果',{exact:true})).toBeVisible();await expect(page.getByText(/此交付不代表检测成功/)).toBeVisible();
 const after=review(['inspect','--task',task.id]).context;expect(after.detect_attempt.status).toBe('uncertain');expect(after.detect_attempt.provider_id).toBeNull();expect(after.supplier_cost_verified).toBe(false);
 const b=await balance(page);expect([b.available,b.reserved,b.spent_total]).toEqual([184,0,16]);const rows=await ledger(page,task.id);expect(rows.filter((r:any)=>r.type==='charge')).toHaveLength(1);expect(rows.filter((r:any)=>r.type==='refund')).toHaveLength(0);
 expect(readdirSync(profile.FAKE_PROVIDER_DIR).filter(name=>name==='detect-'+before.detect_attempt.id+'.intent')).toHaveLength(1);
 evidence.push({case:'reviewed_detection_delivery',final_hash_verified:true,unresolved_detection_preserved:true,warning_visible:true,one_charge:true,paid_calls:0,balance:b});
});
