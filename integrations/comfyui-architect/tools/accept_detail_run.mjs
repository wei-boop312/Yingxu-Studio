// Explicit six owned GPU tasks through the same serial runner as the panel.
import {readFile,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {join} from 'node:path';
const directory=process.argv[2];if(!directory||process.argv[3]!=='--run-six-owned-prompts')throw Error('Explicit owned six-prompt flag required');
const source=await readFile(new URL('../web/js/detail_runner.js',import.meta.url),'utf8');
const {runDetailJobs,validImage}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const bundle=JSON.parse(await readFile(join(directory,'bundle.json'),'utf8'));
const base='http://127.0.0.1:8188';
async function request(path,body){const r=await fetch(base+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(30000)});if(!r.ok)throw Error('Local HTTP '+r.status);return r.json();}
const queue=await request('/queue');if(queue.queue_running.length||queue.queue_pending.length)throw Error('User queue not idle');
try{await readFile(join(directory,'journal.json'));throw Error('Do not overwrite existing journal');}catch(e){if(e.code!=='ENOENT')throw e;}
const journal={version:1,id:randomUUID(),bundle_sha256:bundle.bundle_sha256,entries:bundle.jobs.map(j=>({id:j.id,status:'ready'}))};
// Runner save is synchronous by design: durable readback before each POST.
const fs=await import('node:fs');
function save(value){const raw=JSON.stringify(value,null,2);fs.writeFileSync(join(directory,'journal.json'),raw,{encoding:'utf8',flush:true});if(fs.readFileSync(join(directory,'journal.json'),'utf8')!==raw)throw Error('Journal mismatch');}
await runDetailJobs({jobs:bundle.jobs,journal,save,submit:(prompt,client_id)=>request('/prompt',{prompt,client_id}),
  history:async id=>{const result=(await request('/history/'+id))[id];if(result)await writeFile(join(directory,id+'-history.json'),JSON.stringify(result,null,2));return result;},
  wait:()=>new Promise(r=>setTimeout(r,2000)),stopped:()=>false,changed:()=>console.log(journal.entries.map(e=>e.id+':'+e.status).join(' '))});
for(const entry of journal.entries){if(!validImage(entry.image))throw Error('Output identity invalid');const response=await fetch(base+'/view?'+new URLSearchParams(entry.image),{signal:AbortSignal.timeout(30000)});if(!response.ok)throw Error('Output read failed');await writeFile(join(directory,entry.id+'-output.png'),Buffer.from(await response.arrayBuffer()));}
console.log('Six owned local executions completed; no external model API or commercial quality approval.');
