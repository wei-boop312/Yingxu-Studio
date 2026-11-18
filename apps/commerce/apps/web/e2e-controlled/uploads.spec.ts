import {test, expect, type Page, type Route} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {readFileSync, writeFileSync, mkdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {createHash} from 'node:crypto';

const root=fileURLToPath(new URL('../../../',import.meta.url));
const py=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const fixture=fileURLToPath(new URL('../e2e/fixtures/product.png',import.meta.url));
const cases:Array<Record<string,unknown>>=[];
const started=new Date().toISOString();
let browserVersion='not_observed';
function hold(){let release!:()=>void;const promise=new Promise<void>(resolve=>release=resolve);return {promise,release}}
async function fulfill(route:Route,response:Awaited<ReturnType<Route['fetch']>>){
 try{await route.fulfill({response})}catch(error){if(!route.request().failure())throw error}
}
async function setup(page:Page){
 const provision=JSON.parse(execFileSync(py,['scripts/create_e2e_account.py'],{cwd:root,encoding:'utf8'}));
 const account=JSON.parse(readFileSync(provision.account_file,'utf8'));
 await page.goto('/');await page.getByLabel('邮箱',{exact:true}).fill(account.email);await page.getByLabel('密码',{exact:true}).fill(account.password);
 await page.getByRole('button',{name:'进入工作台'}).click();await expect(page.getByRole('heading',{name:'视觉工作台',exact:true})).toBeVisible();
 const headers={Origin:'http://127.0.0.1:5173','X-CSRF-Token':(await page.context().cookies()).find(c=>c.name==='visual_csrf')!.value};
 const products=[];
 for(const name of ['上传隔离 A','上传隔离 B']){
  const response=await page.request.post('/api/v1/products',{headers,data:{name,category:'generic'}});expect(response.status()).toBe(201);products.push(await response.json());
 }
 await page.reload();await choose(page,'上传隔离 A');return {headers,products};
}
async function choose(page:Page,name:string){await page.getByLabel('当前产品',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(name,{exact:true}).click();await expect(page.getByText('先放入一张真实产品图',{exact:true})).toBeVisible()}
async function unchanged(page:Page){
 const balance=await(await page.request.get('/api/v1/credits/balance')).json();expect([balance.available,balance.reserved,balance.spent_total]).toEqual([200,0,0]);
 const tasks=await(await page.request.get('/api/v1/tasks')).json();expect(tasks.items).toHaveLength(0);
}
test.beforeAll(async({browser})=>{expect(process.env.CONTROLLED_FAKE_BROWSER).toBe('1');browserVersion=browser.version()});
test.afterAll(()=>{
 mkdirSync(path.join(root,'work/ci-public'),{recursive:true});
 writeFileSync(path.join(root,'work/ci-public/frontend-uploads.json'),JSON.stringify({
  result:cases.length===2?'PASS':'FAIL',commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),
  started_at:started,finished_at:new Date().toISOString(),paid_calls:0,browser:browserVersion,
  scope:'production React app and native PG/S3; browser request boundaries hold and forward real signed PUT/complete responses; one complete 503 injected',
  expected_cases:2,cases,test_sha256:createHash('sha256').update(readFileSync(fileURLToPath(import.meta.url))).digest('hex')
 },null,2)+'\n');
});

test('上传显示传输与校验阶段，暂时失败只重试原校验而不重传',async({page})=>{
 await setup(page);let puts=0,intents=0,completions=0;const put=hold(),completion=hold();
 page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/api/v1/assets/upload-intents'))intents++});
 await page.route('**/temp/**',async route=>{if(route.request().method()!=='PUT'){await route.continue();return}puts++;await put.promise;await route.continue()});
 await page.route('**/api/v1/assets/upload-intents/*/complete',async route=>{
  completions++;if(completions===1){await completion.promise;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'validation_temporarily_unavailable'}})})}else await route.continue();
 });
 await page.locator('input[type=file]').first().setInputFiles(fixture);
 await expect(page.getByRole('status',{name:'原图上传状态'})).toContainText('正在上传原图');await expect.poll(()=>puts).toBe(1);put.release();
 await expect.poll(()=>completions).toBe(1);await expect(page.getByRole('status',{name:'原图上传状态'})).toContainText('上传完成，正在校验图片');
 completion.release();await expect(page.getByRole('button',{name:'重新校验',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'重新校验',exact:true}).click();await expect(page.getByText('已归档原图',{exact:true})).toBeVisible();
 expect([intents,puts,completions]).toEqual([1,1,2]);await expect(page.locator('.upload-progress')).toHaveCount(0);await unchanged(page);
 cases.push({case:'upload_progress_and_same_intent_retry',transferring_visible:true,validating_visible:true,intents,puts,completions,model_tasks:0,credits_unchanged:true});
});

test('切换产品取消旧上传流程，延迟的校验和意图响应不覆盖当前原图',async({page})=>{
 const {products}=await setup(page);const completed=hold(),intentHeld=hold();let completionFetched=false,intentFetched=false,puts=0,intents=0,completions=0;
 page.on('request',request=>{if(request.method()==='PUT'&&new URL(request.url()).pathname.includes('/temp/'))puts++});
 await page.route('**/api/v1/assets/upload-intents/*/complete',async route=>{completions++;const response=await route.fetch();expect(response.status()).toBe(200);completionFetched=true;await completed.promise;await fulfill(route,response)});
 await page.locator('input[type=file]').first().setInputFiles(fixture);await expect.poll(()=>completionFetched).toBe(true);
 await expect(page.getByRole('status',{name:'原图上传状态'})).toContainText('正在校验图片');await choose(page,'上传隔离 B');completed.release();
 await expect(page.locator('.upload-progress')).toHaveCount(0);await expect(page.getByText('已归档原图',{exact:true})).toHaveCount(0);
 const a=await(await page.request.get('/api/v1/assets?product_id='+products[0].id)).json();const b=await(await page.request.get('/api/v1/assets?product_id='+products[1].id)).json();
 expect(a.items.filter((row:any)=>row.status==='ready')).toHaveLength(1);expect(b.items).toHaveLength(0);
 await page.unroute('**/api/v1/assets/upload-intents/*/complete');
 await page.route('**/api/v1/assets/upload-intents',async route=>{if(route.request().method()!=='POST'){await route.continue();return}intents++;const response=await route.fetch();expect(response.status()).toBe(201);intentFetched=true;await intentHeld.promise;await fulfill(route,response)});
 await page.locator('input[type=file]').first().setInputFiles(fixture);await expect.poll(()=>intentFetched).toBe(true);
 await page.getByLabel('当前产品',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('上传隔离 A',{exact:true}).click();
 intentHeld.release();await expect(page.getByText('已归档原图',{exact:true})).toBeVisible();await expect(page.locator('.upload-progress')).toHaveCount(0);
 expect([puts,intents,completions]).toEqual([1,1,1]);await expect(page.getByRole('button',{name:'重新校验',exact:true})).toHaveCount(0);await unchanged(page);
 cases.push({case:'upload_scope_change_aborts_stale_work',late_completed_asset_remains_in_original_product:true,stale_intent_not_put:true,puts,intents,completions,model_tasks:0,credits_unchanged:true});
});
