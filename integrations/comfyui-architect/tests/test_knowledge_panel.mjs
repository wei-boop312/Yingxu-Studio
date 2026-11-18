// Component unit test with an owned minimal DOM and mock API, not browser proof.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.parent = null; this.attrs = {}; this.handlers = {}; this.style = {}; this.value = ""; this.disabled = false; this.textContent = ""; }
  setAttribute(name, value) { this.attrs[name] = value; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } if (this.tag === "select" && this.children.length) this.value = this.children[0].value; }
  prepend(...children) { for (const child of children) child.parent = this; this.children.unshift(...children); }
  replaceChildren(...children) { for (const child of this.children) child.parent = null; this.children = []; this.append(...children); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; }
  addEventListener(event, handler) { (this.handlers[event] ||= []).push(handler); }
  async emit(event) { for (const handler of this.handlers[event] || []) await handler({ target: this }); }
  click() { if (this.tag === "a") downloads++; return this.emit("click"); }
  get isConnected() { return this === document.body || Boolean(this.parent?.isConnected); }
}
function all(element = document.body) { return [element, ...element.children.flatMap(child => all(child))]; }
globalThis.document = { body: new Element("body"), createElement: tag => new Element(tag), querySelector: selector => all().find(item => item.className?.split(" ").includes(selector.slice(1))) };
const byText = value => all().find(item => item.textContent === value);
const byLabel = value => all().find(item => item.attrs["aria-label"] === value);
let calls = [], downloads = 0, nextResponse = null;
const preview = () => ({ execution_authorized: false, api_enabled: false, embedding_enabled: false,
  expected: { corpus_revision: "a".repeat(64), registry_generation: 1, template_id: "template:fixture", requirement_sha256: "b".repeat(64) },
  retrieval: { document_count: 82, corpus_revision: "a".repeat(64), registry_generation: 1, results: [] },
  selected_template: "template:fixture", selection: { models: [{ binding: "checkpoint", label: "测试基础模型", choices: ["owned-model"] }], source_image_required: true, source_images: ["owned-input.png"] },
});
globalThis.__knowledgeTestApi = { async fetchApi(path, options) {
  calls.push({ path, body: JSON.parse(options.body), signal: options.signal });
  if (nextResponse) { const response = nextResponse; nextResponse = null; return await response(); }
  const value = preview();
  if (JSON.parse(options.body).build_draft) Object.assign(value, { status: "compiled-not-executed", compiled: { ui_workflow: { nodes: [{ id: 1 }] }, draft: false, execution_ready: true }, validation: { valid: true } });
  return new Response(JSON.stringify({ ok: true, data: value }));
} };
const source = (await readFile(new URL("../web/js/knowledge_panel.js", import.meta.url), "utf8")).replace('import { api } from "../../../scripts/api.js";', "const api = globalThis.__knowledgeTestApi;");
const { openKnowledgePanel } = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const search = () => byText("检索并检查依赖（不编译）");
const build = () => byText("编译草稿（不运行）");
const download = () => byText("导出工作流JSON（不运行）");

openKnowledgePanel("生成一张产品背景");
assert.equal(calls.length, 0); assert(build().disabled); assert(download().disabled);
await search().emit("click");
assert.equal(calls.length, 1); assert.equal(calls[0].body.build_draft, false);
assert.equal(byLabel("测试基础模型").value, ""); assert.equal(byLabel("原始输入图片").value, "");
await build().emit("click"); assert.equal(calls.length, 1); // Explicit selections mandatory.
byLabel("测试基础模型").value = "owned-model"; byLabel("原始输入图片").value = "owned-input.png";
await build().emit("click"); assert.equal(calls.length, 2); assert.equal(calls[1].body.expected.template_id, "template:fixture");
assert.deepEqual(calls[1].body.model_overrides, { checkpoint: "owned-model" }); assert.equal(download().disabled, false);
await download().emit("click"); assert.equal(downloads, 1);
byLabel("知识辅助需求").value = "新需求"; await byLabel("知识辅助需求").emit("input"); assert(build().disabled); assert(download().disabled);
await search().emit("click");
let resolveDelayed; nextResponse = () => new Promise(resolve => { resolveDelayed = resolve; });
const delayed = search().emit("click");
await byLabel("知识辅助需求").emit("input");
resolveDelayed(new Response(JSON.stringify({ ok: true, data: preview() }))); await delayed;
assert(build().disabled); assert.equal(byLabel("测试基础模型"), undefined); // Late result cannot restore stale selections.
nextResponse = () => new Response(JSON.stringify({ ok: true, data: { ...preview(), api_enabled: true } }));
await search().emit("click"); assert(build().disabled);
let resolveClosed; nextResponse = () => new Promise(resolve => { resolveClosed = resolve; });
const closing = search().emit("click"); await byText("关闭").emit("click");
resolveClosed(new Response(JSON.stringify({ ok: true, data: preview() }))); await closing;
assert.equal(document.querySelector(".wa-knowledge-overlay"), undefined);
openKnowledgePanel("first"); openKnowledgePanel("second");
assert.equal(all().filter(item => item.className?.includes("wa-knowledge-overlay")).length, 1);
await byText("关闭").emit("click");
openKnowledgePanel("给模特换装保持脸部不变");
nextResponse = () => new Response(JSON.stringify({ ok: true, data: { ...preview(), selected_template: null,
  selection: { models: [], source_image_required: false, source_images: [] },
  person_request: { title: "人物换装／试穿", required_inputs: [{ label: "人物原图" }, { label: "服装参考图" }], limitations: ["没有可执行换装模板"] },
} }));
await search().emit("click");
assert(build().disabled); assert(byText("人物需求：人物换装／试穿"));
assert(byText("需要的输入：人物原图、服装参考图"));
await byText("关闭").emit("click");
assert(calls.every(call => call.path === "/workflow-architect/knowledge/assist"));
console.log("11 component scenarios passed: no graph, /prompt, real browser or external API used");

