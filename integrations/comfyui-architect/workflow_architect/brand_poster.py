"""Bounded deterministic commerce overlay; no diffusion, factual inference or IO."""
from PIL import Image,ImageDraw,ImageFont,ImageFilter,ImageOps
from .errors import InputError

SIZE=1600
DEFAULT={'headline':'SMART TV BOX','model':'V3','subtitle':'Bring Movie Night Home',
         'badges':[{'icon':'memory','lines':['2GB + 16GB']},{'icon':'chip','lines':['QUAD-CORE','2.0GHz']},{'icon':'voice','lines':['VOICE','REMOTE']},{'icon':'size','lines':['78 x 78','x 17mm']}],
         'accent':'#9D00FF'}

def validate_style(style):
    if not isinstance(style,dict) or set(style)!={'headline','model','subtitle','badges','accent'}:raise InputError('海报仅接受明确文案、四个参数卡与品牌色')
    for key,limit in [('headline',24),('model',8),('subtitle',42)]:
        if not isinstance(style[key],str) or not 1<=len(style[key])<=limit or not style[key].isascii() or any(ord(c)<32 for c in style[key]):raise InputError('海报英文文案为空、超限或含非法字符')
    import re
    if not isinstance(style['accent'],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',style['accent']):raise InputError('品牌色须#RRGGBB')
    if not isinstance(style['badges'],list) or len(style['badges'])!=4:raise InputError('必须四个真实资料参数卡')
    for badge in style['badges']:
        if not isinstance(badge,dict) or set(badge)!={'icon','lines'} or badge['icon'] not in ('memory','chip','voice','size'):raise InputError('图标类型不支持')
        lines=badge['lines']
        if not isinstance(lines,list) or not 1<=len(lines)<=2 or any(not isinstance(s,str) or not 1<=len(s)<=14 or not s.isascii() or any(ord(c)<32 for c in s) for s in lines):raise InputError('参数文案最多两行，每行14英文字符；不截断')
    return style

def bounded(image,max_pixels=16000000):
    if not isinstance(image,Image.Image) or min(image.size)<1 or max(image.size)>8192 or image.width*image.height>max_pixels:raise InputError('海报输入图像尺寸超限')

def cutout(source,mask,crop=None):
    bounded(source);bounded(mask)
    if source.size!=mask.size:raise InputError('抠图蒙版与原图尺寸不一致')
    image=source.convert('RGBA');alpha=mask.convert('L')
    if crop is not None:
        if not isinstance(crop,(list,tuple)) or len(crop)!=4 or any(type(v) not in (int,float) or not 0<=v<=1 for v in crop) or not crop[0]<crop[2] or not crop[1]<crop[3]:raise InputError('素材裁切必须是有效归一化范围')
        box=(int(crop[0]*source.width),int(crop[1]*source.height),int(crop[2]*source.width),int(crop[3]*source.height));image=image.crop(box);alpha=alpha.crop(box)
    bbox=alpha.point(lambda p:255 if p>24 else 0).getbbox()
    if not bbox:raise InputError('蒙版无有效前景')
    # Contract the matte by one source pixel to suppress white backdrop fringes.
    alpha=alpha.filter(ImageFilter.MinFilter(3));image.putalpha(alpha)
    image=image.crop(bbox)
    if image.width<32 or image.height<32:raise InputError('有效商品太小')
    return image

def fit(image,width,height):
    scale=min(width/image.width,height/image.height)
    return image.resize((max(1,round(image.width*scale)),max(1,round(image.height*scale))),Image.Resampling.LANCZOS)

def tabletop_remote(image):
    """Fixed camera-plane homography, never generatively redraw remote keys."""
    import numpy as np
    destination=[(140,0),(329,18),(250,559),(0,530)]
    source=[(0,0),(image.width-1,0),(image.width-1,image.height-1),(0,image.height-1)]
    rows=[];values=[]
    for (x,y),(u,v) in zip(destination,source):
        rows.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]]);values.extend([u,v])
    coefficients=np.linalg.solve(np.asarray(rows,dtype=float),np.asarray(values,dtype=float))
    return image.transform((330,560),Image.Transform.PERSPECTIVE,tuple(coefficients),resample=Image.Resampling.BICUBIC)

def font(path,size):
    try:return ImageFont.truetype(str(path),size)
    except Exception:raise InputError('请提供可用英文字体，不静默替换字体') from None

