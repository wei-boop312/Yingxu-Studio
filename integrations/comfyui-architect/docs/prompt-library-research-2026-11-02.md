# 七维电商提示词手册 · 2026-11-02

新增56卡，保留原24卡，共80卡。每个新增维度8卡；分类索引和完整可填写模板见下文。研究只读公开资料，不下载模型、不启用API、不提交生图。配方由本地重新编写，不全文复制网页或社区提示词集合；来源只支持方法和限制，不证明本机已安装、兼容或效果达标。

## 组合方法

先选任务卡，再选一个环境、一个镜头、一种角度、真实材质和一个主光，最后规划文字区；不要把整库词语拼成一个超长prompt。环境可独立生成，真实商品与Logo优先原图合成。参考编辑每次只改一个主目标，明确图1商品/模特、图2衣服/场景/姿态各自用途。缺少背面或内部照片，应补素材而非把AI想象当实物。

镜头：normal/telephoto为透视风格；macro为近摄意图；deep focus保留关键边缘，shallow depth只虚化背景。它们不是真实EXIF，不保证图像具有某台相机的光学特性。纹理：区分粗糙度、方向性、透射/反射、织纹尺度、涂层与基底。灯光：分别检查方向、面积、软硬、色温、反差、衰减与接触影，避免堆叠互相矛盾的形容词。

文字：先确认逐字文案和事实编号，再做层级、网格、行距、对齐、安全边距、图标与单位；在实际缩略图下检查可读性。文字卡含2张生图/图中文字编辑实验和6张排版说明，不代表版式引擎新增全部能力；Qwen/FLUX文字编辑属于可探索能力，关键参数和品牌文字仍使用可编辑排版层。

## 模型与端口区别

- SDXL：官方Diffusers有双文本编码器和负面输入；本机节点是否暴露同样字段需另核，不能直接把Python参数名当Comfy输入。
- FLUX.2 pro/max：所读指南注明不支持negative prompt；不要把SDXL负面词串照搬。指南针对的版本与本地Klein不是同一效果验收。
- Qwen编辑：参考图与明确改动指令有独立作用；模型卡的文字编辑能力不是每个字符必然正确的承诺。
- IP-Adapter/ControlNet：图像、姿态、深度等是额外条件，关键词不会自动加载节点或权重。
- IC-Light：重打光涉及前景/背景条件与latent控制，不是只给普通采样器增加“柔光”就完成接入。

## 来源与核对摘要

### PS01 · FLUX.2 prompting