const identitySource = (await readFile(new URL("../web/js/identity_panel.js", import.meta.url), "utf8")).replace('import { api } from "../../../scripts/api.js";', "const api = globalThis.__knowledgeTestApi;");
const { openIdentityPanel } = await import("data:text/javascript;base64," + Buffer.from(identitySource).toString("base64"));
const identityPreview = () => ({ route: "photomaker-v1-native", execution_authorized: false, runtime_verified: false, expected: { registry_generation: 1, template_sha256: "c".repeat(64) },
  missing_nodes: [], missing_models: [], notice: "static-only", selection: { checkpoints: ["sd_xl_base_1.0.safetensors"], photomaker: ["photomaker-v1.bin"], lora: ["workflow_architect_photomaker_v1_lora.safetensors"], reference_images: ["owned-reference.png"] } });
let identityCalls = [];
globalThis.__knowledgeTestApi.fetchApi = async (path, options) => {
  const body = JSON.parse(options.body); identityCalls.push({ path, body });
  if (nextResponse) { const response = nextResponse; nextResponse = null; return await response(); }
  const value = path.endsWith("/refresh") ? {} : identityPreview();
  if (body.build_draft) Object.assign(value, { status: "static-compiled-not-runtime-verified", compiled: { ui_workflow: { nodes: [{ id: 1 }] } }, validation: { valid: true } });
  return new Response(JSON.stringify({ ok: true, data: value }));
};
openIdentityPanel(); assert.equal(identityCalls.length, 0);
const identityBuild = () => byText("编译身份参考草稿（不运行）");
const identityDownload = () => byText("导出身份参考JSON（不运行）");
assert(identityBuild().disabled); await byText("检查当前依赖").emit("click");
assert.equal(identityCalls.length, 2); assert.equal(byLabel("SDXL基础模型").value, "");
await identityBuild().emit("click"); assert.equal(identityCalls.length, 2);
byLabel("SDXL基础模型").value = "sd_xl_base_1.0.safetensors";
byLabel("PhotoMaker v1权重").value = "photomaker-v1.bin";
byLabel("PhotoMaker身份LoRA（本地提取）").value = "workflow_architect_photomaker_v1_lora.safetensors";
byLabel("经授权的身份参考图").value = "owned-reference.png";
byLabel("主体描述词").value = "man";
byLabel("身份参考画面描述").value = "wearing a blue jacket";
byLabel("确认参考肖像已获授权").checked = true;
await identityBuild().emit("click"); assert.equal(identityCalls.length, 3);
assert.equal(identityCalls[2].body.expected.template_sha256, "c".repeat(64));
assert.equal(identityCalls[2].body.width, 512); assert.equal(identityDownload().disabled, false);
await byLabel("经授权的身份参考图").emit("change"); assert.equal(byLabel("确认参考肖像已获授权").checked, false); assert(identityDownload().disabled);
await byText("关闭").emit("click");
openIdentityPanel();
nextResponse = () => new Response(JSON.stringify({ ok: false, error: { message: "backend unavailable" } }), { status: 404 });
await byText("检查当前依赖").emit("click"); assert(identityBuild().disabled);
await byText("关闭").emit("click");
assert(identityCalls.every(call => ["/workflow-architect/refresh", "/workflow-architect/people/identity/prepare"].includes(call.path)));
console.log("8 identity component scenarios passed with mock API only; no real browser, model execution or /prompt calls");