def icon(draw,kind,cx,cy):
    white='#FFFFFF';w=5
    if kind=='memory':
        draw.rounded_rectangle((cx-37,cy-22,cx+37,cy+20),radius=3,outline=white,width=4)
        for offset in (-22,0,22):draw.rectangle((cx+offset-6,cy-12,cx+offset+6,cy+7),fill=white)
        for offset in (-27,-9,9,27):draw.line((cx+offset,cy+21,cx+offset,cy+31),fill=white,width=5)
    elif kind=='chip':
        draw.rounded_rectangle((cx-27,cy-27,cx+27,cy+27),radius=5,outline=white,width=w)
        draw.rectangle((cx-13,cy-13,cx+13,cy+13),outline=white,width=3)
        for offset in (-18,0,18):
            for sign in (-1,1):
                draw.line((cx+offset,cy+sign*29,cx+offset,cy+sign*40),fill=white,width=4);draw.line((cx+sign*29,cy+offset,cx+sign*40,cy+offset),fill=white,width=4)
    elif kind=='voice':
        draw.rounded_rectangle((cx-14,cy-34,cx+14,cy+9),radius=13,fill=white)
        draw.arc((cx-26,cy-17,cx+26,cy+30),0,180,fill=white,width=5);draw.line((cx,cy+29,cx,cy+42),fill=white,width=5);draw.line((cx-16,cy+42,cx+16,cy+42),fill=white,width=5)
    else:
        draw.rectangle((cx-33,cy-27,cx+33,cy+27),outline=white,width=5)
        for offset in (-15,0,15):draw.line((cx+offset,cy-26,cx+offset,cy-12),fill=white,width=3)

def shadow(layer,image,x,y,opacity=140):
    width=image.width;height=max(25,int(image.height*.10))
    alpha=image.getchannel('A').resize((width,height),Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(16))
    alpha=alpha.point(lambda p:int(p*opacity/255))
    shade=Image.new('RGBA',(width,height),(0,0,0,0));shade.putalpha(alpha)
    layer.alpha_composite(shade,(x+14,y+image.height-height//2-9))

def create_overlay(product,product_mask,remote,remote_mask,style,font_path):
    validate_style(style)
    device=fit(cutout(product,product_mask),800,610)
    control=cutout(remote,remote_mask,crop=(0,0,.43,1))
    control=tabletop_remote(fit(control,255,800))
    overlay=Image.new('RGBA',(SIZE,SIZE),(0,0,0,0));top=Image.new('RGBA',(SIZE,420),(0,0,0,0))
    gradient=Image.linear_gradient('L').rotate(180).resize((SIZE,420));gradient=gradient.point(lambda p:int(p*.88));top.putalpha(gradient);overlay.alpha_composite(top,(0,0))
    x=400;y=1460-device.height;rx=1200;ry=1540-control.height
    if rx+control.width>1580:rx=1580-control.width
    shadow(overlay,device,x,y,180);shadow(overlay,control,rx,ry,165)
    overlay.alpha_composite(device,(x,y));overlay.alpha_composite(control,(rx,ry))
    draw=ImageDraw.Draw(overlay)
    model_font=font(font_path,150);headline_font=font(font_path,100);sub_font=font(font_path,46)
    draw.text((48,28),style['model'],font=model_font,fill=style['accent'],stroke_width=1)
    modelwidth=draw.textlength(style['model'],font=model_font)
    available=SIZE-(70+modelwidth)-45
    while draw.textlength(style['headline'],font=headline_font)>available and headline_font.size>30:headline_font=font(font_path,headline_font.size-1)
    draw.text((70+modelwidth,74),style['headline'],font=headline_font,fill='white',stroke_width=1)
    subtitle_width=draw.textlength(style['subtitle'],font=sub_font)
    if subtitle_width>1400:raise InputError('副标题超出版面')
    draw.rounded_rectangle((52,222,95+subtitle_width,302),radius=24,fill=style['accent']);draw.text((74,232),style['subtitle'],font=sub_font,fill='white')
    badge_font=font(font_path,39)
    for index,badge in enumerate(style['badges']):
        cy=895+index*174;draw.ellipse((38,cy-60,158,cy+60),fill=style['accent'],outline='#D9B9FF',width=2);icon(draw,badge['icon'],98,cy)
        lines=badge['lines'];ty=cy-26 if len(lines)==1 else cy-47
        for line in lines:
            if draw.textlength(line,font=badge_font)>290:raise InputError('参数文字超出侧栏，拒绝截断')
            draw.text((183,ty),line,font=badge_font,fill='white',stroke_width=0);ty+=45
    report={'size':[SIZE,SIZE],'product_box':[x,y,x+device.width,y+device.height],'remote_box':[rx,ry,rx+control.width,ry+control.height],
            'typography':'deterministic-english','product_redrawn_by_diffusion':False,'copy_independently_verified':False,
            'remote_source_crop':[0,0,.43,1],'commercial_quality_approved':False}
    return overlay,report

def compose(background,overlay,table=None):
    bounded(background)
    if overlay.size!=(SIZE,SIZE):raise InputError('品牌叠加层尺寸不符')
    if table is None:
        base=ImageOps.fit(background.convert('RGBA'),(SIZE,SIZE),method=Image.Resampling.LANCZOS)
    else:
        bounded(table)
        base=Image.new('RGBA',(SIZE,SIZE),'#090909')
        scene=ImageOps.fit(background.convert('RGBA'),(SIZE,1000),method=Image.Resampling.LANCZOS);base.alpha_composite(scene,(0,140))
        surface=ImageOps.fit(table.convert('RGBA'),(SIZE,700),method=Image.Resampling.LANCZOS)
        base.alpha_composite(surface,(0,900))
    return Image.alpha_composite(base,overlay).convert('RGB')
