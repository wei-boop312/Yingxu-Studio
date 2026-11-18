import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

for(const width of [1440,390]){
 test(`trial guidance and private feedback at ${width}px`,async({page})=>{
  test.setTimeout(30000);
  await page.setViewportSize({width,height:1000});
  const products:any[]=[];const writes:string[]=[];
  const task={id:'33333333-3333-4333-8333-333333333333',product_id:'44444444-4444-4444-8444-444444444444',status:'failed',phase:'submitting',revision:1,credits:{status:'refunded',amount:10},created_at:'2026-11-15T00:00:00Z',error_code:null,result:null,compliance:null};
  await page.route('**/api/v1/**',async route=>{
   const request=route.request();const url=new URL(request.url());
   if(request.method()!=='GET')writes.push(url.pathname);
   if(url.pathname==='/api/v1/provider-status')return route.fulfill({json:{profile:'offline'}});
   if(url.pathname==='/api/v1/auth/me')return route.fulfill({json:{id:'11111111-1111-4111-8111-111111111111',email:'synthetic@example.invalid',display_name:'试用用户'}});
   if(url.pathname==='/api/v1/credits/balance')return route.fulfill({json:{available:100,reserved:0,funded_total:100,spent_total:0}});
   if(url.pathname==='/api/v1/products'&&request.method()==='POST'){
    const product={...request.postDataJSON(),id:'22222222-2222-4222-8222-222222222222',revision:1,archived_at:null};products.push(product);return route.fulfill({status:201,json:product});
   }
   if(url.pathname==='/api/v1/tasks')return route.fulfill({json:{items:[task],next_cursor:null}});
   if(url.pathname==='/api/v1/products')return route.fulfill({json:{items:products,next_cursor:null}});
   return route.fulfill({json:{items:[],next_cursor:null}});
  });
  await page.goto('/');
  await expect(page.getByRole('status',{name:'下一步操作'})).toContainText('先新建一个产品');
  await page.getByRole('button',{name:'试用指引',exact:true}).click();
  const guide=page.getByRole('dialog',{name:'初版试用指引'});
  await expect(guide).toContainText('不会调用真实付费模型');
  await expect(guide).toContainText('色块和错位');
  await guide.getByRole('button',{name:'开始试用',exact:true}).click();
  await page.getByRole('button',{name:'记录反馈',exact:true}).click();
  const feedback=page.getByRole('dialog',{name:'记录试用反馈'});
  const downloadButton=feedback.getByRole('button',{name:'下载反馈文件',exact:true});
  await expect(downloadButton).toBeDisabled();
  await feedback.getByRole('textbox',{name:'操作步骤和实际结果',exact:true}).fill('   ');
  await expect(downloadButton).toBeDisabled();
  await feedback.getByRole('textbox',{name:'操作步骤和实际结果',exact:true}).fill('上传后想知道下一步如何操作');
  await feedback.getByRole('textbox',{name:'期望结果',exact:true}).fill('给出下一步提示');
  const pending=page.waitForEvent('download');await downloadButton.click();const download=await pending;
  const file=await download.path();expect(file).toBeTruthy();
  const exported=JSON.parse(readFileSync(file!,'utf8'));
  expect(Object.keys(exported).sort()).toEqual(['schema_version','created_at','mode','area','description','expected'].sort());
  expect(exported.mode).toBe('offline_trial');expect(exported.description).toBe('上传后想知道下一步如何操作');
  expect(writes).toEqual([]);
  await feedback.getByRole('combobox',{name:'关联当前任务',exact:true}).click();
  await page.getByText('仅附带任务编号、状态和阶段',{exact:true}).click();
  const linkedPending=page.waitForEvent('download');await downloadButton.click();
  const linkedFile=await(await linkedPending).path();
  const linked=JSON.parse(readFileSync(linkedFile!,'utf8'));
  expect(linked.task).toEqual({id:task.id,status:task.status,phase:task.phase});
  expect(Object.keys(linked.task).sort()).toEqual(['id','phase','status']);
  expect(writes).toEqual([]);
  await feedback.getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('button',{name:'记录反馈',exact:true}).click();
  await expect(feedback.getByRole('textbox',{name:'操作步骤和实际结果',exact:true})).toHaveValue('');
  await page.keyboard.press('Escape');await expect(feedback).not.toBeVisible();
  await page.getByRole('button',{name:'新建产品',exact:true}).click();
  const create=page.getByRole('dialog',{name:'新建产品',exact:true});
  await create.getByLabel('产品名称',{exact:true}).fill('试用商品');await create.getByLabel('类目',{exact:true}).fill('electronics');
  await create.getByRole('button',{name:'创建产品',exact:true}).click();
  await expect(create).not.toBeVisible();
  await expect(page.getByRole('status',{name:'下一步操作'})).toContainText('上传商品原图');
  expect(writes).toEqual(['/api/v1/products']);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`../../work/trial-workspace-${width}.png`,fullPage:true});
 });
}

test('live mode is explicit before any paid task and feedback remains local',async({page})=>{
 const posts:string[]=[];
 await page.route('**/api/v1/**',async route=>{
  const request=route.request();const path=new URL(request.url()).pathname;
  if(request.method()!=='GET')posts.push(path);
  if(path==='/api/v1/auth/me')return route.fulfill({json:{id:'11111111-1111-4111-8111-111111111111',email:'synthetic@example.invalid',display_name:'试用用户'}});
  if(path==='/api/v1/provider-status')return route.fulfill({json:{profile:'bailian_live'}});
  if(path==='/api/v1/credits/balance')return route.fulfill({json:{available:100,reserved:0,funded_total:100,spent_total:0}});
  return route.fulfill({json:{items:[],next_cursor:null}});
 });
 await page.goto('/');await expect(page.getByText('百炼 · 真实供应商',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'获取报价并继续'})).toBeDisabled();
 await page.getByRole('button',{name:'试用指引',exact:true}).click();
 const guide=page.getByRole('dialog',{name:'初版试用指引'});
 await expect(guide).toContainText('确认报价并提交后会产生实际费用');
 await expect(guide).not.toContainText('不会调用真实付费模型');
 await guide.getByRole('button',{name:'开始试用'}).click();
 expect(posts).toEqual([]);
});