let tryonCalls = [], inserted = 0;
globalThis.__tryonApp = { async loadGraphData() { inserted++; } };
globalThis.confirm = () => true;
const tryonPreview = () => ({route:"klein-tryon-experimental",execution_authorized:false,quality_approved:false,expected:{registry_generation:1,template_sha256:"d".repeat(64)},missing_nodes:[],missing_models:[],selection:{images:["person.png","garment.png"],models:{diffusion:"owned",encoder:"owned",vae:"owned"}}});
globalThis.__knowledgeTestApi.fetchApi = async(path,options)=>{
  const body=JSON.parse(options.body);tryonCalls.push({path,body});
  if(nextResponse){const response=nextResponse;nextResponse=null;return await response();}
  const value=path.endsWith("/refresh")?{}:tryonPreview();
  if(body.build_draft)Object.assign(value,{compiled:{ui_workflow:{nodes:[{id:1}]}},validation:{valid:true}});
  return new Response(JSON.stringify({ok:true,data:value}),{headers:{'Content-Type':'application/json'}});
};
const tryonSource=(await readFile(new URL("../web/js/tryon_panel.js",import.meta.url),"utf8"))
 .replace('import { api } from "../../../scripts/api.js";',"const api=globalThis.__knowledgeTestApi;")
 .replace('import { app } from "../../../scripts/app.js";',"const app=globalThis.__tryonApp;");
const {openTryonPanel}=await import("data:text/javascript;base64,"+Buffer.from(tryonSource).toString("base64"));
openTryonPanel();assert.equal(tryonCalls.length,0);
const tb=()=>byText("编译双图模板（不运行）"),ti=()=>byText("插入画布（替换当前图，不运行）");
assert(tb().disabled);await byText("检查依赖并刷新图片").emit("click");assert.equal(tryonCalls.length,2);
assert.equal(byLabel("图1 人物原图").value,"");await tb().emit("click");assert.equal(tryonCalls.length,2);
byLabel("图1 人物原图").value="person.png";byLabel("图2 服装参考图").value="garment.png";
all().find(e=>e.tag==="input" && e.type==="checkbox").checked=true;
await tb().emit("click");assert.equal(tryonCalls.length,3);assert.equal(tryonCalls[2].body.person_image,"person.png");assert.equal(tryonCalls[2].body.garment_image,"garment.png");assert(!ti().disabled);
await ti().emit("click");assert.equal(inserted,1);assert.equal(tryonCalls.length,3);
await byLabel("换装细节").emit("input");assert(ti().disabled);
await byText("关闭").emit("click");openTryonPanel();
nextResponse=()=>new Response(JSON.stringify({ok:false,error:{message:"unavailable"}}),{status:404});
await byText("检查依赖并刷新图片").emit("click");assert(tb().disabled);await byText("关闭").emit("click");
assert(tryonCalls.every(c=>["/workflow-architect/refresh","/workflow-architect/people/tryon/prepare"].includes(c.path)));
console.log("7 tryon component scenarios passed: explicit inputs, consent, compile/insert only, stale draft clearing and errors; mock DOM, not browser proof");

let foundationCalls=[];
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  foundationCalls.push(path);
  if(nextResponse){const response=nextResponse;nextResponse=null;return await response();}
  return new Response(JSON.stringify({ok:true,data:path.endsWith('/refresh')?{}:{phase:1,api_called:false,execution_authorized:false,document_count:82,registry_node_count:23,candidates:[],sources:[]}}));
};
const foundationSource=(await readFile(new URL('../web/js/foundation_panel.js',import.meta.url),'utf8')).replace('import { api } from "../../../scripts/api.js";','const api=globalThis.__knowledgeTestApi;');
const {openFoundationPanel}=await import('data:text/javascript;base64,'+Buffer.from(foundationSource).toString('base64'));
openFoundationPanel();assert.equal(foundationCalls.length,0);
const fr=()=>byText('扫描并生成来源与兼容报告'),fd=()=>byText('导出底座报告JSON');
await fr().emit('click');assert.equal(foundationCalls.length,0);
byLabel('底座验收需求').value='参考图换装';await fr().emit('click');assert.equal(foundationCalls.length,2);assert(!fd().disabled);
await byLabel('底座验收需求').emit('input');assert(fd().disabled);
nextResponse=()=>new Response(JSON.stringify({ok:false,error:{message:'offline'}}),{status:404});
await fr().emit('click');assert(fd().disabled);await byText('关闭').emit('click');
assert(foundationCalls.every(p=>['/workflow-architect/refresh','/workflow-architect/foundation/report'].includes(p)));
console.log('5 foundation component scenarios passed: no startup calls, empty demand, report, invalidation and failure; mock DOM only');

