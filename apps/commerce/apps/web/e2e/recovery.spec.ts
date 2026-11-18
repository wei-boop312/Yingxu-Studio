import {test,expect,type Page} from '@playwright/test';
import {execFileSync} from 'node:child_process';import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';import {fileURLToPath} from 'node:url';import path from 'node:path';
const root=fileURLToPath(new URL('../../../',import.meta.url));
function account(){const py=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');const result=JSON.parse(execFileSync(py,[path.join(root,'scripts/create_e2e_account.py')],{encoding:'utf8',cwd:root}));return JSON.parse(readFileSync(result.account_file,'utf8'))}
async function login(page:Page,a:any){await page.goto('/');await expect(page.getByLabel('邮箱',{exact:true})).toBeVisible();await page.getByLabel('邮箱',{exact:true}).fill(a.email);await page.getByLabel('密码',{exact:true}).fill(a.password);await page.getByRole('button',{name:'进入工作台'}).click();await expect(page.getByRole('heading',{name:'视觉工作台',exact:true})).toBeVisible()}
async function seed(page:Page){const csrf=(await page.context().cookies()).find(c=>c.name==='visual_csrf')!.value;const headers={'Origin':'http://127.0.0.1:5173','X-CSRF-Token':csrf};const product=await page.request.post('/api/v1/products',{headers,data:{name:'断线恢复验收专属产品',category:'generic'}});expect(product.status()).toBe(201);const p=await product.json();const data=readFileSync(new URL('./fixtures/product.png',import.meta.url));const upload=await page.request.post('/api/v1/assets/upload-intents',{headers,data:{product_id:p.id,filename:'fixture.png',mime:'image/png',size:data.length}});const u=await upload.json();expect((await page.request.put(u.upload_url,{data,headers:u.headers})).status()).toBe(200);const complete=await page.request.post('/api/v1/assets/upload-intents/'+u.intent_id+'/complete',{headers});expect(complete.status()).toBe(200);await page.reload();await expect(page.getByText('已归档原图',{exact:true})).toBeVisible();await page.getByLabel('细节保护',{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('无保护 · 不回贴原图像素',{exact:true}).click();return p}

test('断线恢复、规则报告版本、Cookie刷新和账号隔离',async({page,context})=>{
 if(process.env.REQUIRE_PRODUCTION_BUILD==='1'){const html=await (await page.request.get('/')).text();expect(html).toContain('/assets/');expect(html).not.toContain('/@vite/client')}
 const a=account();await login(page,a);await seed(page);
 let posts=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/v1/tasks'))posts++});
 await page.getByRole('button',{name:'获取报价并继续'}).click();
 const created=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/api/v1/tasks')&&r.status()===201);
 await page.getByRole('dialog',{name:'确认本次生成'}).getByRole('button',{name:'确认并创建任务'}).click();const response=await created;const task=await response.json();await context.setOffline(true);
 await expect(page.getByText('轮询更新',{exact:true})).toBeVisible({timeout:12000});
 await context.setOffline(false);await expect(page.getByRole('link',{name:'下载 PNG'})).toBeVisible({timeout:15000});expect(posts).toBe(1);
 await context.clearCookies({name:'visual_access'});
 const refreshed=page.waitForResponse(r=>r.url().endsWith('/api/v1/auth/refresh')&&r.status()===200);
 await page.reload();await refreshed;await expect(page.getByRole('heading',{name:'视觉工作台',exact:true})).toBeVisible();await expect(page.getByRole('link',{name:'下载 PNG'})).toBeVisible();
 const reportData=await (await page.request.get('/api/v1/tasks/'+task.id+'/compliance')).json();
 const profile=JSON.parse(readFileSync(path.join(root,'.local/runtime.json'),'utf8'));const proof=path.join(root,'work','browser-review-evidence.json');mkdirSync(path.dirname(proof),{recursive:true});writeFileSync(proof,JSON.stringify(['local:browser-review-fixture']));const py=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
 execFileSync(py,['scripts/admin.py','review','--task',task.id,'--check',reportData.id,'--reviewer',a.user_id,'--decision','approved','--reason','浏览器验收：人工确认产品保护区域','--evidence-json',proof,'--operation-id',crypto.randomUUID()],{cwd:root,env:{...process.env,ADMIN_DATABASE_URL:profile.ADMIN_DATABASE_URL},encoding:'utf8'});
 await page.getByRole('button',{name:'查看检查报告'}).click();const reviewDialog=page.getByRole('dialog',{name:'最终成品检查报告'});await expect(reviewDialog.getByText('人工复核：通过',{exact:true})).toBeVisible();await expect(reviewDialog.getByText('需要人工复核',{exact:true})).toBeVisible();await reviewDialog.getByRole('button',{name:'关闭',exact:true}).click();
 const ruleVersion='browser-'+crypto.randomUUID();const ruleFile=path.join(root,'work','browser-rule-document.json');
 writeFileSync(ruleFile,JSON.stringify({platform:'amazon',marketplace:'US',category:'generic',image_type:'MAIN',version:ruleVersion,evidence_status:'unverified',config:{}}));
 const rulesEnv={...process.env,...profile,PYTHONPATH:path.join(root,'src')};
 const published=JSON.parse(execFileSync(py,['scripts/rules.py','publish','--document',ruleFile,'--operation-id',crypto.randomUUID(),'--reason','browser synthetic unverified rule fixture'],{cwd:root,env:rulesEnv,encoding:'utf8'}));
 const beforeBalance=await (await page.request.get('/api/v1/credits/balance')).json();
 const recomputed=JSON.parse(execFileSync(py,['scripts/rules.py','recheck','--task',task.id,'--source-check',reportData.id,'--rule-id',published.rule_id,'--operation-id',crypto.randomUUID(),'--reason','browser rule-only recheck; no supplier call'],{cwd:root,env:rulesEnv,encoding:'utf8'}));
 expect(recomputed.provider_submissions).toBe(0);expect(recomputed.credits_charged).toBe(0);
 expect(await (await page.request.get('/api/v1/credits/balance')).json()).toEqual(beforeBalance);
 await page.getByRole('button',{name:'查看检查报告'}).click();
 await expect(reviewDialog.getByText('人工复核：通过',{exact:true})).toBeVisible();
 await reviewDialog.getByLabel('报告版本',{exact:true}).click();await page.getByText(new RegExp('重算报告.*'+recomputed.check_id.slice(0,8))).last().click();
 await expect(reviewDialog.getByText('规则版本：'+ruleVersion,{exact:true})).toBeVisible();await expect(reviewDialog.getByText('人工复核：通过',{exact:true})).toHaveCount(0);
 await expect(reviewDialog.getByRole('link',{name:'下载当前报告'})).toHaveAttribute('href',new RegExp(recomputed.check_id));
 await reviewDialog.getByText('最终成品检查报告',{exact:true}).click();
 await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0);
 await page.screenshot({path:path.join(root,'docs/evidence/browser-report-versions.png')});
 await reviewDialog.getByLabel('报告版本',{exact:true}).click();await page.getByText(new RegExp('原始报告.*'+reportData.id.slice(0,8))).last().click();
 await expect(reviewDialog.getByText('人工复核：通过',{exact:true})).toBeVisible();await reviewDialog.getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:'退出登录',exact:true}).click();await expect(page.getByRole('heading',{name:'登录你的工作台'})).toBeVisible();
 const b=account();await login(page,b);await expect(page.getByText('先放入一张真实产品图',{exact:true})).toBeVisible();await expect(page.getByTestId('canvas-source')).toHaveCount(0);
 expect((await page.request.get('/api/v1/tasks/'+task.id)).status()).toBe(404);
 await page.getByRole('button',{name:/任务记录/}).click();await expect(page.getByText('暂无任务，先去工作台创建一张图片',{exact:true})).toBeVisible();
});
test('不同产品草稿隔离，刷新恢复当前产品且编辑不创建模型任务',async({page})=>{
 const a=account();await login(page,a);const first=await seed(page);const prompt=page.getByLabel('画面描述');await prompt.fill('产品 A 专属草稿');
 const otherOrigins:string[]=[];page.on('request',r=>{const host=new URL(r.url()).hostname;if(!['127.0.0.1','localhost'].includes(host))otherOrigins.push(host)});
 await page.getByRole('button',{name:/新建产品/}).click();const dialog=page.getByRole('dialog',{name:'新建产品'});await dialog.getByLabel('产品名称').fill('产品 B · 隔离草稿');await dialog.getByRole('button',{name:'创建产品',exact:true}).click();await expect(dialog).not.toBeVisible();await expect(prompt).not.toHaveValue('产品 A 专属草稿');await prompt.fill('产品 B 专属草稿');
 await page.getByLabel('当前产品',{exact:true}).click();await page.getByText(first.name,{exact:true}).last().click();await expect(prompt).toHaveValue('产品 A 专属草稿');
 await page.reload();await expect(prompt).toHaveValue('产品 A 专属草稿');await expect(page.getByText('已归档原图',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'产品资料',exact:true}).click();const row=page.getByRole('row').filter({has:page.getByRole('button',{name:first.name,exact:true})});await row.getByRole('button',{name:'编辑产品',exact:true}).click();const edit=page.getByRole('dialog',{name:'编辑产品',exact:true});await expect(edit.getByLabel('产品名称')).toHaveValue(first.name);await edit.getByLabel('产品名称').fill('产品 A · 更新资料');await edit.getByLabel('类目',{exact:true}).fill('kitchen');const patched=page.waitForResponse(r=>r.url().endsWith('/api/v1/products/'+first.id)&&r.request().method()==='PATCH'&&r.status()===200);await edit.getByRole('button',{name:'保存产品',exact:true}).click();const response=await patched;expect(response.request().headers()['if-match']).toBe(String(first.revision));await expect(edit).not.toBeVisible();const changed=page.getByRole('row').filter({has:page.getByRole('button',{name:'产品 A · 更新资料',exact:true})});await expect(changed.getByText('kitchen',{exact:true})).toBeVisible();await changed.getByRole('button',{name:'进入工作台',exact:true}).click();await expect(prompt).toHaveValue('产品 A 专属草稿');await page.reload();await expect(prompt).toHaveValue('产品 A 专属草稿');const detail=await(await page.request.get('/api/v1/products/'+first.id)).json();expect(detail.name).toBe('产品 A · 更新资料');expect(detail.category).toBe('kitchen');expect(detail.revision).toBe(first.revision+1);
 const tasks=await page.request.get('/api/v1/tasks');expect((await tasks.json()).items).toHaveLength(0);expect(otherOrigins).toEqual([]);
});
