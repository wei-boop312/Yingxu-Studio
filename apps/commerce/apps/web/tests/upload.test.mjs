import assert from 'node:assert/strict';
import test from 'node:test';
import {putSignedUpload} from '../src/upload.ts';

class Xhr {
 static all=[];upload={};status=0;sent=0;headers={};
 constructor(){Xhr.all.push(this)}
 open(method,url){this.method=method;this.url=url}
 setRequestHeader(name,value){this.headers[name]=value}
 send(file){this.file=file;this.sent++}
 abort(){this.aborted=true;this.onabort?.()}
}
const original=globalThis.XMLHttpRequest;
test.beforeEach(()=>{globalThis.XMLHttpRequest=Xhr;Xhr.all=[]});
test.afterEach(()=>{globalThis.XMLHttpRequest=original});

test('reports byte progress, waits for HTTP success, omits cookies and never retries',async()=>{
 const progress=[];const file=new File(['abc'],'fixture.png',{type:'image/png'});
 const promise=putSignedUpload('https://storage.invalid/signed',{'Content-Type':'image/png'},file,new AbortController().signal,p=>progress.push(p));
 const xhr=Xhr.all[0];assert.equal(xhr.withCredentials,false);assert.equal(xhr.method,'PUT');assert.equal(xhr.file,file);
 xhr.upload.onprogress({lengthComputable:true,loaded:1,total:3});xhr.upload.onprogress({lengthComputable:true,loaded:3,total:3});xhr.upload.onprogress({lengthComputable:false});
 assert.deepEqual(progress,[33,99,undefined]);xhr.status=200;xhr.onload();await promise;
 assert.equal(xhr.sent,1);assert.equal(Xhr.all.length,1);assert.deepEqual(xhr.headers,{'Content-Type':'image/png'});
});
test('abort before start sends no request and mid-flight abort settles once',async()=>{
 const before=new AbortController();before.abort();await assert.rejects(putSignedUpload('unused',{},new File([],'empty'),before.signal,()=>{}),{name:'AbortError'});
 assert.equal(Xhr.all[0].sent,0);
 const controller=new AbortController();const promise=putSignedUpload('unused',{},new File([],'empty'),controller.signal,()=>{});
 controller.abort();await assert.rejects(promise,{name:'AbortError'});assert.equal(Xhr.all[1].aborted,true);assert.equal(Xhr.all[1].sent,1);
});
test('HTTP failure timeout and network errors do not repeat PUT or expose its URL',async()=>{
 for(const event of ['onload','ontimeout','onerror']){
  const promise=putSignedUpload('https://storage.invalid?fixture=private',{},new File([],'empty'),new AbortController().signal,()=>{});
  const xhr=Xhr.all.at(-1);xhr.status=503;xhr[event]();await assert.rejects(promise,error=>!error.message.includes('storage.invalid'));assert.equal(xhr.sent,1);
 }
 assert.equal(Xhr.all.length,3);
});
test('transport setup failure does not expose the signed URL',async()=>{
 class Broken extends Xhr {open(){throw new Error('private upload URL')}}
 globalThis.XMLHttpRequest=Broken;
 await assert.rejects(putSignedUpload('unused',{},new File([],'empty'),new AbortController().signal,()=>{}),error=>!error.message.includes('private'));
 assert.equal(Xhr.all[0].sent,0);
});
