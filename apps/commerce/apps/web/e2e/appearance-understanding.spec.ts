import {test,expect} from '@playwright/test';

test('AI appearance review sends confirmed text to redraw without any pixel regions',async({page})=>{
 const product='22222222-2222-4222-8222-222222222222',source='33333333-3333-4333-8333-333333333333',identity='44444444-4444-4444-8444-444444444444';
 let posts=0;let confirmed:any;let redraw:any;let profile:any;
 const summary={description:'黑色圆角盒体，正面有可见标识。',colors:['黑色'],materials:[],uncertainties:['侧面接口被遮挡']};
 const record={id:identity,status:'succeeded',source_asset_version_id:source,result:summary,confirmed_profile_id:null};
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path.endsWith('/download'))return route.fulfill({status:404});
  if(path==='/api/v1/auth/me')return route.fulfill({json:{id:'11111111-1111-4111-8111-111111111111',email:'synthetic@example.invalid',display_name:'试用用户'}});
  if(path==='/api/v1/provider-status')return route.fulfill({json:{profile:'bailian_live'}});
  if(path==='/api/v1/products')return route.fulfill({json:{items:[{id:product,name:'测试盒体',category:'generic',revision:1}],next_cursor:null}});
  if(path==='/api/v1/assets')return route.fulfill({json:{items:[{id:'55555555-5555-4555-8555-555555555555',version_id:source,status:'ready',width:512,height:512,sha256:'a'.repeat(64)}],next_cursor:null}});
  if(path.endsWith('/appearances'))return route.fulfill({json:{items:profile?[profile]:[],next_cursor:null}});
  if(path==='/api/v1/credits/balance')return route.fulfill({json:{available:100,reserved:0,funded_total:100,spent_total:0}});
  if(path==='/api/v1/appearance-understandings')return route.fulfill({json:{items:[]}});
  if(path==='/api/v1/appearance-understandings/quotes')return route.fulfill({status:201,json:{...record,status:'quoted',result:null,reference_rates_per_million_tokens:{input_token:'0.8',output_token:'2.7',input_token_cache:'0.1'}}});
  if(path.endsWith('/submit')){posts++;expect(route.request().headers()['x-provider-confirmation']).toBe('bailian_live');return route.fulfill({json:record})}
  if(path.endsWith('/confirm')){confirmed=route.request().postDataJSON();profile={id:'66666666-6666-4666-8666-666666666666',name:confirmed.name,revision:1,config:{...confirmed.config,source_asset_version_id:source,understanding_id:identity}};return route.fulfill({status:201,json:profile})}
  if(path==='/api/v1/quotes'){redraw=route.request().postDataJSON();return route.fulfill({status:201,json:{quote_id:identity,credits:16,expires_at:'2099-01-01T00:00:00Z',input_hash:'a'.repeat(64),provider_profile:'bailian_live'}})}
  return route.fulfill({json:{items:[],next_cursor:null}});
 });
 await page.goto('/');await page.getByRole('button',{name:'AI 理解产品外观',exact:true}).click();
 const modal=page.getByRole('dialog',{name:'AI 理解产品外观',exact:true});
 await modal.getByRole('button',{name:'获取外观理解费用说明'}).click();
 const fee=page.getByRole('dialog',{name:'确认外观理解费用'});
 await expect(fee.getByRole('button',{name:'确认费用并识别一次'})).toBeDisabled();
 await fee.getByRole('checkbox').check();await fee.getByRole('button',{name:'确认费用并识别一次'}).click();
 await expect(modal).toContainText('侧面接口被遮挡');
 const save=modal.getByRole('button',{name:'确认外观描述并使用'});await expect(save).toBeDisabled();
 await modal.getByRole('textbox',{name:'外观文字约束'}).fill('人工校订：黑色圆角盒体，保留正面标识与轮廓。');
 await modal.getByRole('checkbox').check();await save.click();await expect(modal).not.toBeVisible();
 expect(confirmed.confirmed).toBe(true);expect(confirmed.config.description).toContain('人工校订');
 await expect(page.getByText('AI理解后已确认 · 文字外观参考',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:/获取报价并继续/}).click();
 expect(redraw.appearance_profile_id).toBe(profile.id);expect(redraw.generation.protection_mode).toBe('none');expect(redraw.generation.regions).toEqual([]);expect(posts).toBe(1);
 await page.screenshot({path:'../../work/appearance-understanding-ui.png',fullPage:true});
});