let plannerCalls=[];
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  plannerCalls.push({path,body:JSON.parse(options.body)});
  if(nextResponse){const response=nextResponse;nextResponse=null;return await response();}
  const value=path.endsWith('/refresh')?{}:path.endsWith('/context')?{phase:2,api_called:false,execution_authorized:false,status:'ready-for-proposal',context:{candidates:[]},manual_prompt:'owned context'}:{phase:2,execution_authorized:false,status:'grounded-proposal-not-executed'};
  return new Response(JSON.stringify({ok:true,data:value}),{headers:{'Content-Type':'application/json'}});
};
const plannerSource=(await readFile(new URL('../web/js/planner_panel.js',import.meta.url),'utf8')).replace('import { api } from "../../../scripts/api.js";','const api=globalThis.__knowledgeTestApi;').replace('import { openCompilePanel } from "./compile_panel.js";','const openCompilePanel=()=>{};');
const {openPlannerPanel}=await import('data:text/javascript;base64,'+Buffer.from(plannerSource).toString('base64'));
openPlannerPanel();assert.equal(plannerCalls.length,0);
const pc=()=>byText('生成可交给大模型的上下文'),pv=()=>byText('校验导入方案（不运行）'),pe=()=>byText('导出规划记录（不是ComfyUI工作流）');
assert(pv().disabled);await pc().emit('click');assert.equal(plannerCalls.length,0);
byLabel('规划需求').value='参考图换装';await pc().emit('click');assert.equal(plannerCalls.length,2);assert(!pv().disabled);
let copiedPrompt=null;Object.defineProperty(globalThis,'navigator',{value:{clipboard:{writeText:async value=>{copiedPrompt=value;}}},configurable:true});
await byText('复制完整提示词（含回复格式）').emit('click');assert.equal(copiedPrompt,'owned context');assert.equal(plannerCalls.length,2);
byLabel('大模型JSON方案').value='```json';await pv().emit('click');assert.equal(plannerCalls.length,2);assert(pe().disabled);
byLabel('大模型JSON方案').value='{}';await pv().emit('click');assert.equal(plannerCalls.length,3);assert(!pe().disabled);
await byLabel('大模型JSON方案').emit('input');assert(pe().disabled);
await byLabel('规划需求').emit('input');assert(pv().disabled);assert(byText('导出给ChatGPT的提示词').disabled);
nextResponse=()=>new Response(JSON.stringify({ok:false,error:{message:'unavailable'}}),{status:404});
await pc().emit('click');assert(pv().disabled);await byText('关闭').emit('click');
assert(plannerCalls.every(c=>['/workflow-architect/refresh','/workflow-architect/planner/context','/workflow-architect/planner/validate'].includes(c.path)));
console.log('9 planner mock-DOM scenarios passed: full prompt copy, explicit context/export/import, syntax/error/stale invalidation, no external API or execution');

let compileCalls=[],graphInserts=0,lastBlob=null;
globalThis.__compileApp={async loadGraphData(graph){assert(graph.nodes.length);assert(graph.links.length);graphInserts++;}};
const oldCreate=URL.createObjectURL;URL.createObjectURL=blob=>{lastBlob=blob;return oldCreate(blob);};
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  const body=JSON.parse(options.body);compileCalls.push({path,body});
  if(nextResponse){const response=nextResponse;nextResponse=null;return await response();}
  const value={phase:3,execution_authorized:false,selection:{models:[{binding:'diffusion',choices:['owned']}],image_roles:['person_image','garment_image'],images:['person.png','garment.png'],task:'virtual_tryon',output:{width:576,height:768},prompt_default:'Match the reference outfit.'}};
  if(body.build)Object.assign(value,{status:'workflow-compiled-not-executed',compiled:{ui_workflow:{nodes:[{id:1},{id:2}],links:[[1,1,0,2,0,'IMAGE']]},api_prompt:{'1':{class_type:'LoadImage'}}},validation:{valid:true},repair_attempt:body.repair_attempt});
  return new Response(JSON.stringify({ok:true,data:value}),{headers:{'Content-Type':'application/json'}});
};
const compileSource=(await readFile(new URL('../web/js/compile_panel.js',import.meta.url),'utf8'))
 .replace('import { api } from "../../../scripts/api.js";','const api=globalThis.__knowledgeTestApi;')
 .replace('import { app } from "../../../scripts/app.js";','const app=globalThis.__compileApp;');
