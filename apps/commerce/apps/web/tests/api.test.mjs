import assert from 'node:assert/strict';
import test from 'node:test';
import {ApiError,api,errorText} from '../src/api.ts';

const fetchOriginal=globalThis.fetch,documentOriginal=globalThis.document;
test.beforeEach(()=>{globalThis.document={cookie:'visual_csrf=fixture-csrf'}});
test.afterEach(()=>{globalThis.fetch=fetchOriginal;if(documentOriginal===undefined)delete globalThis.document;else globalThis.document=documentOriginal});
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});

test('public errors use Chinese messages and validated support IDs without exposing unknown codes',()=>{
 const id='12345678-1234-1234-1234-123456789abc';
 const text=errorText(new ApiError(500,'internal_constraint_name',id));assert.ok(text.includes(id));assert.ok(!text.includes('internal_constraint_name'));assert.match(text,/问题编号/);
 assert.match(errorText(new ApiError(422,'invalid_request')),/填写内容/);
 assert.ok(!errorText(new ApiError(500,'unknown','https://private.invalid')).includes('private.invalid'));
 assert.ok(!errorText(new Error('Failed to fetch https://private.invalid')).includes('private.invalid'));
 assert.equal(errorText(new Error('上传等待超时，请检查网络后重新选择图片')),'上传等待超时，请检查网络后重新选择图片');
});
test('untrusted error codes and support IDs cannot become visible diagnostics',async()=>{
 globalThis.fetch=async()=>json({error:{code:'internal/path/to/file',request_id:'https://private.invalid'}},500);
 await assert.rejects(api('/fixture'),error=>error instanceof ApiError&&error.code==='request_failed'&&error.requestId===undefined&&!errorText(error).includes('private.invalid'));
 const id='12345678-1234-1234-1234-123456789abc';globalThis.fetch=async()=>json({error:{code:'dependency_unavailable',request_id:id}},503);
 await assert.rejects(api('/fixture'),error=>error.requestId===id&&errorText(error).includes(id)&&!errorText(error).includes('dependency_unavailable'));
});
test('session refresh replays the exact write body and idempotency key with current CSRF only once',async()=>{
 const calls=[];let writes=0;
 globalThis.fetch=async(url,options)=>{
  calls.push({url,options});
  if(url.endsWith('/auth/refresh')){globalThis.document.cookie='visual_csrf=refreshed-fixture';return json({ok:true})}
  writes++;return writes===1?json({error:{code:'authentication_required'}},401):json({id:'same-task'});
 };
 const body=JSON.stringify({quote_id:'original-quote',prompt:'original-input'});
 assert.deepEqual(await api('/tasks',{method:'POST',body,headers:{'Idempotency-Key':'original-operation'}}),{id:'same-task'});
 assert.equal(calls.length,3);assert.equal(writes,2);
 assert.equal(calls[0].options.body,body);assert.equal(calls[2].options.body,body);
 assert.equal(calls[0].options.headers['Idempotency-Key'],'original-operation');assert.equal(calls[2].options.headers['Idempotency-Key'],'original-operation');
 assert.equal(calls[2].options.headers['X-CSRF-Token'],'refreshed-fixture');
});
test('aborted writes and failed logins are never automatically replayed',async()=>{
 let calls=0;globalThis.fetch=async()=>{calls++;throw new DOMException('aborted','AbortError')};
 await assert.rejects(api('/tasks',{method:'POST',body:'{}'}),{name:'AbortError'});assert.equal(calls,1);
 calls=0;globalThis.fetch=async()=>{calls++;return json({error:{code:'invalid_credentials'}},401)};
 await assert.rejects(api('/auth/login',{method:'POST',body:'{}'}),error=>error instanceof ApiError&&error.code==='invalid_credentials');assert.equal(calls,1);
});
