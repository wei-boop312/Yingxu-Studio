import {test,expect,type Page} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const py=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const hash=(bytes:Buffer)=>createHash('sha256').update(bytes).digest('hex');
function distribution(values:number[]){const sorted=[...values].sort((a,b)=>a-b);const p=(fraction:number)=>sorted[Math.min(sorted.length-1,Math.ceil(sorted.length*fraction)-1)]??null;return {count:values.length,min_ms:sorted[0]??null,p50_ms:p(.5),p95_ms:p(.95),p99_ms:p(.99),max_ms:sorted.at(-1)??null}}
async function nodePoint(page:Page,view:'source'|'target',kind:'image'|'rect',x:number,y:number){return page.evaluate(({view,kind,x,y})=>{const K=(window as any).Konva;const stage=K.stages.find((s:any)=>s.container().closest('[data-testid]')?.dataset.testid==='canvas-'+view);const node=stage.findOne(kind==='image'?'Image':'Rect');const p=node.getAbsoluteTransform().point({x,y});const box=stage.container().getBoundingClientRect();return {x:box.x+p.x,y:box.y+p.y}}, {view,kind,x,y})}

test('生产构建：4MP、50绘制对象、60秒真实指针及20并发500次非模型API',async({page,browser,context})=>{
 test.setTimeout(180000);await page.setViewportSize({width:1920,height:1200});
 const startedAt=new Date().toISOString();const html=await(await page.request.get('/')).text();expect(html).toContain('/assets/');expect(html).not.toContain('/@vite/client');
 const provision=JSON.parse(execFileSync(py,['scripts/create_e2e_account.py'],{cwd:root,encoding:'utf8'}));const a=JSON.parse(readFileSync(provision.account_file,'utf8'));
 const errors:string[]=[];let taskPosts=0;const remoteHosts=new Set<string>();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/v1/tasks'))taskPosts++;const host=new URL(r.url()).hostname;if(!['127.0.0.1','localhost'].includes(host))remoteHosts.add(host)});
 await page.goto('/');await page.getByLabel('邮箱',{exact:true}).fill(a.email);await page.getByLabel('密码',{exact:true}).fill(a.password);await page.getByRole('button',{name:'进入工作台'}).click();await expect(page.getByRole('heading',{name:'视觉工作台',exact:true})).toBeVisible();
 const csrf=(await context.cookies()).find(c=>c.name==='visual_csrf')!.value;const headers={'Origin':'http://127.0.0.1:5173','X-CSRF-Token':csrf};
 const productResponse=await page.request.post('/api/v1/products',{headers,data:{name:'4MP 画布性能验收',category:'performance-synthetic'}});expect(productResponse.status()).toBe(201);const product=await productResponse.json();
 execFileSync(py,['scripts/performance_fixture.py'],{cwd:root});const bytes=readFileSync(path.join(root,'work/performance/4mp.png'));
 const intent=await page.request.post('/api/v1/assets/upload-intents',{headers,data:{product_id:product.id,filename:'4mp.png',mime:'image/png',size:bytes.length}});expect(intent.status()).toBe(201);const u=await intent.json();
 expect((await page.request.put(u.upload_url,{data:bytes,headers:u.headers})).status()).toBe(200);const complete=await page.request.post('/api/v1/assets/upload-intents/'+u.intent_id+'/complete',{headers});expect(complete.status()).toBe(200);const canonical=await complete.json();
 expect(canonical.width*canonical.height).toBe(4000000);await page.reload();await expect(page.getByTestId('canvas-source').locator('canvas')).toBeVisible();await expect(page.getByTestId('canvas-target').locator('canvas')).toBeVisible();
 // Use ordinary controls and real mouse events; Konva is inspected read-only for geometry/counts.
 const sourcePanel=page.getByTestId('canvas-source').locator('..');
 await sourcePanel.getByRole('button',{name:'放大',exact:true}).click();await sourcePanel.getByText('平移',{exact:true}).click();
 let p=await nodePoint(page,'source','image',1700,1700);await page.mouse.move(p.x,p.y);await page.mouse.down();await page.mouse.move(p.x+18,p.y+8,{steps:12});await page.mouse.up();
 await sourcePanel.getByText('选区',{exact:true}).click();
 const intended:Array<{x:number,y:number,width:number,height:number}>=[];
 for(let row=0;row<3;row++)for(let col=0;col<4;col++){
  const r={x:160+col*400,y:150+row*480,width:160,height:160};intended.push(r);
  const start=await nodePoint(page,'source','image',r.x,r.y),end=await nodePoint(page,'source','image',r.x+r.width,r.y+r.height);
  await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:6});await page.mouse.up();
 }
 await expect(page.getByText('12 个保护区域',{exact:true})).toBeVisible();
 const scene=await page.evaluate(()=>{const K=(window as any).Konva;return K.stages.map((s:any)=>({view:s.container().closest('[data-testid]')?.dataset.testid,scale:s.findOne('Image').getAbsoluteScale().x,shapes:s.find((n:any)=>n.getType()==='Shape').map((n:any)=>({kind:n.getClassName(),x:n.x(),y:n.y(),width:n.width(),height:n.height()}))}))});
 expect(scene).toHaveLength(2);expect(scene.reduce((sum:number,s:any)=>sum+s.shapes.length,0)).toBe(50);
 const sourceScene=scene.find((s:any)=>s.view==='canvas-source');const originalRects=sourceScene.shapes.filter((n:any)=>n.kind==='Rect');expect(originalRects).toHaveLength(12);
 for(let i=0;i<12;i++){const actual=originalRects[i],wanted=intended[i],tolerance=Math.ceil(1/sourceScene.scale);expect(Math.abs(actual.x-wanted.x)).toBeLessThanOrEqual(tolerance);expect(Math.abs(actual.y-wanted.y)).toBeLessThanOrEqual(tolerance);expect(Math.abs(actual.width-wanted.width)).toBeLessThanOrEqual(2*tolerance);expect(Math.abs(actual.height-wanted.height)).toBeLessThanOrEqual(2*tolerance)}
 await sourcePanel.getByRole('button',{name:'适应画布',exact:true}).click();
 // API probe: retain every response and failure in the percentile population.
 const routes=[['products','/products'],['tasks','/tasks'],['balance','/credits/balance'],['rules','/rules/amazon'],['me','/auth/me'],['product','/products/'+product.id]];
 const samples:Array<{index:number,route:string,status:number|null,duration_ms:number,bytes:number,request_id:string|null,error?:string}>=[];let next=0;
 const apiStarted=performance.now();await Promise.all(Array.from({length:20},async()=>{while(true){const index=next++;if(index>=500)return;const [name,route]=routes[index%routes.length];const begin=performance.now();try{const response=await page.request.get('/api/v1'+route,{timeout:10000});const body=await response.body();samples.push({index,route:name,status:response.status(),duration_ms:performance.now()-begin,bytes:body.length,request_id:response.headers()['x-request-id']||null});await response.dispose()}catch{samples.push({index,route:name,status:null,duration_ms:performance.now()-begin,bytes:0,request_id:null,error:'request_failed'})}}}));
 const apiElapsed=performance.now()-apiStarted;
 const cdp=await context.newCDPSession(page);await cdp.send('Performance.enable');const memoryBefore=(await cdp.send('Performance.getMetrics')).metrics.filter((m:any)=>['JSHeapUsedSize','JSHeapTotalSize','Nodes','Documents','LayoutObjects'].includes(m.name));
 // RAF timestamps measure cadence/stalls, not physical display presentation.
 await page.evaluate(()=>{const w=window as any;const K=w.Konva;const perf=w.__canvasPerf={start:performance.now(),last:0,intervals:[],draw:[],events:[],longTasks:[],drag:{},scale:{min:Infinity,max:0},running:true};
  const frame=(time:number)=>{if(!perf.running)return;if(perf.last)perf.intervals.push(time-perf.last);perf.last=time;perf.raf=requestAnimationFrame(frame)};perf.raf=requestAnimationFrame(frame);
  for(const stage of K.stages)for(const layer of stage.getLayers()){let start=0;layer.on('beforeDraw.perf',()=>{start=performance.now()});layer.on('draw.perf',()=>{perf.draw.push(performance.now()-start);const scale=stage.findOne('Image').getAbsoluteScale().x;perf.scale.min=Math.min(perf.scale.min,scale);perf.scale.max=Math.max(perf.scale.max,scale)})}
  for(const stage of K.stages){const starts=new Map();stage.on('dragstart.perf dragmove.perf dragend.perf',(e:any)=>{const node=e.target;const kind=node.getClassName();const d=perf.drag[kind]??={starts:0,moves:0,ends:0,max_displacement:0};if(e.type==='dragstart'){starts.set(node,{x:node.x(),y:node.y()});d.starts++}else if(e.type==='dragmove'){d.moves++;const first=starts.get(node) as any;if(first)d.max_displacement=Math.max(d.max_displacement,Math.hypot(node.x()-first.x,node.y()-first.y))}else d.ends++})}
  perf.listener=(e:Event)=>{if((e.target as Element).closest?.('.canvas-host'))perf.events.push({type:e.type,time:performance.now()-perf.start})};for(const name of ['pointermove','pointerdown','pointerup','wheel'])document.addEventListener(name,perf.listener,true);
  perf.observer=new PerformanceObserver(list=>perf.longTasks.push(...list.getEntries().map(e=>({start_ms:e.startTime-perf.start,duration_ms:e.duration}))));perf.observer.observe({type:'longtask',buffered:false});
 });
 const interactionStart=performance.now();let iterations=0;let zoomGestures=0,dragGestures=0;
 while(performance.now()-interactionStart<60000){
  if(iterations%3===0){
   p=await nodePoint(page,'source','image',1000,900);await page.mouse.move(p.x,p.y);
   for(let i=0;i<5;i++){await page.mouse.wheel(0,-60);await page.mouse.move(p.x+i,p.y+i)}
   for(let i=0;i<5;i++){await page.mouse.wheel(0,60);await page.mouse.move(p.x-i,p.y-i)}
   await sourcePanel.getByRole('button',{name:'适应画布',exact:true}).click();zoomGestures+=10;
  }else if(iterations%3===1){
   const before=await nodePoint(page,'target','rect',70,70);await page.mouse.move(before.x,before.y);await page.mouse.down();await page.mouse.move(before.x+14,before.y+14,{steps:28});await page.mouse.move(before.x,before.y,{steps:28});await page.mouse.up();dragGestures++;
  }else{
   await sourcePanel.getByText('平移',{exact:true}).click();p=await nodePoint(page,'source','image',1700,1700);await page.mouse.move(p.x,p.y);await page.mouse.down();await page.mouse.move(p.x+22,p.y+12,{steps:28});await page.mouse.move(p.x,p.y,{steps:28});await page.mouse.up();await sourcePanel.getByText('选区',{exact:true}).click();dragGestures++;
  }
  iterations++;
 }
 const perf=await page.evaluate(()=>{const w=window as any;const p=w.__canvasPerf;p.running=false;cancelAnimationFrame(p.raf);p.duration=performance.now()-p.start;p.observer.disconnect();for(const name of ['pointermove','pointerdown','pointerup','wheel'])document.removeEventListener(name,p.listener,true);for(const s of w.Konva.stages){s.off('.perf');for(const l of s.getLayers())l.off('.perf')}return {duration_ms:p.duration,intervals:p.intervals,draw:p.draw,events:p.events,long_tasks:p.longTasks,drag:p.drag,scale:p.scale,scene:w.Konva.stages.map((s:any)=>({view:s.container().closest('[data-testid]')?.dataset.testid,shapes:s.find((n:any)=>n.getType()==='Shape').map((n:any)=>({kind:n.getClassName(),x:n.x(),y:n.y(),width:n.width(),height:n.height()}))})),device:{user_agent:navigator.userAgent,hardware_concurrency:navigator.hardwareConcurrency,device_pixel_ratio:devicePixelRatio,viewport:{width:innerWidth,height:innerHeight}}}});
 const memoryAfter=(await cdp.send('Performance.getMetrics')).metrics.filter((m:any)=>['JSHeapUsedSize','JSHeapTotalSize','Nodes','Documents','LayoutObjects'].includes(m.name));
 const targetAfter=perf.scene.find((s:any)=>s.view==='canvas-target').shapes.filter((n:any)=>n.kind==='Rect');const sourceAfter=perf.scene.find((s:any)=>s.view==='canvas-source').shapes.filter((n:any)=>n.kind==='Rect');
 expect(sourceAfter).toEqual(originalRects);for(let i=0;i<12;i++){expect(targetAfter[i].width).toBe(originalRects[i].width);expect(targetAfter[i].height).toBe(originalRects[i].height);expect(Number.isInteger(targetAfter[i].x)&&Number.isInteger(targetAfter[i].y)).toBeTruthy();expect(targetAfter[i].x).toBeGreaterThanOrEqual(0);expect(targetAfter[i].y).toBeGreaterThanOrEqual(0)}
 const tasks=await(await page.request.get('/api/v1/tasks')).json();
 const frameStats=distribution(perf.intervals);const apiStats=distribution(samples.map(s=>s.duration_ms));
 const gaps=perf.events.slice(1).map((e:any,i:number)=>e.time-perf.events[i].time);const statusCounts:Record<string,number>={};for(const s of samples)statusCounts[String(s.status)]=(statusCounts[String(s.status)]||0)+1;
 const buildFiles=readdirSync(path.join(root,'apps/web/dist/assets')).filter(x=>x.endsWith('.js')).map(name=>({name,sha256:hash(readFileSync(path.join(root,'apps/web/dist/assets',name)))}));
 const evidence={started_at:startedAt,finished_at:new Date().toISOString(),base_commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),production_build:{assets:buildFiles,html_sha256:hash(Buffer.from(html))},environment:{platform:os.platform(),release:os.release(),arch:os.arch(),cpu:os.cpus()[0]?.model,logical_cpu_count:os.cpus().length,total_memory_bytes:os.totalmem(),browser:browser.version(),...perf.device},fixture:{width:2000,height:2000,source_sha256:hash(bytes),canonical_sha256:canonical.sha256,description:'synthetic 4MP image, not real product quality evaluation'},scene:{drawable_objects:50,definition:'2 images + 24 ROI rectangles + 24 labels across two stages; groups/layers/stages excluded',initial:scene,final:perf.scene},canvas:{duration_ms:perf.duration_ms,iterations,zoom_gestures:zoomGestures,drag_gestures:dragGestures,frame_interval:frameStats,intervals_ms:perf.intervals,draw_cpu:distribution(perf.draw),draw_cpu_ms:perf.draw,long_tasks:perf.long_tasks,observed_drag:perf.drag,observed_scale:perf.scale,event_count:perf.events.length,max_input_gap_ms:Math.max(...gaps),event_types:perf.events.reduce((a:any,e:any)=>(a[e.type]=(a[e.type]||0)+1,a),{}),memory_before:memoryBefore,memory_after:memoryAfter,measurement:'requestAnimationFrame cadence; Layer beforeDraw-to-draw CPU time; CDP heap metrics. Not physical display/GPU presentation or total process RSS.'},api:{concurrency:20,requested:500,elapsed_ms:apiElapsed,latency:apiStats,status_counts:statusCounts,samples:samples.sort((a,b)=>a.index-b.index),includes_failures_in_percentiles:true},safety:{task_posts:taskPosts,user_tasks_after:tasks.items.length,remote_hosts:[...remoteHosts],page_errors:errors},targets:{canvas_p95_ms:33,api_p95_ms:500,max_input_gap_ms:1000},passed:perf.duration_ms>=60000&&perf.events.length>600&&perf.draw.length>300&&Math.max(...gaps)<1000&&perf.drag.Rect?.max_displacement>5&&perf.drag.Group?.max_displacement>5&&perf.scale.max>perf.scale.min*1.1&&perf.scene.reduce((n:number,s:any)=>n+s.shapes.length,0)===50&&frameStats.p95_ms!<33&&apiStats.p95_ms!<500&&samples.length===500&&samples.every(s=>s.status===200)&&taskPosts===0&&tasks.items.length===0&&errors.length===0&&remoteHosts.size===0};
 writeFileSync(path.join(root,'docs/evidence/performance-production.json'),JSON.stringify(evidence,null,2));await page.screenshot({path:path.join(root,'docs/evidence/browser-performance-50-objects.png')});
 console.log(JSON.stringify({canvas_frame_p95_ms:frameStats.p95_ms,canvas_duration_ms:perf.duration_ms,api_p95_ms:apiStats.p95_ms,status_counts:statusCounts,passed:evidence.passed}));
 expect(perf.duration_ms).toBeGreaterThanOrEqual(60000);expect(perf.events.length).toBeGreaterThan(600);expect(perf.draw.length).toBeGreaterThan(300);expect(zoomGestures).toBeGreaterThan(0);expect(dragGestures).toBeGreaterThan(0);expect(perf.drag.Rect.max_displacement).toBeGreaterThan(5);expect(perf.drag.Group.max_displacement).toBeGreaterThan(5);expect(evidence.passed).toBeTruthy();
});