const {openCompilePanel}=await import('data:text/javascript;base64,'+Buffer.from(compileSource).toString('base64'));
openCompilePanel({text:'参考图换装',proposal:{template_id:'owned'}});assert.equal(compileCalls.length,0);
const cb=()=>byText('生成真实工作流（不运行）'),cd=()=>byText('导出ComfyUI工作流JSON'),ci=()=>byText('插入真实工作流（替换画布）');
assert(cb().disabled);await byText('核验方案并读取可选图片/模型').emit('click');assert.equal(compileCalls.length,1);assert(!cb().disabled);
assert.equal(byLabel('人物原图').value,'');await cb().emit('click');assert.equal(compileCalls.length,1);
byLabel('模型 diffusion').value='owned';byLabel('人物原图').value='person.png';byLabel('服装参考图').value='garment.png';
all().find(e=>e.tag==='input'&&e.type==='checkbox').checked=true;
await cb().emit('click');assert.equal(compileCalls.length,2);assert(!cd().disabled);
await cd().emit('click');const exported=JSON.parse(await lastBlob.text());assert(exported.nodes.length);assert(exported.links.length);assert(!exported.proposal);
await ci().emit('click');assert.equal(graphInserts,1);assert.equal(compileCalls.length,2);
await byLabel('实际工作流提示词').emit('input');assert(cd().disabled);await cb().emit('click');assert.equal(compileCalls.length,2);
byLabel('显式修订原因').value='PROMPT';await cb().emit('click');assert.equal(compileCalls.length,3);assert.equal(compileCalls[2].body.repair_attempt,1);
await cb().emit('click');assert.equal(compileCalls.length,4);assert(cb().disabled);
await byLabel('编译原需求').emit('input');assert(cd().disabled);assert(cb().disabled);
await byText('关闭').emit('click');openCompilePanel({text:'换装',proposal:{}});
nextResponse=()=>new Response('405: Method Not Allowed',{status:405});await byText('核验方案并读取可选图片/模型').emit('click');assert(cb().disabled);assert(all().some(e=>e.textContent.includes('后端接口未加载')));await byText('关闭').emit('click');
assert(compileCalls.every(c=>c.path==='/workflow-architect/planner/compile'));
URL.createObjectURL=oldCreate;
console.log('10 compile mock-DOM scenarios passed: actual workflow export, manual insert, explicit bindings, finite revisions and HTTP errors; no queue or real browser');

{
const dimensionCalls=[];let defaultPrompt='Preserve the source; resize only.';
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  const body=JSON.parse(options.body);dimensionCalls.push(body);
  if(nextResponse){const fn=nextResponse;nextResponse=null;return await fn();}
  const data={phase:3,execution_authorized:false,selection:{models:[],image_roles:['source_image'],images:['owned.png'],task:'image_resize',output:{width:256,height:256},prompt_default:defaultPrompt}};
  if(body.build)Object.assign(data,{status:'workflow-compiled-not-executed',compiled:{ui_workflow:{nodes:[{id:1},{id:2}],links:[[1,1,0,2,0,'IMAGE']]},api_prompt:{}},validation:{valid:true}});
  return new Response(JSON.stringify({ok:true,data}),{headers:{'Content-Type':'application/json'}});
};
openCompilePanel({text:'调整图片尺寸为256x256',proposal:{template_id:'resize'}});
assert.equal(byLabel('实际工作流提示词').value,'');
await byText('核验方案并读取可选图片/模型').emit('click');
assert.equal(byLabel('实际输出宽度').value,'256');assert.equal(byLabel('实际输出高度').value,'256');assert(!byLabel('实际工作流提示词').value.includes('garment'));
byLabel('原图（重绘须含正确蒙版）').value='owned.png';all().find(e=>e.tag==='input'&&e.type==='checkbox').checked=true;
byLabel('实际输出高度').value='';await cb().emit('click');assert.equal(dimensionCalls.length,1);
byLabel('实际输出高度').value='256';byLabel('实际输出宽度').value='257';await cb().emit('click');assert.equal(dimensionCalls.length,1);
byLabel('实际输出宽度').value='256';await cb().emit('click');assert.equal(dimensionCalls.length,2);assert.equal(dimensionCalls[1].width,256);assert.equal(dimensionCalls[1].height,256);
await byLabel('实际输出宽度').emit('input');assert(cd().disabled);assert.equal(all().find(e=>e.tag==='input'&&e.type==='checkbox').checked,false);
byLabel('编译原需求').value='生成一张主图';await byLabel('编译原需求').emit('input');assert.equal(byLabel('实际工作流提示词').value,'');
defaultPrompt='';await byText('核验方案并读取可选图片/模型').emit('click');assert.equal(byLabel('实际工作流提示词').value,'');
await byText('关闭').emit('click');openCompilePanel({text:'resize',proposal:{}});
nextResponse=()=>new Response(JSON.stringify({ok:true,data:{phase:3,execution_authorized:false,selection:{models:[],image_roles:[],images:[]}}}),{headers:{'Content-Type':'application/json'}});
await byText('核验方案并读取可选图片/模型').emit('click');assert(cb().disabled);await byText('关闭').emit('click');
console.log('6 compile-dimension mock-DOM scenarios passed; requested 256 preserved, no generic garment prompt, invalid size blocked, stale clearing and old backend rejected');
}

