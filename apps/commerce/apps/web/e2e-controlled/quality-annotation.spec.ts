import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {copyFileSync,mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,randomUUID} from 'node:crypto';
import path from 'node:path';

const root=fileURLToPath(new URL('../../../',import.meta.url));
const py=path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');

test('private local annotation requires independent labels and exports an unchanged scope',async({page,browser})=>{
 const folder=path.join(root,'.local/annotation-browser',randomUUID());mkdirSync(folder,{recursive:true});
 const fixture=fileURLToPath(new URL('../e2e/fixtures/product.png',import.meta.url));copyFileSync(fixture,path.join(folder,'final.png'));
 const hostile='</script><script>document.body.dataset.pwned="yes"</script>';
 const initial={schema_version:1,dataset_kind:'synthetic_fixture',human_reviewed_attested:false,usage_rights_attested:false,samples:[{
  sample_id:'synthetic',image_file:'final.png',image_sha256:createHash('sha256').update(readFileSync(fixture)).digest('hex'),report_file:'report.json',report_sha256:'b'.repeat(64),
  platform:'amazon',marketplace:'US',category:hostile,image_type:'MAIN',rule_version:'fixture',human_label:null,annotator_fingerprint:null,
  features:{visible_text:null,on_product_logo:null,added_watermark:null,dark_background:null,category_exception:null}
 }]};
 const manifest=path.join(folder,'manifest.json');writeFileSync(manifest,JSON.stringify(initial));
 execFileSync(py,['-c',"import json,sys; from pathlib import Path; from visual_platform.services.quality_annotation import annotation_document; p=Path(sys.argv[1]); p.with_name('annotate.html').write_text(annotation_document(json.loads(p.read_text())),encoding='utf-8')",manifest],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')}});
 const network:string[]=[];page.on('request',request=>{if(/^https?:/.test(request.url()))network.push(request.url())});
 await page.goto(pathToFileURL(path.join(folder,'annotate.html')).href);
 await expect(page.getByRole('heading',{name:'私有图片人工标注'})).toBeVisible();
 await expect(page.locator('legend.scope')).toContainText(hostile);
 expect(await page.locator('body').getAttribute('data-pwned')).toBeNull();
 const save=page.getByRole('button',{name:'下载已标注清单'});await expect(save).toBeDisabled();
 await page.getByLabel('标注者指纹（64位小写SHA256）').fill('a'.repeat(64));
 for(const feature of ['visible_text','on_product_logo','added_watermark','dark_background'])await page.getByTestId(feature).selectOption('false');
 await page.getByTestId('truth-0').selectOption('non_compliant');await page.getByTestId('exception-0').selectOption('true');
 await expect(save).toBeDisabled();await page.getByLabel('我已独立完成图片及对应规则范围的人工审核').check();await expect(save).toBeDisabled();
 await page.getByLabel('我已确认本次评测所需的图片使用权属').check();await expect(save).toBeEnabled();
 const downloaded=page.waitForEvent('download');await save.click();const download=await downloaded;
 expect(download.suggestedFilename()).toBe('manifest.reviewed.json');const file=await download.path();expect(file).toBeTruthy();
 const reviewed=JSON.parse(readFileSync(file!,'utf8'));expect(reviewed.human_reviewed_attested).toBe(true);expect(reviewed.usage_rights_attested).toBe(true);
 const {human_label,annotator_fingerprint,features,...scope}=reviewed.samples[0];
 const {human_label:oldLabel,annotator_fingerprint:oldAnnotator,features:oldFeatures,...expectedScope}=initial.samples[0];
 expect(scope).toEqual(expectedScope);expect(human_label).toBe('non_compliant');expect(annotator_fingerprint).toBe('a'.repeat(64));expect(features).toEqual({visible_text:false,on_product_logo:false,added_watermark:false,dark_background:false,category_exception:true});
 expect(network).toEqual([]);expect(JSON.parse(readFileSync(manifest,'utf8'))).toEqual(initial);
 await expect(page.getByRole('status')).toContainText('请把下载文件放回本标注包目录');
 mkdirSync(path.join(root,'work/ci-public'),{recursive:true});writeFileSync(path.join(root,'work/ci-public/frontend-quality-annotation.json'),JSON.stringify({
  result:'PASS',commit:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),observed_at:new Date().toISOString(),paid_calls:0,browser:browser.version(),
  scope:'synthetic local-file annotation UI only; native PG/S3 export separately tested',blank_labels_required:true,both_attestations_required:true,scope_and_hashes_preserved:true,no_external_requests:true,untrusted_scope_rendered_as_text:true,original_manifest_unchanged:true,
  test_sha256:createHash('sha256').update(readFileSync(fileURLToPath(import.meta.url))).digest('hex'),generator_sha256:createHash('sha256').update(readFileSync(path.join(root,'src/visual_platform/services/quality_annotation.py'))).digest('hex')
 },null,2)+'\n');
});
