"""Self-contained private annotation UI: no remote assets, inference or uploads."""

import base64
import hashlib
import json

STYLE = """
body{font:16px system-ui,sans-serif;margin:0;background:#f4f6f3;color:#26382c}
main{max-width:1100px;margin:auto;padding:24px}h1{font-size:26px}
.layout{display:grid;grid-template-columns:minmax(260px,1fr) minmax(300px,1fr);gap:24px}
section,fieldset{background:white;border:1px solid #d4ddd5;border-radius:10px;padding:18px;margin:0 0 18px}
img{width:100%;max-height:680px;object-fit:contain;background:#e9ede8}
label{display:block;margin:12px 0}input[type=text],select{display:block;padding:8px;box-sizing:border-box;width:100%;margin-top:5px}
button{padding:12px 18px;background:#356448;color:white;border:0;border-radius:6px;font:inherit;cursor:pointer}
button:disabled{background:#9ca89f;cursor:default}small,p{line-height:1.6}.scope{overflow-wrap:anywhere}
@media(max-width:760px){.layout{display:block}main{padding:12px}}
"""

SCRIPT = """
'use strict';
const initial=JSON.parse(document.getElementById('dataset').textContent);
const form=document.getElementById('labels');
const shared=['visible_text','on_product_logo','added_watermark','dark_background'];
const names={visible_text:'存在可见文字',on_product_logo:'存在产品本体标识',added_watermark:'存在附加水印',dark_background:'属于深色背景'};
function select(parent,name,title,options){
 const label=document.createElement('label');label.textContent=title;
 const node=document.createElement('select');node.name=name;node.required=true;node.setAttribute('data-testid',name);
 for(const [value,text] of [['','请选择，不能沿用自动结论'],...options]){
  const option=document.createElement('option');option.value=value;option.textContent=text;node.append(option);
 }
 label.append(node);parent.append(label);return node;
}
const yesNo=[['true','是'],['false','否']];
const physical=document.getElementById('physical');
for(const feature of shared)select(physical,feature,names[feature],yesNo);
const scopes=document.getElementById('scopes');
initial.samples.forEach((sample,index)=>{
 const field=document.createElement('fieldset');const legend=document.createElement('legend');
 legend.className='scope';legend.textContent=[sample.platform,sample.marketplace,sample.category,sample.image_type,sample.rule_version].join(' · ');field.append(legend);
 select(field,'truth-'+index,'该范围的人工结论',[['compliant','合规'],['non_compliant','违规']]);
 select(field,'exception-'+index,'该类目是否存在适用例外',yesNo);scopes.append(field);
});
const save=document.getElementById('save');
function valid(){
 return /^[a-f0-9]{64}$/.test(form.elements.namedItem('annotator').value)
 &&form.elements.namedItem('reviewed').checked&&form.elements.namedItem('rights').checked
 &&[...form.querySelectorAll('select')].every(node=>node.value!=='');
}
function refresh(){save.disabled=!valid();}
form.addEventListener('input',refresh);form.addEventListener('change',refresh);
form.addEventListener('submit',event=>{
 event.preventDefault();if(!valid())return;
 const output=structuredClone(initial);
 output.human_reviewed_attested=true;output.usage_rights_attested=true;
 output.samples.forEach((sample,index)=>{
  sample.human_label=form.elements.namedItem('truth-'+index).value;
  sample.annotator_fingerprint=form.elements.namedItem('annotator').value;
  for(const feature of shared)sample.features[feature]=form.elements.namedItem(feature).value==='true';
  sample.features.category_exception=form.elements.namedItem('exception-'+index).value==='true';
 });
 const url=URL.createObjectURL(new Blob([JSON.stringify(output,null,2)+'\\n'],{type:'application/json'}));
 const link=document.createElement('a');link.href=url;link.download='manifest.reviewed.json';link.click();
 setTimeout(()=>URL.revokeObjectURL(url),30000);
 document.getElementById('status').textContent='清单已生成。请把下载文件放回本标注包目录，再运行评测；原图和报告保持不变。';
});
refresh();
"""


def digest(raw):
    return base64.b64encode(hashlib.sha256(raw.encode()).digest()).decode()


def annotation_document(manifest):
    # Escape HTML delimiters even inside inert JSON, since source scope strings are untrusted.
    data = (
        json.dumps(manifest, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    policy = (
        "default-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; "
        "img-src 'self' file: data:; "
        f"script-src 'sha256-{digest(SCRIPT)}'; style-src 'sha256-{digest(STYLE)}'"
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{policy}">
<title>私有图片人工标注</title><style>{STYLE}</style></head>
<body><main><h1>私有图片人工标注</h1>
<p>本页面仅在本地填写，不联网、不上传图片。请依据原图与适用的站点/类目规则独立判断；无法判断时先复核，不强行填写。页面不展示自动结论。</p>
<div class="layout"><section><img src="final.png" alt="待人工核对的最终图片"><p>请保留 final.png、report.json 原文件与摘要。</p></section>
<form id="labels"><section><label>标注者指纹（64位小写SHA256）<input name="annotator" type="text" required pattern="[a-f0-9]{{64}}" maxlength="64" autocomplete="off"></label>
<fieldset id="physical"><legend>图片物理特征（所有平台共用）</legend></fieldset></section>
<div id="scopes"></div><section>
<label><input name="reviewed" type="checkbox">我已独立完成图片及对应规则范围的人工审核</label>
<label><input name="rights" type="checkbox">我已确认本次评测所需的图片使用权属</label>
<button id="save" type="submit" disabled>下载已标注清单</button>
<p id="status" role="status">标签与声明尚未填写完整。标注不代表供应商验收或发布许可。</p>
<small>下载后将 manifest.reviewed.json 放到本包内，运行 evaluate_quality.py。源文件、规则范围与摘要不应改写。</small>
</section></form></div>
<script id="dataset" type="application/json">{data}</script><script>{SCRIPT}</script>
</main></body></html>"""