let apiCalls=[];
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  apiCalls.push({path,body:JSON.parse(options.body)});
  if(nextResponse){const response=nextResponse;nextResponse=null;return await response();}
  const value=path.endsWith('/status')?{configured:false,status:'not-configured'}:{sent:false,configured:false,credential_read:false};
  return new Response(JSON.stringify({ok:true,data:value}),{headers:{'Content-Type':'application/json'}});
};
const apiSource=(await readFile(new URL('../web/js/api_support_panel.js',import.meta.url),'utf8'))
 .replace('import { api } from "../../../scripts/api.js";','const api=globalThis.__knowledgeTestApi;')
 .replace('import { openCompilePanel } from "./compile_panel.js";','const openCompilePanel=()=>{};');
const {openApiSupportPanel}=await import('data:text/javascript;base64,'+Buffer.from(apiSource).toString('base64'));
openApiSupportPanel();assert.equal(apiCalls.length,0);
const ap=()=>byText('单次API规划（可能收费，不生图）');assert(ap().disabled);
await byText('读取连接状态').emit('click');assert.equal(apiCalls.length,1);assert(ap().disabled);
byLabel('API规划需求').value='参考图换装';byLabel('非密钥API配置').value='{}';await byText('检查配置与请求结构（不联网）').emit('click');assert.equal(apiCalls.length,2);assert(ap().disabled);
byLabel('非密钥API配置').value='not-json';await byText('检查配置与请求结构（不联网）').emit('click');assert.equal(apiCalls.length,2);
nextResponse=()=>new Response('405',{status:405});await byText('读取连接状态').emit('click');assert(ap().disabled);
await byText('关闭').emit('click');assert(apiCalls.every(c=>['/workflow-architect/api/status','/workflow-architect/api/profile-check'].includes(c.path)));
console.log('6 API support mock-DOM scenarios passed: no startup network, disabled-unconfigured, dryrun only, invalid JSON and HTTP errors; no secrets or provider calls');

let detailCalls=[];
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  detailCalls.push(path);let value={};
  if(path.endsWith('/context'))value={context_digest:'a'.repeat(64),default_plan:{context_digest:'a'.repeat(64),modules:[]},context:{product:{name:'自有商品'}},selection:{models:[{binding:'checkpoint',choices:['owned-model']}],images:['owned.png']},manual_prompt:'owned full detail instructions'};
  if(path.endsWith('/validate'))value={sections:[{title:'商品展示',copy:['已提供事实']}]};
  if(path.endsWith('/compile'))value={sections:[],jobs:[],bundle_sha256:'b'.repeat(64)};
  if(path.endsWith('/status'))value={configured:false};
  return new Response(JSON.stringify({ok:true,data:value}),{headers:{'Content-Type':'application/json'}});
};
const detailSource=(await readFile(new URL('../web/js/detail_panel.js',import.meta.url),'utf8'))
 .replace("import { api } from '../../../scripts/api.js';",'const api=globalThis.__knowledgeTestApi;')
 .replace("import { runDetailJobs,renderDetailPage,validateJournal } from './detail_runner.js';",'const runDetailJobs=()=>{throw Error("must not queue")},renderDetailPage=()=>{},validateJournal=j=>j;');
const {openDetailPanel}=await import('data:text/javascript;base64,'+Buffer.from(detailSource).toString('base64'));
openDetailPanel();assert.equal(detailCalls.length,0);
assert(byText('确认后连续运行六张').disabled);
await byText('确认后连续运行六张').emit('click');
await byText('仅明确失败模块：允许人工重试一次').emit('click');
assert.equal(detailCalls.length,0);
byLabel('详情商品名称').value='自有商品';byLabel('详情真实资料').value='蓝色';
await byText('读取环境并生成六模块草案').emit('click');assert.equal(detailCalls.length,2);
await byText('校验方案并预览文案').emit('click');assert(detailCalls.at(-1).endsWith('/validate'));
const countBefore=detailCalls.length;await byText('编译六份真实工作流').emit('click');assert.equal(detailCalls.length,countBefore);
await byText('API规划六模块（可能收费）').emit('click');assert(detailCalls.at(-1).endsWith('/status'));assert(!detailCalls.some(p=>p.endsWith('/propose')));
byLabel('详情六模块方案').value='not-json';await byText('校验方案并预览文案').emit('click');assert(!detailCalls.includes('/prompt'));
await byText('关闭').emit('click');assert(!document.querySelector('.wa-detail-overlay'));
console.log('7 detail-panel mock-DOM scenarios passed; no queue, credentials, external API or real browser');

