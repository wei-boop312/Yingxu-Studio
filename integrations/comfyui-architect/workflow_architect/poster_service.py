"""Local image-only preparation and native Comfy compositor export. No queue/API."""
import hashlib
import copy
import io
import json
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile
from PIL import Image
from .brand_poster import create_overlay,compose,validate_style,bounded
from .errors import InputError

SLOT=threading.BoundedSemaphore(1)
ROLES={'scene','table','product','product_mask','remote','remote_mask'}

def resolve(ref,folders):
    if not isinstance(ref,dict) or set(ref)!={'filename','subfolder','type'} or ref['type'] not in ('input','output'):raise InputError('素材引用结构不合法')
    name=ref['filename'];sub=ref['subfolder']
    if not isinstance(name,str) or not re.fullmatch(r'[^/\\:\x00-\x1f]{1,180}\.png',name,re.I) or not isinstance(sub,str) or len(sub)>180 or re.search(r'[\\:\x00-\x1f]',sub) or sub.startswith('/') or '..' in sub.split('/'):raise InputError('仅接受Comfy本机PNG素材，不接受地址或任意路径')
    root=Path(folders.get_input_directory() if ref['type']=='input' else folders.get_output_directory()).resolve()
    path=root/sub/name
    for parent in [path,*path.parents]:
        if parent==root:break
        if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()):raise InputError('素材不允许链接')
    if not path.resolve().is_relative_to(root) or not path.is_file():raise InputError('素材不存在或超出本机媒体目录')
    return path

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            chunk=stream.read(65536)
            if not chunk:break
            h.update(chunk)
    return h.hexdigest()

def check_budget(folders,additional_bytes=0,additional_files=0):
    files=[]
    for directory,pattern in [(Path(folders.get_input_directory()),'wa_brand_*.png'),(Path(folders.get_output_directory())/'workflow_architect','brand_poster_*')]:
        if directory.exists():files.extend(p for p in directory.glob(pattern) if p.is_file())
    if len(files)+additional_files>256 or sum(p.stat().st_size for p in files)+additional_bytes>512*1024*1024:raise InputError('品牌海报自有副本容量已满，不删除历史腾空间')

def native_template(base,overlay,prefix):
    template={'kind':'concrete','template_id':'brand_poster_native_composite_v1','task':'image_resize','profiles':['stable'],'verified':True,'nodes':[
        {'key':'base','class_type':'LoadImage','title':'已核验场景与桌面底图','inputs':{'image':base}},
        {'key':'overlay','class_type':'LoadImage','title':'真实商品与确定性英文叠加层','inputs':{'image':overlay}},
        {'key':'mask','class_type':'InvertMask','title':'还原叠加层前景Alpha','inputs':{'mask':{'link':['overlay',1]}}},
        {'key':'composite','class_type':'ImageCompositeMasked','title':'原生Alpha合成（不重新生成商品）','inputs':{'destination':{'link':['base',0]},'source':{'link':['overlay',0]},'x':0,'y':0,'resize_source':False,'mask':{'link':['mask',0]}}},
        {'key':'save','class_type':'SaveImage','title':'保存1600品牌海报','inputs':{'images':{'link':['composite',0]},'filename_prefix':prefix}}]}
    for node,position in zip(template['nodes'],[(50,80),(50,500),(420,500),(760,200),(1130,200)]):node['position']=position;node['stage']='output' if node['key']=='save' else 'preprocessing'
    return template

