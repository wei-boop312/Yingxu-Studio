# PhotoMaker v1专用身份参考 · 未发布实验分支

## 最新实测结论（2026-10-16，覆盖下方早期准备状态）

用户已授权下载/安装及自有合成肖像小样。固定官方权重经公开镜像转Hugging Face CDN下载，934103417字节，流式/落盘SHA与官方记录一致；没有改TLS、全局环境或使用凭证。从其中lora_weights本地无损提取1120张量到185975712字节safetensors，逐张量相等，派生SHA42f77b9aaa1a7049c9b59a6707b4bf43e1db842145a3d910f0ecda0ca1baaf88。原.bin保留。

第一张原9节点图漏LoRA，虽运行成功但不宜评价身份质量。官方pipeline确实加载id_encoder和lora_weights；修为**10节点**，新增LoraLoaderModelOnly并让KSampler消费其MODEL输出。对照图保持原提示/seed/16步，只补LoRA1.0，运行成功但画面明显异常；最后一组主体man、LoRA0.6、24步、补正负提示也完成，仍有过度灰化/裁切/边缘纹理问题。第三组是多变量调参，不能据此证明单一根因。

三组均为512×512、自有合成成年肖像，实际耗时26.35/40.30/61.77秒；这些是本机具体观察，不是普遍性能/显存保证。输出质量**未通过**，不用于生产、不发布到日常插件、不推荐客户任务；在线维持2.4.2原功能。工作区模板、接口和实验UI仅保留研究，返回quality_approved=false，编译通过不证明身份保持。

当前具体模型选择须明确SDXL基础模型、v1编码器与本地提取LoRA三项，参考图和主体词另明确选择。默认24步/LoRA0.6，允许16..32步、强度0..1；需要原registry/template期待，不接受环境漂移。界面尚未实际浏览器验收，只有模拟DOM测试。以下9节点/未下载/尚未接LoRA等描述保留为早期调查，不能替代本段实际结论。

## 实现与隔离

工作区新增identity.py、templates/experimental/photomaker_v1_reference.json及POST /workflow-architect/people/identity/prepare接线；**没有复制到日常安装目录，当前在线2.4.2无此新接口**。待模型下载获准与验证后再合并发布，避免再为不完整模型路线要求用户重启。

这是单条具体静态图，不是新增一轮抽象设计：LoadImage→PhotoMakerEncode；PhotoMakerLoader与SDXL Checkpoint/CLIP→正向身份条件；负向CLIPTextEncode、EmptyLatentImage→KSampler→VAEDecode→SaveImage。共9节点，输出单张候选。专用模板位于experimental，不混入82条通用知识索引，不误替换普通文生图；内部复用原text_to_image编译规格只是类型投影，公开路线始终photomaker-v1-native。

原模板verified字段在旧编译器中仅表示允许静态校验；新增runtime_verified=false/review_scope明确没有推理或质量证据。不把此布尔值当已验收身份一致性。

## 关键正确性

按本机comfy_extras/nodes_photomaker.py实际源码与object_info签名，PhotoMakerLoader输出PHOTOMAKER，PhotoMakerEncode接photomaker/image/clip/text并输出CONDITIONING。本机代码寻找以空格独立分隔的photomaker，不能把官方Diffusers示例的img照搬。前缀固定a photograph of a person photomaker，用户描述不重复该独立词。

显式build_draft=true、肖像授权自我确认、真实LoadImage选项、精确模型对sd_xl_base_1.0.safetensors + photomaker-v1.bin才进入编译；不自动选第一项或替换V2。两权重缺一或节点缺失直接dependency-required，无图。首次build缺依赖时只返回缺项，不构建占位图。默认读取依赖，不加载文件或网络。画布64..1024、8倍数、最多1MP，16步/单张；这些是实验上限而非8GB显存成功保证。

明确限制：原生Loader读取官方权重中的id_encoder部分，不能声称与官方Diffusers完整适配器/LoRA流程等价；实际效果、checkpoint兼容和显存须后续验证。当前编译结果可能标旧结构execution_ready，**仅是静态类型校验**；外层与元信息execution_authorized=false/runtime_verified=false，不自动提交/prompt或改画布。不保证固定脸、衣物/商标保真或肖像授权真实性。

## 官方候选权重与许可记录

- [PhotoMaker官方权重固定版本](https://huggingface.co/TencentARC/PhotoMaker/blob/42a0f2dece1836c370906d0ec5143fabb92cbf42/photomaker-v1.bin)：934 MB，SHA256 529d503fa378bfb3a74e3384ab2064d7269d59f0638324555d22067c31e275bc。
- [官方许可证](https://huggingface.co/TencentARC/PhotoMaker/blob/main/LICENSE)：项目说明Apache-2.0及第三方组件例外，不据model-card标签保证整套底模/肖像/商标权利。
- [官方Diffusers示例](https://github.com/TencentARC/PhotoMaker)与[Comfy内置源码](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_photomaker.py)分别核对，不混用触发词。

候选及来源记录见研究归档photomaker-v1-route-manifest.json。本轮未下载权重、安装依赖、反序列化模型或执行生成；先取得用户对约934MB本地模型下载的明确许可。随后固定版本下载临时文件、核实际长度与SHA、再放入明确photomaker目录，不覆写现有不同权重。人物效果样例应由用户提供有权使用的参考图，或经确认使用合成肖像，不能偷偷选用户目录的私人照片。
