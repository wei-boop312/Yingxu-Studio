/** One signed PUT, without app cookies, model calls or transport retries. */
export function putSignedUpload(url:string,headers:Record<string,string>,file:File,signal:AbortSignal,onProgress:(percent:number|undefined)=>void):Promise<void>{
 return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();let settled=false;
  const done=(error?:Error)=>{if(settled)return;settled=true;signal.removeEventListener('abort',abort);if(error)reject(error);else resolve()};
  const abort=()=>{xhr.abort();done(new DOMException('上传已取消','AbortError'))};
  if(signal.aborted){done(new DOMException('上传已取消','AbortError'));return}
  try {
  xhr.open('PUT',url);xhr.withCredentials=false;xhr.timeout=120000;
  for(const [name,value] of Object.entries(headers))xhr.setRequestHeader(name,value);
  xhr.upload.onprogress=event=>{if(!settled)onProgress(event.lengthComputable&&event.total>0?Math.min(99,Math.floor(event.loaded*100/event.total)):undefined)};
  xhr.onload=()=>done(xhr.status>=200&&xhr.status<300?undefined:new Error('上传未完成，请检查连接后重新选择图片'));
  xhr.onerror=()=>done(new Error('上传连接中断，请检查网络后重新选择图片'));
  xhr.ontimeout=()=>done(new Error('上传等待超时，请检查网络后重新选择图片'));
  xhr.onabort=()=>done(new DOMException('上传已取消','AbortError'));
  signal.addEventListener('abort',abort,{once:true});xhr.send(file);
  } catch { done(new Error('上传暂不可用，请重新选择图片')) }
 });
}
