export class ApiError extends Error {
 status:number;code:string;requestId?:string;
 constructor(status:number,code:string,requestId?:string){super(code);this.status=status;this.code=code;this.requestId=requestId&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(requestId)?requestId:undefined}
}
export const csrf=()=>document.cookie.split('; ').find(x=>x.startsWith('visual_csrf='))?.split('=').slice(1).join('=')||'';
let refreshing:Promise<boolean>|null=null;
async function refresh(){
 if(!refreshing) refreshing=fetch('/api/v1/auth/refresh',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf()}}).then(r=>r.ok).finally(()=>{refreshing=null});
 return refreshing;
}
export async function api<T>(path:string,options:RequestInit={},retry=true):Promise<T>{
 const unsafe=options.method && !['GET','HEAD'].includes(options.method);
 const response=await fetch('/api/v1'+path,{...options,credentials:'same-origin',headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(unsafe?{'X-CSRF-Token':csrf()}:{}),...options.headers}});
 if(response.status===401 && retry && !['/auth/login','/auth/refresh','/auth/logout'].includes(path) && await refresh()) return api<T>(path,options,false);
 if(!response.ok){let code='request_failed';let requestId:string|undefined;try{const error=(await response.json()).error;if(typeof error?.code==='string'&&/^[a-z0-9_]{1,80}$/.test(error.code))code=error.code;const id=error?.request_id;if(typeof id==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))requestId=id}catch{};throw new ApiError(response.status,code,requestId)}
 return response.status===204?undefined as T:response.json();
}
export const post=<T>(path:string,body?:unknown,headers?:Record<string,string>)=>api<T>(path,{method:'POST',body:body===undefined?undefined:JSON.stringify(body),headers});
export const errors:Record<string,string>={appearance_live_provider_required:'AI自动理解需要百炼真实供应商模式',appearance_provider_unavailable:'外观理解供应商未就绪或授权已停用',appearance_reconciliation_required:'该原图有未确认的识别请求，请先对账，勿重复提交',appearance_source_too_large:'请使用不超过4MP、10MB、每边不超过8000像素且长宽比不超过10:1的工作图',appearance_confirmation_required:'请校订并确认外观描述',appearance_source_mismatch:'这份AI外观描述对应另一原图，请重新理解当前原图或选择对应资料',appearance_source_binding_immutable:'外观资料的原图来源不能更改，请新建识别',appearance_not_ready:'AI理解尚未完成，不能确认草稿',live_provider_confirmation_required:'供应商模式已更新，请刷新页面并重新确认真实调用费用',live_provider_not_authorized:'当前账号尚未启用真实供应商，请联系管理员',live_model_dimensions_unsupported:'当前尺寸不受真实生成模型支持，请调整工作图或输出尺寸',provider_runtime_revoked:'当前服务配置已停用，请联系管理员',unregistered_runtime_configuration:'服务配置尚未登记，请联系管理员',registered_runtime_binding_mismatch:'服务配置与报价不一致，请联系管理员',runtime_profile_not_enabled:'当前服务模式尚未启用，请联系管理员',invalid_credentials:'账号或密码不正确',invalid_request:'填写内容不符合要求，请检查后重试',service_not_configured:'服务尚未就绪，请联系管理员',upload_busy:'图片正在校验，请稍候',appearance_unavailable:'外观资料不可用，请重新选择',product_unavailable:'产品已归档或不可用，请重新选择',quote_consumed:'此报价已使用，请在任务记录中查看结果',authentication_required:'登录已过期，请重新登录',login_rate_limited:'尝试次数较多，请稍后再试',insufficient_credits:'可用积分不足',quote_expired:'报价已过期，请重新确认',quote_input_mismatch:'配置已变化，请重新获取报价',idempotency_conflict:'提交配置冲突，请重新确认',already_started:'任务已开始，暂不支持取消',decoder_capacity:'图片校验繁忙，请稍后重试',invalid_image:'图片格式、大小或内容不符合要求',upload_expired:'上传已过期，请重新选择图片',upload_rejected:'此图片未通过校验，请重新选择图片',validation_temporarily_unavailable:'图片校验暂不可用，可重新完成上传',revision_conflict:'内容已更新，请刷新后重试',dependency_unavailable:'服务暂不可用，请稍后重试',not_found:'内容不存在或没有访问权限',source_unavailable:'原图版本不可用',pixel_exact_size_mismatch:'精确保护需要与原图尺寸一致',csrf_rejected:'登录状态已变化，请刷新页面',origin_rejected:'访问地址与服务配置不一致'};
export function errorText(error:unknown){if(error instanceof ApiError){const message=errors[error.code]||'操作未完成，请稍后重试';return error.requestId?message+'（问题编号：'+error.requestId+'）':message}return error instanceof Error&&/[\u3400-\u9fff]/.test(error.message)?error.message:'连接暂不可用，请检查网络后重试'}
export type User={id:string,email:string,display_name:string};
export type Product={id:string,name:string,category:string,revision:number,archived_at:string|null};
export type AssetVersion={version_id:string,asset_id:string,width:number,height:number,sha256:string,canonical:boolean};
export type Task={id:string,product_id:string,source:{asset_id:string,version_id:string},status:'queued'|'processing'|'succeeded'|'failed'|'cancelled'|'uncertain',phase:string,revision:number,credits:{status:string,amount:number},created_at:string,error_code:string|null,result:null|{asset_version_id:string,sha256:string,width:number,height:number,download_endpoint:string,report_id:string},compliance:null|{auto_overall:string,report_id:string,detection_degraded:boolean}};
export type Page<T>={items:T[],next_cursor:string|null};
export type Balance={available:number,reserved:number,funded_total:number,spent_total:number};
export type Region={source_x:number,source_y:number,width:number,height:number,target_x:number,target_y:number};
export type Generation={type:'white_background'|'scene'|'lifestyle',prompt:string,output:{width:number,height:number,format:'png'},protection_mode:'pixel_exact'|'none',regions:Region[]};
export type Business={product_id:string,source_asset_version_id:string,appearance_profile_id?:string,generation:Generation,targets:{platform:string,marketplace:string,category:string,image_type:string}[]};