let posterCalls=[];
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  posterCalls.push({path,body:JSON.parse(options.body)});
  const value=path.endsWith('/options')?{font_available:true,assets:[{filename:'owned.png',subfolder:'',type:'input'}]}:
    {executed:false,package_id:'a'.repeat(32),preview:{filename:'brand_poster_'+('a'.repeat(32))+'.png',subfolder:'workflow_architect',type:'output'},compiled:{ui_workflow:{nodes:[{id:1}],links:[[1,1,0,2,0,'IMAGE']]}}};
  return new Response(JSON.stringify({ok:true,data:value}),{headers:{'Content-Type':'application/json'}});
};
const posterSource=(await readFile(new URL('../web/js/poster_panel.js',import.meta.url),'utf8')).replace("import { api } from '../../../scripts/api.js';",'const api=globalThis.__knowledgeTestApi;');
const {openPosterPanel}=await import('data:text/javascript;base64,'+Buffer.from(posterSource).toString('base64'));
openPosterPanel();assert.equal(posterCalls.length,0);const pp=()=>byText('生成品牌首屏并编译原生合成图');assert(pp().disabled);
await byText('读取本机素材').emit('click');assert.equal(posterCalls.length,1);
await pp().emit('click');assert.equal(posterCalls.length,1);
const posterConsent=all().find(e=>e.tag==='input'&&e.type==='checkbox');posterConsent.checked=true;
const styleBox=byLabel('品牌海报英文与参数JSON'),originalStyle=styleBox.value;styleBox.value='bad json';await pp().emit('click');assert.equal(posterCalls.length,1);styleBox.value=originalStyle;
await pp().emit('click');assert.equal(posterCalls.length,1);
for(const e of all().filter(e=>e.tag==='select'))e.value='0';await pp().emit('click');assert.equal(posterCalls.length,2);
assert.equal(Object.keys(posterCalls[1].body.assets).length,6);assert.equal(posterCalls[1].body.confirmed,true);
assert(!byText('下载含素材的完整工作流包').disabled);await styleBox.emit('input');assert(byText('下载含素材的完整工作流包').disabled);
await byText('关闭').emit('click');assert(!document.querySelector('.wa-poster-overlay'));assert(posterCalls.every(c=>c.path.includes('/poster/')));
console.log('8 poster-panel mock-DOM scenarios passed; explicit roles/consent, stale clearing, no queue or external API');

{
let promptCalls=[];
const promptData=JSON.parse(await readFile(new URL('../workflow_architect/templates/prompt_library.json',import.meta.url),'utf8'));
const promptCatalog={...promptData,content_sha256:'c'.repeat(64),execution_authorized:false,api_called:false,runtime_verified:false};
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  const body=JSON.parse(options.body);promptCalls.push({path,body});
  if(nextResponse){const response=nextResponse;nextResponse=null;return await response();}
  let data=promptCatalog;
  if(path.endsWith('/render')){
    const card=structuredClone(promptData.cards.find(c=>c.id===body.id));
    card.positive=card.positive.replace(/\{([a-z_]+)\}/g,(_,k)=>body.variables[k]);card.variables=body.variables;
    data={card,content_sha256:promptCatalog.content_sha256,execution_authorized:false,api_called:false,notice:'not workflow'};
  }
  return new Response(JSON.stringify({ok:true,data}));
};
const promptSource=(await readFile(new URL('../web/js/prompt_library_panel.js',import.meta.url),'utf8')).replace("import { api } from '../../../scripts/api.js';",'const api=globalThis.__knowledgeTestApi;');
const {openPromptLibraryPanel}=await import('data:text/javascript;base64,'+Buffer.from(promptSource).toString('base64'));
openPromptLibraryPanel();assert.equal(promptCalls.length,0);
const pc=()=>byText('填写并生成可复制提示词'),pd=()=>byText('导出提示词资料JSON（不是工作流）');
assert(pc().disabled);assert(pd().disabled);
await byText('读取提示词库').emit('click');assert.equal(promptCalls.length,1);assert.equal(byLabel('提示词卡').children.length,80);
byLabel('提示词关键词').value='整套替换';await byLabel('提示词关键词').emit('input');assert.equal(byLabel('提示词卡').children.length,1);
const garment=byLabel('变量 garment');assert(garment);garment.value='the blue reference outfit';
await pc().emit('click');assert.equal(promptCalls.length,2);assert.equal(promptCalls[1].body.variables.garment,'the blue reference outfit');assert(!pd().disabled);
assert(byLabel('已填写提示词与检查清单').value.includes('not merely recolor'));assert(!byLabel('已填写提示词与检查清单').value.includes('{garment}'));
const priorDownloads=downloads;await pd().emit('click');assert.equal(downloads,priorDownloads+1);
await garment.emit('input');assert(pd().disabled);assert.equal(byLabel('已填写提示词与检查清单').value,'');
garment.value='{unresolved}';await pc().emit('click');assert.equal(promptCalls.length,2);
byLabel('提示词关键词').value='does-not-exist';await byLabel('提示词关键词').emit('input');assert(pc().disabled);assert(pd().disabled);
byLabel('提示词关键词').value='整套替换';await byLabel('提示词关键词').emit('input');
let resolvePrompt;nextResponse=()=>new Promise(resolve=>{resolvePrompt=resolve;});const latePrompt=pc().emit('click');
await byLabel('变量 garment').emit('input');resolvePrompt(new Response(JSON.stringify({ok:true,data:{...promptCatalog,card:promptData.cards[15]}})));await latePrompt;assert(pd().disabled);
let resolvePromptClose;nextResponse=()=>new Promise(resolve=>{resolvePromptClose=resolve;});const closePrompt=pc().emit('click');await byText('关闭').emit('click');
resolvePromptClose(new Response(JSON.stringify({ok:true,data:promptCatalog})));await closePrompt;assert(!document.querySelector('.wa-prompts-overlay'));
openPromptLibraryPanel();nextResponse=()=>new Response('404',{status:404});await byText('读取提示词库').emit('click');assert(pc().disabled);await byText('关闭').emit('click');
assert(promptCalls.every(c=>['/workflow-architect/prompts/search','/workflow-architect/prompts/render'].includes(c.path)));
console.log('10 prompt-library mock-DOM scenarios passed; explicit read, search, fill, export, stale/closed isolation, invalid input and missing backend; no execution');
}