def prepare(service,payload,cancel=None,folders=None):
    payload=copy.deepcopy(payload)
    if not isinstance(payload,dict) or set(payload)!={'assets','style','confirmed'} or payload['confirmed'] is not True:raise InputError('须确认素材权利、实际文案与本机写入，不自动执行')
    if not isinstance(payload['assets'],dict) or set(payload['assets'])!=ROLES:raise InputError('须完整六个素材角色')
    validate_style(payload['style'])
    if not SLOT.acquire(blocking=False):raise InputError('已有海报准备尚未结束')
    start=time.monotonic()
    def current():
        if (cancel is not None and cancel.is_set()) or time.monotonic()-start>30:raise InputError('海报准备取消或到期，不采用迟到结果')
    try:
        if folders is None:
            import folder_paths as folders
        check_budget(folders);current()
        paths={key:resolve(value,folders) for key,value in payload['assets'].items()};encoded=0;pixels=0;loaded={};hashes={}
        # All header/declared size checks before full raster decode.
        for key,path in paths.items():
            size=path.stat().st_size;encoded+=size
            if size>30*1024*1024 or encoded>64*1024*1024:raise InputError('海报PNG单份30MiB/合计64MiB超限')
            with Image.open(path) as image:
                if image.format!='PNG':raise InputError('素材真实格式不是PNG')
                bounded(image);pixels+=image.width*image.height
            if pixels>48000000:raise InputError('合计解码像素超限')
        for key,path in paths.items():
            current();hashes[key]=digest(path)
            with Image.open(path) as image:image.load();loaded[key]=image.copy()
        current();overlay,report=create_overlay(loaded['product'],loaded['product_mask'],loaded['remote'],loaded['remote_mask'],payload['style'],Path('C:/Windows/Fonts/arialbd.ttf'))
        base=compose(loaded['scene'],Image.new('RGBA',(1600,1600)),loaded['table']);preview=Image.alpha_composite(base.convert('RGBA'),overlay).convert('RGB')
        encoded_images=[]
        for image in (base,overlay,preview):
            stream=io.BytesIO();image.save(stream,format='PNG');encoded_images.append(stream.getvalue())
        # Include worst-case near-uncompressed ZIP payload plus bounded metadata.
        check_budget(folders,2*(len(encoded_images[0])+len(encoded_images[1]))+len(encoded_images[2])+2*1024*1024,4)
        for key,path in paths.items():
            if digest(path)!=hashes[key]:raise InputError('素材在准备中发生变化')
        current();ident=uuid.uuid4().hex
        inputroot=Path(folders.get_input_directory());outroot=Path(folders.get_output_directory())/'workflow_architect';outroot.mkdir(exist_ok=True)
        if not outroot.resolve().is_relative_to(Path(folders.get_output_directory()).resolve()) or outroot.is_symlink() or (hasattr(outroot,'is_junction') and outroot.is_junction()):raise InputError('自有输出目录不能重定向')
        base_name='wa_brand_'+ident+'_base.png';overlay_name='wa_brand_'+ident+'_overlay.png'
        preview_name='brand_poster_'+ident+'.png';package_name='brand_poster_'+ident+'.zip'
        for data,path in zip(encoded_images,[inputroot/base_name,inputroot/overlay_name,outroot/preview_name]):
            current()
            with path.open('xb') as stream:stream.write(data)
        current();service.refresh()
        spec=service.parse({'text':'调整图片尺寸1600x1600'})['spec']
        compiled=service.compile({'spec':spec,'concrete_template':native_template(base_name,overlay_name,'workflow_architect/brand_poster_native_'+ident),'input_overrides':{'source_image':base_name},'allow_draft':False})
        if not compiled.get('compiled') or not compiled['validation']['valid']:raise InputError('原生合成工作流未通过校验；保留自有准备副本，不排队')
        current();report.update(source_sha256=hashes,style=payload['style'],external_api_calls=0,queued=False)
        with zipfile.ZipFile(outroot/package_name,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('workflow.json',json.dumps(compiled['compiled']['ui_workflow'],ensure_ascii=False,indent=2));archive.write(inputroot/base_name,'input/'+base_name);archive.write(inputroot/overlay_name,'input/'+overlay_name)
            archive.writestr('receipt.json',json.dumps(report,ensure_ascii=False,indent=2));archive.writestr('README.txt','Copy both input/*.png files into the destination ComfyUI input folder, then load workflow.json. Text/product layers are baked; rebuild through Brand Poster panel to edit. No credential or paid execution is included.')
        for key,path in paths.items():
            if digest(path)!=hashes[key]:raise InputError('最终素材已变化，不采用准备结果；自有暂存副本保留，不排队')
        current()
        return {'compiled':compiled['compiled'],'validation':compiled['validation'],'report':report,
                'preview':{'filename':preview_name,'subfolder':'workflow_architect','type':'output'},'package_id':ident,
                'executed':False,'commercial_quality_approved':False,'notice':'已准备本机副本和原生合成图，不自动排队；文案真实性与视觉仍须人工验收。'}
    finally:SLOT.release()

def options(service,folders=None):
    if folders is None:
        import folder_paths as folders
    result=[]
    for kind,root,sub in [('input',Path(folders.get_input_directory()),''),('output',Path(folders.get_output_directory())/'workflow_architect','workflow_architect')]:
        if not root.exists():continue
        for path in sorted(root.glob('*.png')):
            if kind=='output' and not path.name.startswith(('poster_','brand_poster_')):continue
            if not path.is_file() or path.is_symlink() or path.stat().st_size>30*1024*1024:continue
            result.append({'filename':path.name,'subfolder':sub,'type':kind})
            if len(result)>2000:raise InputError('本机素材选项超过2000，请显式整理后再选，不删除原图')
    return {'assets':result,'execution_authorized':False,'font_available':Path('C:/Windows/Fonts/arialbd.ttf').is_file()}

def package_path(ident,folders=None):
    if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{32}',ident):raise InputError('海报包编号不合法')
    if folders is None:
        import folder_paths as folders
    root=Path(folders.get_output_directory()).resolve();path=root/'workflow_architect'/('brand_poster_'+ident+'.zip')
    if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size>30*1024*1024:raise InputError('海报包不存在或超限')
    return path
