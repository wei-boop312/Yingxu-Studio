import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {createHash} from 'node:crypto';

const root=fileURLToPath(new URL('../../../',import.meta.url));
const py=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
function hold(){let release!:()=>void;const promise=new Promise<void>(resolve=>release=resolve);return {promise,release}}
test('迟到的报价不能跨产品或配置弹出，只有当前确认的输入能冻结一次积分',async({page,browser})=>{
 expect(process.env.CONTROLLED_FAKE_BROWSER).toBe('1');
 const provision=JSON.parse(execFileSync(py,['scripts/create_e2e_account.py'],{cwd:root,encoding:'utf8'}));
 const account=JSON.parse(readFileSync(provision.account_file,'utf8'));
 await page.goto('/');await page.getByLabel('邮箱',{exact:true}).fill(account.email);await page.getByLabel('密码',{exact:true}).fill(account.password);await page.getByRole('button',{name:'进入工作台'}).click();
 await expect(page.getByRole('heading',{name:'视觉工作台',exact:true})).toBeVisible();
 const headers={Origin:'http://127.0.0.1:5173','X-CSRF-Token':(await page.context().cookies()).find(c=>c.name==='visual_csrf')!.value};
 const data=readFileSync(new URL('../e2e/fixtures/product.png',import.meta.url));const products=[];
 for(const name of ['报价产品 A','报价产品 B']){
  const created=await page.request.post('/api/v1/products',{headers,data:{name,category:'generic'}});expect(created.status()).toBe(201);const product=await created.json();products.push(product);
  const response=await page.request.post('/api/v1/assets/upload-intents',{headers,data:{product_id:product.id,filename:'fixture.png',mime:'image/png',size:data.length}});expect(response.status()).toBe(201);
  const intent=await response.json();expect((await page.request.put(intent.upload_url,{data,headers:intent.headers})).status()).toBe(200);
  expect((await page.request.post('/api/v1/assets/upload-intents/'+intent.intent_id+'/complete',{headers})).status()).toBe(200);
 }
 await page.reload();await page.getByLabel('当前产品',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('报价产品 A',{exact:true}).click();await expect(page.getByText('已归档原图',{exact:true})).toBeVisible();
 await page.getByLabel('细节保护',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('无保护 · 不回贴原图像素',{exact:true}).click();
 const productGate=hold(),configGate=hold();let quoted=0,productFetched=false,configFetched=false,taskPosts=0;let confirmed:any;
 page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/api/v1/tasks'))taskPosts++});
 await page.route('**/api/v1/quotes',async route=>{
  if(route.request().method()!=='POST'){await route.continue();return}
  quoted++;const response=await route.fetch();expect(response.status()).toBe(201);
  if(quoted===1){productFetched=true;await productGate.promise}else if(quoted===2){configFetched=true;await configGate.promise}else confirmed=route.request().postDataJSON();
  try{await route.fulfill({response})}catch(error){if(!route.request().failure())throw error}
 });
 const button=page.getByRole('button',{name:'获取报价并继续'});const dialog=page.getByRole('dialog',{name:'确认本次生成'});
 await button.click();await expect.poll(()=>productFetched).toBe(true);
 await page.getByLabel('当前产品',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('报价产品 B',{exact:true}).click();
 productGate.release();await expect(dialog).not.toBeVisible();expect(taskPosts).toBe(0);
 await expect(page.getByText('已归档原图',{exact:true})).toBeVisible();await page.getByLabel('细节保护',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('无保护 · 不回贴原图像素',{exact:true}).click();await expect(button).toBeEnabled();await button.click();await expect.poll(()=>configFetched).toBe(true);
 await page.getByLabel('画面描述').fill('这是修改后的当前报价配置');configGate.release();await expect(button).toBeEnabled();await expect(dialog).not.toBeVisible();expect(taskPosts).toBe(0);
 await button.click();await expect(dialog).toBeVisible();await expect(dialog).toContainText('报价产品 B');
 const created=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/v1/tasks')&&response.status()===201);
 await dialog.getByRole('button',{name:'确认并创建任务',exact:true}).click();const response=await created;const task=await response.json();const input=response.request().postDataJSON();
 expect(input.product_id).toBe(products[1].id);expect(input.generation.prompt).toBe('这是修改后的当前报价配置');
 const {quote_id,...business}=input;expect(quote_id).toBeTruthy();expect(business).toEqual(confirmed);expect([quoted,taskPosts]).toEqual([3,1]);
 expect((await page.request.post('/api/v1/tasks/'+task.id+'/cancel',{headers})).status()).toBe(200);
 const transactions=(await(await page.request.get('/api/v1/credits/transactions')).json()).items.filter((row:any)=>row.task_id===task.id);
 expect(transactions.filter((row:any)=>row.type==='freeze')).toHaveLength(1);expect(transactions.filter((row:any)=>row.type==='refund')).toHaveLength(1);expect(transactions.filter((row:any)=>row.type==='charge')).toHaveLength(0);
 const balance=await(await page.request.get('/api/v1/credits/balance')).json();expect([balance.available,balance.reserved,balance.spent_total]).toEqual([200,0,0]);
 mkdirSync(path.join(root,'work/ci-public'),{recursive:true});writeFileSync(path.join(root,'work/ci-public/frontend-quotes.json'),JSON.stringify({
  result:'PASS',commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),observed_at:new Date().toISOString(),paid_calls:0,browser:browser.version(),
  scope:'production React + native PG/S3; two actual quote responses delayed at browser boundary; queued task explicitly cancelled without supplier calls',
  stale_product_quote_discarded:true,stale_config_quote_discarded:true,confirmed_input_matches_current_quote:true,quotes:quoted,task_posts:taskPosts,one_freeze_one_refund:true,
  test_sha256:createHash('sha256').update(readFileSync(fileURLToPath(import.meta.url))).digest('hex')
 },null,2)+'\n');
});