{
let localCalls=[],compiledLocal=null;
globalThis.__offlineTestCompile=initial=>{compiledLocal=initial;};
globalThis.__knowledgeTestApi.fetchApi=async(path,options)=>{
  localCalls.push({path,body:JSON.parse(options.body)});
  if(nextResponse){const fn=nextResponse;nextResponse=null;return await fn();}
  const data=path.endsWith('/status')?{ready:true,execution_authorized:false,external_api_called:false}:
    {status:'grounded-local-plan-not-executed',accepted:{proposal:{template_id:'owned-template'}},execution_authorized:false,external_api_called:false};
  return new Response(JSON.stringify({ok:true,data}),{headers:{'Content-Type':'application/json'}});
};
const source=(await readFile(new URL('../web/js/offline_ai_panel.js',import.meta.url),'utf8'))
  .replace("import { api } from '../../../scripts/api.js';",'const api=globalThis.__knowledgeTestApi;')
  .replace("import { openCompilePanel } from './compile_panel.js';",'const openCompilePanel=globalThis.__offlineTestCompile;');
const {openOfflineAiPanel}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
openOfflineAiPanel();assert.equal(localCalls.length,0);
await byText('检查本地模型').emit('click');assert.equal(localCalls.length,1);
const ask=()=>byText('单次离线推理（不生图）'),next=()=>byText('交给原编译器绑定图片与模型');
await ask().emit('click');assert.equal(localCalls.length,1);
byLabel('离线助手需求').value='参考图换装';byLabel('离线助手模式').value='plan';
const consent=all().find(e=>e.tag==='input'&&e.type==='checkbox');consent.checked=true;
await ask().emit('click');assert.equal(localCalls.length,2);assert.equal(localCalls[1].body.confirmed,true);assert(!next().disabled);
await next().emit('click');assert.equal(compiledLocal.text,'参考图换装');
await byLabel('离线助手需求').emit('input');assert(next().disabled);
let resolveLate;nextResponse=()=>new Promise(resolve=>{resolveLate=resolve;});const pending=ask().emit('click');
await byLabel('离线助手需求').emit('input');resolveLate(new Response(JSON.stringify({ok:true,data:{execution_authorized:false,external_api_called:false,status:'grounded-local-plan-not-executed',accepted:{proposal:{}}}}),{headers:{'Content-Type':'application/json'}}));await pending;assert(next().disabled);
nextResponse=()=>new Response('405',{status:405});await byText('检查本地模型').emit('click');assert(next().disabled);
byLabel('离线助手模式').value='knowledge';
nextResponse=()=>new Response(JSON.stringify({ok:true,data:{status:'local-answer-not-executed',execution_authorized:false,external_api_called:false,answer:{answer:'invented contrast advice'}}}),{headers:{'Content-Type':'application/json'}});
await ask().emit('click');assert(next().disabled);assert(all().some(e=>e.textContent.includes('后端仍为旧版自由回答')));
nextResponse=()=>new Response(JSON.stringify({ok:true,data:{status:'local-answer-not-executed',answer_kind:'verified-source-excerpts',execution_authorized:false,external_api_called:false,answer:{answer:'原文证据'}}}),{headers:{'Content-Type':'application/json'}});
await ask().emit('click');assert(next().disabled);assert(all().some(e=>e.tag==='pre'&&e.textContent.includes('原文证据')));
await byText('关闭').emit('click');assert(!document.querySelector('.wa-offline-overlay'));
assert(localCalls.every(c=>['/workflow-architect/offline/status','/workflow-architect/offline/assist'].includes(c.path)));
console.log('10 offline-AI mock-DOM scenarios passed; explicit consent, compiler handoff, stale isolation, missing backend and verified-excerpt contract; no real model or queue');
}