[原始资料](https://docs.bfl.ai/guides/prompting_guide_flux2) · 核对日期 2026-11-02

官方FLUX.2 pro/max文档提供自然语言构图、镜头和文字描述建议，并注明无负面提示支持；不能泛化为本地Klein或其他模型已实测。

### PS02 · Diffusers SDXL

[原始资料](https://huggingface.co/docs/diffusers/main/using-diffusers/sdxl) · 核对日期 2026-11-02

官方说明SDXL双文本编码器与独立条件输入；负面条件是否生效要看实际guidance和节点。

### PS03 · Qwen Image Edit

[原始资料](https://huggingface.co/Qwen/Qwen-Image-Edit) · 核对日期 2026-11-02

模型卡展示参考图编辑与文字编辑能力；能力声明不等于商品细节或每个字符必然正确。

### PS04 · IC-Light

[原始资料](https://github.com/lllyasviel/IC-Light) · 核对日期 2026-11-02

上游区分前景文本条件和背景条件重打光，并说明Lighting Preference属于初始latent控制；单靠描述不是已接入重打光。

### PS05 · IP-Adapter

[原始资料](https://github.com/tencent-ailab/IP-Adapter) · 核对日期 2026-11-02

上游展示图像与文本条件结合；图像裁切可能丢边缘，适配器类别与权重需匹配，文字不替代身份条件。

### PS06 · ControlNet

[原始资料](https://github.com/lllyasviel/ControlNet) · 核对日期 2026-11-02

上游列出边缘、深度与姿态等结构条件；姿势与几何约束需要实际控制图和匹配模型，不靠提示词自动启用。

### PS07 · ComfyUI examples

[原始资料](https://comfyanonymous.github.io/ComfyUI_examples/) · 核对日期 2026-11-02

官方示例图片含工作流元数据；真正工作流与独立提示词资料JSON不同。原Comfy-Org/ComfyUI_examples猜测URL404，已换有效入口。

### PS08 · Adobe product photography

[原始资料](https://www.adobe.com/creativecloud/photography/type/product-photography.html) · 核对日期 2026-11-02

产品摄影教程强调受控光线和拍摄实践；我们将其用于场景检查，不搬用为生成模型参数保证。

### PS09 · Canon macro focus

[原始资料](https://www.usa.canon.com/learning/training-articles/training-articles-list/macro-photography-how-to-look-sharp) · 核对日期 2026-11-02

微距教程解释景深局限与焦点堆栈思路；生成描述不能证明不存在于原图的细节。

### PS10 · Adobe Spectrum typography

[原始资料](https://spectrum.adobe.com/foundations/typography/typography-system) · 核对日期 2026-11-02

设计系统用字重、字号等区分层级。电商排版卡是本地原创应用，不是亚马逊规则或平台审核证明。

检索中遇到的Kontext旧猜测地址和Comfy-Org/ComfyUI_examples地址404，未将它们记为成功资料；Comfy示例已核对有效官方入口。不执行网页README中的安装命令，不读取用户凭证或搬运远程工作流为执行许可。

## 新增卡片完整内容

### 美式客厅·日间 · env_living

分类：环境空间；用途：text-to-image；参考：PS01、PS08。

素材：生成无商品背景；商品原图和匹配蒙版后合成。

变量示例：palette=warm neutrals with charcoal accents；landing=lower right。

```text
Commercial background photograph. A lived-in contemporary living room with an unoccupied sofa, a low walnut console and generous breathing room. Side daylight softly shapes the room; the television is a neutral blank surface for licensed content later. Use {palette} as the restrained color family. Reserve a clear landing zone at {landing} for the real product to be composited separately. Maintain coherent physical scale and lighting, with uncluttered separation of foreground and background. Keep the set free of baked-in text, unverified symbols and invented merchandise.
```

验收：检查水平线、落位面积、尺度与场景透视；不让道具暗示未验证功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 办公桌·效率场景 · env_desk

分类：环境空间；用途：text-to-image；参考：PS01、PS08。

素材：生成无商品背景；商品原图和匹配蒙版后合成。

变量示例：palette=warm neutrals with charcoal accents；landing=lower right。

```text
Commercial background photograph. An orderly home-office desk with a restrained keyboard and notebook in the distance. A continuous clear desk plane occupies the foreground. Cable clutter is controlled without making the room feel sterile. Use {palette} as the restrained color family. Reserve a clear landing zone at {landing} for the real product to be composited separately. Maintain coherent physical scale and lighting, with uncluttered separation of foreground and background. Keep the set free of baked-in text, unverified symbols and invented merchandise.
```

验收：检查水平线、落位面积、尺度与场景透视；不让道具暗示未验证功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 厨房·干净台面 · env_kitchen

分类：环境空间；用途：text-to-image；参考：PS01、PS08。

素材：生成无商品背景；商品原图和匹配蒙版后合成。

变量示例：palette=warm neutrals with charcoal accents；landing=lower right。

```text
Commercial background photograph. A bright domestic kitchen with a clean pale worktop, restrained cabinetry and softly blurred cookware far from the landing zone. Keep the counter dry and physically continuous, with no implied food-contact certification. Use {palette} as the restrained color family. Reserve a clear landing zone at {landing} for the real product to be composited separately. Maintain coherent physical scale and lighting, with uncluttered separation of foreground and background. Keep the set free of baked-in text, unverified symbols and invented merchandise.
```

验收：检查水平线、落位面积、尺度与场景透视；不让道具暗示未验证功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 浴室·护肤场景 · env_bath

分类：环境空间；用途：text-to-image；参考：PS01、PS08。

素材：生成无商品背景；商品原图和匹配蒙版后合成。

变量示例：palette=warm neutrals with charcoal accents；landing=lower right。

```text
Commercial background photograph. A calm bathroom vanity with pale stone and diffused daylight, small distant folded towels and a dry foreground. Restrained moisture in the atmosphere; no water droplets on the future product footprint. Use {palette} as the restrained color family. Reserve a clear landing zone at {landing} for the real product to be composited separately. Maintain coherent physical scale and lighting, with uncluttered separation of foreground and background. Keep the set free of baked-in text, unverified symbols and invented merchandise.
```

验收：检查水平线、落位面积、尺度与场景透视；不让道具暗示未验证功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 户外·自然石台 · env_outdoor

分类：环境空间；用途：text-to-image；参考：PS01、PS08。

素材：生成无商品背景；商品原图和匹配蒙版后合成。

变量示例：palette=warm neutrals with charcoal accents；landing=lower right。

```text
Commercial background photograph. A shaded garden terrace with a stable stone surface, gentle foliage depth and realistic open-sky illumination. The intended product area remains dry, with no implied waterproof or outdoor durability test. Use {palette} as the restrained color family. Reserve a clear landing zone at {landing} for the real product to be composited separately. Maintain coherent physical scale and lighting, with uncluttered separation of foreground and background. Keep the set free of baked-in text, unverified symbols and invented merchandise.
```

验收：检查水平线、落位面积、尺度与场景透视；不让道具暗示未验证功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 卧室·柔和生活方式 · env_bedroom

分类：环境空间；用途：text-to-image；参考：PS01、PS08。

素材：生成无商品背景；商品原图和匹配蒙版后合成。

变量示例：palette=warm neutrals with charcoal accents；landing=lower right。

```text
Commercial background photograph. A quiet adult bedroom in morning light, plain linen bedding, simple bedside furniture and uncluttered wall planes. Keep a credible foreground surface rather than an unsupported floating pedestal. Use {palette} as the restrained color family. Reserve a clear landing zone at {landing} for the real product to be composited separately. Maintain coherent physical scale and lighting, with uncluttered separation of foreground and background. Keep the set free of baked-in text, unverified symbols and invented merchandise.
```

验收：检查水平线、落位面积、尺度与场景透视；不让道具暗示未验证功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 零售·展示空间 · env_retail

分类：环境空间；用途：text-to-image；参考：PS01、PS08。

素材：生成无商品背景；商品原图和匹配蒙版后合成。

变量示例：palette=warm neutrals with charcoal accents；landing=lower right。

```text
Commercial background photograph. An understated boutique display environment with layered shelves and soft architectural lighting. Keep the foreground plinth broad and physically grounded, without fabricated price tickets, seals or endorsement signs. Use {palette} as the restrained color family. Reserve a clear landing zone at {landing} for the real product to be composited separately. Maintain coherent physical scale and lighting, with uncluttered separation of foreground and background. Keep the set free of baked-in text, unverified symbols and invented merchandise.
```

验收：检查水平线、落位面积、尺度与场景透视；不让道具暗示未验证功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 节日·克制氛围 · env_season

分类：环境空间；用途：text-to-image；参考：PS01、PS08。

素材：生成无商品背景；商品原图和匹配蒙版后合成。

变量示例：palette=warm neutrals with charcoal accents；landing=lower right。

```text
Commercial background photograph. A restrained winter gifting environment with distant out-of-focus warm lights and small neutral wrapped props. Keep decorations outside the future product silhouette and reserve a calm area for later campaign typography. Use {palette} as the restrained color family. Reserve a clear landing zone at {landing} for the real product to be composited separately. Maintain coherent physical scale and lighting, with uncluttered separation of foreground and background. Keep the set free of baked-in text, unverified symbols and invented merchandise.
```

验收：检查水平线、落位面积、尺度与场景透视；不让道具暗示未验证功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 标准视角·自然透视 · camera_normal

分类：镜头表现；用途：reference-edit；参考：PS08、PS09。

素材：图1真实商品；需要新角度时另提供对应实拍，不据文字臆造背面。

变量示例：subject=the product in image 1；setting=a restrained neutral studio。

```text
Reference-guided commercial edit of {subject} in {setting}. Use a normal-lens visual impression with level camera orientation and moderate working distance. Keep near and far edges believable and avoid the enlarged front face associated with a very close camera. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：焦距/光圈是视觉意图，不是EXIF或真实拍摄证明；检查完整清晰商品而非只追求虚化；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 中长焦·压缩感 · camera_tele

分类：镜头表现；用途：reference-edit；参考：PS08、PS09。

素材：图1真实商品；需要新角度时另提供对应实拍，不据文字臆造背面。

变量示例：subject=the product in image 1；setting=a restrained neutral studio。

```text
Reference-guided commercial edit of {subject} in {setting}. Use a modest telephoto visual impression from a greater working distance, restrained perspective convergence and a quiet background. Keep the product at a credible scale without flattening its recognizable shape. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：焦距/光圈是视觉意图，不是EXIF或真实拍摄证明；检查完整清晰商品而非只追求虚化；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 微距·关键细节 · camera_macro

分类：镜头表现；用途：reference-edit；参考：PS08、PS09。

素材：图1真实商品；需要新角度时另提供对应实拍，不据文字臆造背面。

变量示例：subject=the product in image 1；setting=a restrained neutral studio。

```text
Reference-guided commercial edit of {subject} in {setting}. Frame only a detail that is visible in the source photograph. Place the focal plane across that detail; allow a gradual falloff behind it. Do not fabricate pores, threads, serial numbers or machining marks. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：焦距/光圈是视觉意图，不是EXIF或真实拍摄证明；检查完整清晰商品而非只追求虚化；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 深景深·完整可读 · camera_deep

分类：镜头表现；用途：reference-edit；参考：PS08、PS09。

素材：图1真实商品；需要新角度时另提供对应实拍，不据文字臆造背面。

变量示例：subject=the product in image 1；setting=a restrained neutral studio。

```text
Reference-guided commercial edit of {subject} in {setting}. Keep the front edge, key controls and visible rear boundary simultaneously legible with a deep-focus visual impression. Avoid artificial sharpened outlines and do not blur factual ports or labels. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：焦距/光圈是视觉意图，不是EXIF或真实拍摄证明；检查完整清晰商品而非只追求虚化；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 浅景深·背景分离 · camera_shallow

分类：镜头表现；用途：reference-edit；参考：PS08、PS09。

素材：图1真实商品；需要新角度时另提供对应实拍，不据文字臆造背面。

变量示例：subject=the product in image 1；setting=a restrained neutral studio。

```text
Reference-guided commercial edit of {subject} in {setting}. Keep the full functional product area crisp while allowing a smooth background blur. The focus plane must not cut through a logo or a dimension-critical edge. Avoid pasted bokeh circles. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：焦距/光圈是视觉意图，不是EXIF或真实拍摄证明；检查完整清晰商品而非只追求虚化；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 留白构图·文案区域 · camera_negative

分类：镜头表现；用途：reference-edit；参考：PS08、PS09。

素材：图1真实商品；需要新角度时另提供对应实拍，不据文字臆造背面。

变量示例：subject=the product in image 1；setting=a restrained neutral studio。

```text
Reference-guided commercial edit of {subject} in {setting}. Compose an asymmetrical product presentation with substantial clean space beside the subject. Keep the quiet area free of bright edge detail, placing the visual mass away from future headline placement. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：焦距/光圈是视觉意图，不是EXIF或真实拍摄证明；检查完整清晰商品而非只追求虚化；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 近景·质感展示 · camera_close

分类：镜头表现；用途：reference-edit；参考：PS08、PS09。

素材：图1真实商品；需要新角度时另提供对应实拍，不据文字臆造背面。

变量示例：subject=the product in image 1；setting=a restrained neutral studio。

```text
Reference-guided commercial edit of {subject} in {setting}. Use a tight but complete product framing with deliberate margins. Preserve the entire silhouette and avoid clipping small feet, strap ends or protruding connectors. Keep the perceived camera height coherent with the source. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：焦距/光圈是视觉意图，不是EXIF或真实拍摄证明；检查完整清晰商品而非只追求虚化；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 环境中景·比例可信 · camera_wide

分类：镜头表现；用途：reference-edit；参考：PS08、PS09。

素材：图1真实商品；需要新角度时另提供对应实拍，不据文字臆造背面。

变量示例：subject=the product in image 1；setting=a restrained neutral studio。

```text
Reference-guided commercial edit of {subject} in {setting}. Place the product as a credible part of a wider setting, with enough environmental context to communicate use. Keep the product identifiable but not implausibly large relative to furniture or people. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：焦距/光圈是视觉意图，不是EXIF或真实拍摄证明；检查完整清晰商品而非只追求虚化；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 正面·对称展示 · angle_front

分类：产品角度；用途：reference-edit；参考：PS03、PS06。

素材：图1必须已经是目标角度实拍；本卡只整理背景、构图、光线，不凭单张图证明隐藏结构。

变量示例：subject=the exact product in image 1；backdrop=a neutral gray studio background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use the supplied front view, level horizon, centered axis and balanced left-right margins. Preserve the exact front panel and visible controls, avoiding an invented symmetric redesign. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：逐项核对轮廓、Logo、端口数量与遮挡；缺目标视角应补拍，不能生成后称实拍；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 前侧四分之三·厚度展示 · angle_threequarter

分类：产品角度；用途：reference-edit；参考：PS03、PS06。

素材：图1必须已经是目标角度实拍；本卡只整理背景、构图、光线，不凭单张图证明隐藏结构。

变量示例：subject=the exact product in image 1；backdrop=a neutral gray studio background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use the supplied three-quarter angle so the top and one side are legible. Preserve the existing perspective and edge convergence; communicate thickness through real visible shading rather than an exaggerated bevel. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：逐项核对轮廓、Logo、端口数量与遮挡；缺目标视角应补拍，不能生成后称实拍；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 俯视·平铺展示 · angle_top

分类：产品角度；用途：reference-edit；参考：PS03、PS06。

素材：图1必须已经是目标角度实拍；本卡只整理背景、构图、光线，不凭单张图证明隐藏结构。

变量示例：subject=the exact product in image 1；backdrop=a neutral gray studio background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use the supplied overhead view. Keep the main plane parallel to the image plane and arrange only confirmed included items with controlled gaps. Avoid presenting a tilted reference as an exact orthographic measurement. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：逐项核对轮廓、Logo、端口数量与遮挡；缺目标视角应补拍，不能生成后称实拍；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 侧面·轮廓展示 · angle_side

分类：产品角度；用途：reference-edit；参考：PS03、PS06。

素材：图1必须已经是目标角度实拍；本卡只整理背景、构图、光线，不凭单张图证明隐藏结构。

变量示例：subject=the exact product in image 1；backdrop=a neutral gray studio background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use the original side profile, with a stable baseline and breathing room around protrusions. Retain the source thickness, connector geometry and curve transitions without beautifying the silhouette. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：逐项核对轮廓、Logo、端口数量与遮挡；缺目标视角应补拍，不能生成后称实拍；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 背面·功能展示 · angle_back

分类：产品角度；用途：reference-edit；参考：PS03、PS06。

素材：图1必须已经是目标角度实拍；本卡只整理背景、构图、光线，不凭单张图证明隐藏结构。

变量示例：subject=the exact product in image 1；backdrop=a neutral gray studio background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use the actual rear-view source. Keep all visible sockets and markings exactly as supplied, with unobstructed space for external callout lines. Unidentified holes remain unlabeled. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：逐项核对轮廓、Logo、端口数量与遮挡；缺目标视角应补拍，不能生成后称实拍；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 低机位·稳重表现 · angle_low

分类：产品角度；用途：reference-edit；参考：PS03、PS06。

素材：图1必须已经是目标角度实拍；本卡只整理背景、构图、光线，不凭单张图证明隐藏结构。

变量示例：subject=the exact product in image 1；backdrop=a neutral gray studio background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use a source already captured from a low camera position. Retain its actual top visibility and ground plane; avoid stretching the vertical face or transforming a thin enclosure into a heavy tower. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：逐项核对轮廓、Logo、端口数量与遮挡；缺目标视角应补拍，不能生成后称实拍；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 局部接口·功能近景 · angle_detail

分类：产品角度；用途：reference-edit；参考：PS03、PS06。

素材：图1必须已经是目标角度实拍；本卡只整理背景、构图、光线，不凭单张图证明隐藏结构。

变量示例：subject=the exact product in image 1；backdrop=a neutral gray studio background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Crop a real close view of the selected interface, retaining enough surrounding casing to establish its location. Keep the socket interior and pin pattern as photographed, without generative enhancement of unreadable detail. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：逐项核对轮廓、Logo、端口数量与遮挡；缺目标视角应补拍，不能生成后称实拍；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 多角度系列·一致性 · angle_set

分类：产品角度；用途：reference-edit；参考：PS03、PS06。

素材：图1必须已经是目标角度实拍；本卡只整理背景、构图、光线，不凭单张图证明隐藏结构。

变量示例：subject=the exact product in image 1；backdrop=a neutral gray studio background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Arrange supplied front, side and three-quarter photographs as one coordinated series. Keep consistent scale logic and tonal treatment while retaining view-specific lighting; do not infer a full 3D reconstruction. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：逐项核对轮廓、Logo、端口数量与遮挡；缺目标视角应补拍，不能生成后称实拍；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 哑光塑料·微粗糙 · material_plastic

分类：纹理材质；用途：reference-edit；参考：PS08、PS03。

素材：原图必须支持该材质；不要给哑光塑料强行换成金属后当商品事实。

变量示例：subject=the supplied product in image 1；light=soft controlled side illumination。

```text
Reference-guided commercial edit of {subject} under {light}. Render the existing matte polymer finish with low-intensity broad reflections and fine restrained surface variation. Keep mold lines and real edges visible, avoiding rubber-like softness or metallic highlights. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：纹理只是编辑意图，微细节和色卡需原图对照；不能把AI修饰当材料检测；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 拉丝金属·方向一致 · material_brushed

分类：纹理材质；用途：reference-edit；参考：PS08、PS03。

素材：原图必须支持该材质；不要给哑光塑料强行换成金属后当商品事实。

变量示例：subject=the supplied product in image 1；light=soft controlled side illumination。

```text
Reference-guided commercial edit of {subject} under {light}. Keep the existing brushed-metal direction consistent across each real surface. Let elongated highlights follow the material and curvature. Avoid random scratch patterns and do not add a brushed finish absent from the reference. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：纹理只是编辑意图，微细节和色卡需原图对照；不能把AI修饰当材料检测；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 抛光金属·反射控制 · material_polished

分类：纹理材质；用途：reference-edit；参考：PS08、PS03。

素材：原图必须支持该材质；不要给哑光塑料强行换成金属后当商品事实。

变量示例：subject=the supplied product in image 1；light=soft controlled side illumination。

```text
Reference-guided commercial edit of {subject} under {light}. Retain the polished surface with coherent large reflected shapes and restrained bright accents. Reflections must follow the object curvature; preserve edge separation without turning the object into a mirror of an unrelated room. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：纹理只是编辑意图，微细节和色卡需原图对照；不能把AI修饰当材料检测；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 玻璃·透明与折射 · material_glass

分类：纹理材质；用途：reference-edit；参考：PS08、PS03。

素材：原图必须支持该材质；不要给哑光塑料强行换成金属后当商品事实。

变量示例：subject=the supplied product in image 1；light=soft controlled side illumination。

```text
Reference-guided commercial edit of {subject} under {light}. Retain visible glass thickness, existing tint and plausible transmitted background. Show restrained edge highlights; avoid invented contents, impossible transparency or a floating outline disconnected from its base. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：纹理只是编辑意图，微细节和色卡需原图对照；不能把AI修饰当材料检测；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 织物·织纹与褶皱 · material_fabric

分类：纹理材质；用途：reference-edit；参考：PS08、PS03。

素材：原图必须支持该材质；不要给哑光塑料强行换成金属后当商品事实。

变量示例：subject=the supplied product in image 1；light=soft controlled side illumination。

```text
Reference-guided commercial edit of {subject} under {light}. Retain the actual weave direction, seam construction and fabric weight visible in the reference. Let folds arise from gravity and real tension points rather than adding decorative wrinkles everywhere. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：纹理只是编辑意图，微细节和色卡需原图对照；不能把AI修饰当材料检测；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 皮革·自然纹理 · material_leather

分类：纹理材质；用途：reference-edit；参考：PS08、PS03。

素材：原图必须支持该材质；不要给哑光塑料强行换成金属后当商品事实。

变量示例：subject=the supplied product in image 1；light=soft controlled side illumination。

```text
Reference-guided commercial edit of {subject} under {light}. Keep the supplied leather-like grain, edge finishing and stitching scale. Use gentle tonal variation rather than repetitive stamped pores. Do not imply genuine leather certification from appearance. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：纹理只是编辑意图，微细节和色卡需原图对照；不能把AI修饰当材料检测；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 陶瓷·釉面层次 · material_ceramic

分类：纹理材质；用途：reference-edit；参考：PS08、PS03。

素材：原图必须支持该材质；不要给哑光塑料强行换成金属后当商品事实。

变量示例：subject=the supplied product in image 1；light=soft controlled side illumination。

```text
Reference-guided commercial edit of {subject} under {light}. Preserve the existing glazed or unglazed finish and rim thickness. Use smooth broad tonal transitions with restrained specular reflection; keep actual surface irregularities instead of inventing handmade defects. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：纹理只是编辑意图，微细节和色卡需原图对照；不能把AI修饰当材料检测；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 纸张包装·印刷边缘 · material_paper

分类：纹理材质；用途：reference-edit；参考：PS08、PS03。

素材：原图必须支持该材质；不要给哑光塑料强行换成金属后当商品事实。

变量示例：subject=the supplied product in image 1；light=soft controlled side illumination。

```text
Reference-guided commercial edit of {subject} under {light}. Keep the supplied paper or cardboard structure, folds and print registration. Render a restrained fiber impression where already supported. Preserve exact packaging artwork rather than substituting invented seals or premium labels. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：纹理只是编辑意图，微细节和色卡需原图对照；不能把AI修饰当材料检测；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 自然肖像·肤质真实 · person_portrait

分类：人物模特；用途：reference-edit；参考：PS03、PS05、PS06。

素材：图1获授权成年模特；姿势参考如有必须单独说明，当前库不自动安装身份或姿态模型。

变量示例：person=the authorized adult in image 1；setting=a natural contemporary lifestyle setting。

```text
Reference-guided commercial edit of {person} in {setting}. Create a relaxed chest-up commercial portrait with a subtle expression and natural skin variation. Keep facial proportions and recognizable accessories consistent with the reference, with gentle eye highlights and no porcelain smoothing. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：核脸部身份、手指数、脚部、身体比例与商品接触；描述不能代替专用条件或本人许可；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 站姿·全身服装展示 · person_standing

分类：人物模特；用途：reference-edit；参考：PS03、PS05、PS06。

素材：图1获授权成年模特；姿势参考如有必须单独说明，当前库不自动安装身份或姿态模型。

变量示例：person=the authorized adult in image 1；setting=a natural contemporary lifestyle setting。

```text
Reference-guided commercial edit of {person} in {setting}. Use a balanced full-body standing pose with visible feet, relaxed shoulders and believable weight distribution. Keep the complete outfit within the frame and leave separation between arms and torso. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：核脸部身份、手指数、脚部、身体比例与商品接触；描述不能代替专用条件或本人许可；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 坐姿·生活方式 · person_seated

分类：人物模特；用途：reference-edit；参考：PS03、PS05、PS06。

素材：图1获授权成年模特；姿势参考如有必须单独说明，当前库不自动安装身份或姿态模型。

变量示例：person=the authorized adult in image 1；setting=a natural contemporary lifestyle setting。

```text
Reference-guided commercial edit of {person} in {setting}. Use a comfortable seated posture with plausible hip and knee angles, hands resting naturally and a credible chair contact. Avoid merging limbs with furniture or hiding structural errors behind excessive blur. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：核脸部身份、手指数、脚部、身体比例与商品接触；描述不能代替专用条件或本人许可；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 手部持物·接触与遮挡 · person_hands

分类：人物模特；用途：reference-edit；参考：PS03、PS05、PS06。

素材：图1获授权成年模特；姿势参考如有必须单独说明，当前库不自动安装身份或姿态模型。

变量示例：person=the authorized adult in image 1；setting=a natural contemporary lifestyle setting。

```text
Reference-guided commercial edit of {person} in {setting}. Show a restrained single-object handling gesture with anatomically plausible fingers. Keep the held product identity tied to its supplied reference, with correct occlusion and contact shadows. If fine markings drift, return to staged photography or verified compositing. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：核脸部身份、手指数、脚部、身体比例与商品接触；描述不能代替专用条件或本人许可；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 多人场景·分工清楚 · person_group

分类：人物模特；用途：reference-edit；参考：PS03、PS05、PS06。

素材：图1获授权成年模特；姿势参考如有必须单独说明，当前库不自动安装身份或姿态模型。

变量示例：person=the authorized adult in image 1；setting=a natural contemporary lifestyle setting。

```text
Reference-guided commercial edit of {person} in {setting}. Show a small group of authorized adults interacting naturally, with distinct bodies and individually coherent gaze directions. Keep every person's limb ownership clear and avoid duplicated facial features. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：核脸部身份、手指数、脚部、身体比例与商品接触；描述不能代替专用条件或本人许可；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 熟龄模特·年龄真实 · person_mature

分类：人物模特；用途：reference-edit；参考：PS03、PS05、PS06。

素材：图1获授权成年模特；姿势参考如有必须单独说明，当前库不自动安装身份或姿态模型。

变量示例：person=the authorized adult in image 1；setting=a natural contemporary lifestyle setting。

```text
Reference-guided commercial edit of {person} in {setting}. Retain the adult subject's actual age cues, skin folds and natural expression. Use respectful ordinary styling and an approachable pose without artificial de-aging or exaggerated frailty. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：核脸部身份、手指数、脚部、身体比例与商品接触；描述不能代替专用条件或本人许可；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 服装展示·面料与版型 · person_wardrobe

分类：人物模特；用途：reference-edit；参考：PS03、PS05、PS06。

素材：图1获授权成年模特；姿势参考如有必须单独说明，当前库不自动安装身份或姿态模型。

变量示例：person=the authorized adult in image 1；setting=a natural contemporary lifestyle setting。

```text
Reference-guided commercial edit of {person} in {setting}. Keep the supplied garment construction, proportions and fit, with natural tension at shoulder and waist. Clearly expose the intended selling detail while retaining the model's identity and the reference garment's real coverage. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：核脸部身份、手指数、脚部、身体比例与商品接触；描述不能代替专用条件或本人许可；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 姿态参考·结构先行 · person_pose

分类：人物模特；用途：reference-edit；参考：PS03、PS05、PS06。

素材：图1获授权成年模特；姿势参考如有必须单独说明，当前库不自动安装身份或姿态模型。

变量示例：person=the authorized adult in image 1；setting=a natural contemporary lifestyle setting。

```text
Reference-guided commercial edit of {person} in {setting}. Use the separately supplied authorized pose reference for body arrangement only. Keep identity from image 1 and wardrobe from the designated garment reference; do not mix identity, pose and clothing roles. Check the actual pose-conditioning workflow separately. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：核脸部身份、手指数、脚部、身体比例与商品接触；描述不能代替专用条件或本人许可；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 大柔光箱·干净商业照 · light_softbox

分类：灯光质感；用途：reference-edit；参考：PS04、PS08。

素材：真实商品原图；接受受控光照变化，不能同时宣称全部像素不变。

变量示例：subject=the real product in image 1；backdrop=a quiet neutral background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use a large soft key from upper left with gentle front fill. Highlights are broad and graduated, shadow edges soft but grounded, and the existing surface color remains neutral rather than washed out. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：检查高光方向、阴影接触、边缘光和背景一致性；重打光流程与权重须另验；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 边缘光·深色主体分离 · light_rim

分类：灯光质感；用途：reference-edit；参考：PS04、PS08。

素材：真实商品原图；接受受控光照变化，不能同时宣称全部像素不变。

变量示例：subject=the real product in image 1；backdrop=a quiet neutral background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Add a restrained rear-side rim that separates the dark silhouette from the backdrop. Keep the interior surface readable with a low soft fill; avoid a uniform glowing outline around every edge. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：检查高光方向、阴影接触、边缘光和背景一致性；重打光流程与权重须另验；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 窗光·柔和自然 · light_window

分类：灯光质感；用途：reference-edit；参考：PS04、PS08。

素材：真实商品原图；接受受控光照变化，不能同时宣称全部像素不变。

变量示例：subject=the real product in image 1；backdrop=a quiet neutral background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use broad indirect window light from one side with a subtle room bounce. Maintain a believable falloff across the product, avoiding contradictory multiple suns or hard window patterns across critical markings. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：检查高光方向、阴影接触、边缘光和背景一致性；重打光流程与权重须另验；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 硬光·几何与结构 · light_hard

分类：灯光质感；用途：reference-edit；参考：PS04、PS08。

素材：真实商品原图；接受受控光照变化，不能同时宣称全部像素不变。

变量示例：subject=the real product in image 1；backdrop=a quiet neutral background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use one small directional light for deliberate crisp shadows and defined planes. Preserve detail in bright surfaces and shadowed functional areas; avoid crushing dark product controls to black. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：检查高光方向、阴影接触、边缘光和背景一致性；重打光流程与权重须另验；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 高调·白色层次 · light_highkey

分类：灯光质感；用途：reference-edit；参考：PS04、PS08。

素材：真实商品原图；接受受控光照变化，不能同时宣称全部像素不变。

变量示例：subject=the real product in image 1；backdrop=a quiet neutral background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use a bright neutral set with soft wraparound illumination while keeping the product boundary readable. Preserve subtle shadows and real white material detail rather than clipping everything to pure white. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：检查高光方向、阴影接触、边缘光和背景一致性；重打光流程与权重须另验；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 低调·黑色层次 · light_lowkey

分类：灯光质感；用途：reference-edit；参考：PS04、PS08。

素材：真实商品原图；接受受控光照变化，不能同时宣称全部像素不变。

变量示例：subject=the real product in image 1；backdrop=a quiet neutral background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use a dark controlled set with selective soft highlights and enough fill to resolve real edges. Keep the product surface distinguishable from the background without artificial cyan outlines. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：检查高光方向、阴影接触、边缘光和背景一致性；重打光流程与权重须另验；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 掠射光·纹理起伏 · light_grazing

分类：灯光质感；用途：reference-edit；参考：PS04、PS08。

素材：真实商品原图；接受受控光照变化，不能同时宣称全部像素不变。

变量示例：subject=the real product in image 1；backdrop=a quiet neutral background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Use a restrained grazing light across the existing material to reveal real surface relief. Keep texture scale consistent and avoid adding dust, scratches or a dramatically rough finish unsupported by the reference. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：检查高光方向、阴影接触、边缘光和背景一致性；重打光流程与权重须另验；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 接触阴影·落位真实 · light_contact

分类：灯光质感；用途：reference-edit；参考：PS04、PS08。

素材：真实商品原图；接受受控光照变化，不能同时宣称全部像素不变。

变量示例：subject=the real product in image 1；backdrop=a quiet neutral background。

```text
Reference-guided commercial edit of {subject} in {backdrop}. Match the scene's dominant light direction. Add a tight dark contact region immediately beneath the object, softening and lightening with distance. Keep the shadow on the same support plane and avoid a detached drop-shadow sticker. Retain the reference identity, real visible construction and approved color relationships. Make only the requested presentation change, and compare the final image with the source. Written preservation instructions are not pixel-level evidence.
```

验收：检查高光方向、阴影接触、边缘光和背景一致性；重打光流程与权重须另验；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 标题层级·主副文案 · type_hierarchy

分类：文字排版；用途：text-to-image；参考：PS01、PS10。

素材：已核实短标题；此卡用于文字生成试验，不代替精确排版验收。

变量示例：headline=Bring Movie Night Home；accent=#9D00FF。

```text
An original minimalist commerce poster study. Render the exact headline "{headline}" once in bold clear sans-serif lettering, using {accent} as the accent color. Place the headline in the upper left, a quiet supporting line area beneath it, and a clear image area in the lower right for later real-product compositing. Keep letter spacing balanced and contrast strong. Add no specifications, certification seals or invented brand marks. Treat the generated text as a draft for character-by-character review.
```

验收：检查拼写、数字、单位、对齐、手机缩略图可读性和遮挡；排版意图不是现有版式引擎新增功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

生图文字实验：支持文字的模型也可能错字；保留原文逐字校对，失败时改真实字体排版。

### 栅格布局·留白与安全边距 · type_grid

分类：文字排版；用途：layout-only；参考：PS10。

素材：已核实英文文案、真实参数、可用字体和图像；精确文字建议确定性排版。

变量示例：headline=Bring Movie Night Home；accent=#9D00FF。

```text
LAYOUT BRIEF ONLY, not a diffusion workflow. Approved headline: "{headline}". Use {accent} as a controlled accent while keeping body text legible. Align text and image blocks to a consistent grid with generous outer margins. Reserve the product silhouette as an exclusion area, and keep supporting lines away from bright background details. Place exact text in an editable typography layer, separate from product pixels. Treat all ratios and spacing as a design brief to verify in the actual renderer; no performance or certification claim may be invented.
```

验收：检查拼写、数字、单位、对齐、手机缩略图可读性和遮挡；排版意图不是现有版式引擎新增功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 参数图标·数据与标签 · type_badges

分类：文字排版；用途：layout-only；参考：PS10。

素材：已核实英文文案、真实参数、可用字体和图像；精确文字建议确定性排版。

变量示例：headline=Bring Movie Night Home；accent=#9D00FF。

```text
LAYOUT BRIEF ONLY, not a diffusion workflow. Approved headline: "{headline}". Use {accent} as a controlled accent while keeping body text legible. Arrange a small set of verified parameter badges with consistent icon weight, number alignment and label spacing. Every number must retain its verified unit; icons must not imply certification or unsupported features. Place exact text in an editable typography layer, separate from product pixels. Treat all ratios and spacing as a design brief to verify in the actual renderer; no performance or certification claim may be invented.
```

验收：检查拼写、数字、单位、对齐、手机缩略图可读性和遮挡；排版意图不是现有版式引擎新增功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 移动端·缩略可读 · type_mobile

分类：文字排版；用途：layout-only；参考：PS10。

素材：已核实英文文案、真实参数、可用字体和图像；精确文字建议确定性排版。

变量示例：headline=Bring Movie Night Home；accent=#9D00FF。

```text
LAYOUT BRIEF ONLY, not a diffusion workflow. Approved headline: "{headline}". Use {accent} as a controlled accent while keeping body text legible. Optimize the layout for reduced display size: short headline, limited copy, distinct contrast and sufficient line spacing. Inspect the actual thumbnail rather than trusting large-canvas readability. Place exact text in an editable typography layer, separate from product pixels. Treat all ratios and spacing as a design brief to verify in the actual renderer; no performance or certification claim may be invented.
```

验收：检查拼写、数字、单位、对齐、手机缩略图可读性和遮挡；排版意图不是现有版式引擎新增功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 双语排版·语义一致 · type_bilingual

分类：文字排版；用途：layout-only；参考：PS10。

素材：已核实英文文案、真实参数、可用字体和图像；精确文字建议确定性排版。

变量示例：headline=Bring Movie Night Home；accent=#9D00FF。

```text
LAYOUT BRIEF ONLY, not a diffusion workflow. Approved headline: "{headline}". Use {accent} as a controlled accent while keeping body text legible. Set two approved language versions in clearly separated blocks with comparable hierarchy. Preserve brand spelling and technical units; review translations manually and avoid awkward letter-by-letter wrapping. Place exact text in an editable typography layer, separate from product pixels. Treat all ratios and spacing as a design brief to verify in the actual renderer; no performance or certification claim may be invented.
```

验收：检查拼写、数字、单位、对齐、手机缩略图可读性和遮挡；排版意图不是现有版式引擎新增功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 结构标注·引线不遮挡 · type_callouts

分类：文字排版；用途：layout-only；参考：PS10。

素材：已核实英文文案、真实参数、可用字体和图像；精确文字建议确定性排版。

变量示例：headline=Bring Movie Night Home；accent=#9D00FF。

```text
LAYOUT BRIEF ONLY, not a diffusion workflow. Approved headline: "{headline}". Use {accent} as a controlled accent while keeping body text legible. Place verified labels outside the original product silhouette. Keep leader lines short, avoid crossings and anchor each line to the actual visible feature. Unknown details remain unlabeled. Place exact text in an editable typography layer, separate from product pixels. Treat all ratios and spacing as a design brief to verify in the actual renderer; no performance or certification claim may be invented.
```

验收：检查拼写、数字、单位、对齐、手机缩略图可读性和遮挡；排版意图不是现有版式引擎新增功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 对比版式·不伪造数据 · type_comparison

分类：文字排版；用途：layout-only；参考：PS10。

素材：已核实英文文案、真实参数、可用字体和图像；精确文字建议确定性排版。

变量示例：headline=Bring Movie Night Home；accent=#9D00FF。

```text
LAYOUT BRIEF ONLY, not a diffusion workflow. Approved headline: "{headline}". Use {accent} as a controlled accent while keeping body text legible. Use a restrained two-column comparison only when both sides have substantiated data. Align identical metrics and units, cite the measurement context separately and avoid fabricated bars or unfair scales. Place exact text in an editable typography layer, separate from product pixels. Treat all ratios and spacing as a design brief to verify in the actual renderer; no performance or certification claim may be invented.
```

验收：检查拼写、数字、单位、对齐、手机缩略图可读性和遮挡；排版意图不是现有版式引擎新增功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

本地原创电商配方，参考资料方法后重新编写，非官方逐字模板或实测承诺。负面提示不跨模型套用；镜头参数、材质及排版描述只是意图。

### 文字替换·逐字校对 · type_textedit

分类：文字排版；用途：reference-edit；参考：PS03、PS10。

素材：图1：有权编辑的海报原图；明确只替换的文字位置；经核对的短标题；Logo和参数层不在替换范围。

变量示例：headline=Bring Movie Night Home；accent=#9D00FF。

```text
In image 1 replace only the main headline with the exact text "{headline}". Keep all other wording, the product, the logo, the layout and the background unchanged in appearance. Use {accent} for the replacement headline, matching the original text block baseline, font character and relative scale. Do not add explanatory words or alter numerical specifications. Review the replacement character by character; this instruction does not prove pixel-level preservation.
```

验收：检查拼写、数字、单位、对齐、手机缩略图可读性和遮挡；排版意图不是现有版式引擎新增功能；先生成一张预览并逐项对照；不自动排队，不把未验证示意当作商品事实。

参考图文字编辑实验，不把语言保持指令当像素证明；关键文案可回到确定性排版。
