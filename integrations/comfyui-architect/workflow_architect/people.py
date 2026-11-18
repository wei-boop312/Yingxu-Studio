"""Conservative people-intent routing, not a face/try-on execution engine."""
import re


def people_request(text):
    if not isinstance(text, str):
        return None
    if re.search(r"换脸|替换.{0,12}(?:脸|面部)|(?:脸|面部).{0,12}(?:替换|换成)|face[ _-]?swap", text, re.I):
        kind, title = "face_swap", "参考人物换脸"
        roles = [("target_person", "待处理人物图"), ("identity_reference", "身份参考图")]
        limits = ["尚无通过本机验收的换脸执行模板。", "使用真实人物肖像前需确认本人授权，不承诺身份一致性。"]
    elif re.search(r"换装|换衣|试衣|试穿|换上.{0,12}(?:上衣|衣服|服装)|(?:替换|换成|更换).{0,18}(?:泳衣|泳装|比基尼)|virtual.?try.?on", text, re.I):
        kind, title = "virtual_tryon", "人物换装／试穿"
        roles = [("person", "人物原图"), ("garment", "服装参考图")]
        limits = ["通用规划入口不自动执行换装。请使用独立“Klein双图换装（实验）”入口选择人物图和服装图；尚未通过商品质量验收。", "实验模板不保证衣物、身份或蒙版外像素保真，不自动批量或改用付费API。"]
    elif re.search(r"同一(?:个|位)?(?:人物|模特)|保持.{0,6}(?:身份|人脸|脸部|五官)|(?:脸部|五官|身份)不变|参考.{0,12}(?:人物|人脸|模特|身份)|identity.?reference|consistent.?character", text, re.I):
        kind, title = "identity_reference", "参考人物一致性"
        roles = [("identity_reference", "经授权的身份参考图")]
        limits = ["普通文生图提示词不能保证同一人物；请使用独立“PhotoMaker身份参考（实验）”入口检查专用图，不自动回退通用模板。", "身份参考效果仍需核验，不能仅靠“保持脸部”字样视作已保护。"]
    elif re.search(r"人物模特|电商.{0,6}模特|(?:生成|制作|创建|画|做).{0,18}(?:人物|模特|人像)|portrait|fashion.?model", text, re.I):
        kind, title = "portrait_generation", "人物／电商模特生成"
        roles = []
        limits = ["可使用现有通用文生图模板，但不是人物专用模型或质量验收。", "不保证固定身份、精确服装款式或商品细节；需要这些能力应明确提供参考要求。"]
    else:
        return None
    batch = bool(re.search(r"一组|批量|多张|多套|[2-9二三四五六七八九十]\s*张", text))
    if batch:
        limits.append("当前知识辅助只编译单任务，不会把“一组/批量”自动当作已完成批量生产。")
    report = {"kind": kind, "title": title, "required_inputs": [{"role": role, "label": label} for role, label in roles],
            "reference_only": kind != "portrait_generation" or batch,
            "batch_requested": batch, "batch_execution_supported": False,
            "limitations": limits, "execution_authorized": False}
    if kind == "virtual_tryon":
        two_piece = bool(re.search(r"两件式|两件套|分体(?:式)?泳|比基尼|bikini|two[ -]?piece", text, re.I))
        low_waist = bool(re.search(r"低腰|low[ -]?(?:rise|waist)", text, re.I))
        report["garment_structure"] = {
            "two_piece_requested": two_piece,
            "low_waist_requested": low_waist,
            "structure_fidelity_verified": False,
            "plugin_execution_supported": False,
            "required_reviews": ["版型与覆盖范围", "领口/肩带/袖型", "图案与材质", "手部和衣物边界", "蒙版外原图尺寸像素保护"],
        }
        if two_piece or low_waist:
            limits.append("两件式/低腰版型尚未通过本地商品保真验收；不能以改色、原图回填或运行完成宣称试穿成功。")
            limits.append("CatVTON示例服装类别主要选择蒙版，不是低腰线控制参数；不得自动回退普通重绘、改色或付费API。")
    return report


def people_dependencies(person, snapshot):
    """Read registry metadata only; never instantiate a loader or fetch weights."""
    def choices(node_name, input_name):
        node = snapshot.nodes.get(node_name)
        entry = node.input_by_name(input_name) if node else None
        return list(entry.choices) if entry else []

    if person["kind"] != "identity_reference":
        return {"registry_generation": snapshot.generation, "routes": [], "execution_authorized": False,
                "notice": "当前仅为身份参考路线提供逐项依赖诊断；不据节点数量推断其他人物能力。"}
    reports = []
    face_nodes = ["IPAdapterModelLoader", "IPAdapterFaceID", "IPAdapterInsightFaceLoader", "CLIPVisionLoader", "CheckpointLoaderSimple"]
    face_weights = [name for name in choices("IPAdapterModelLoader", "ipadapter_file") if re.search(r"faceid", name, re.I)]
    clip_weights = [name for name in choices("CLIPVisionLoader", "clip_name") if re.search(r"ViT.H.14.*s32B.b79K", name, re.I)]
    reports.append({"route": "ipadapter-faceid", "title": "IPAdapter FaceID 身份参考",
                    "missing_nodes": [name for name in face_nodes if name not in snapshot.nodes],
                    "weight_options": {"faceid_adapter": face_weights, "clip_vision_vit_h": clip_weights},
                    "missing_weights": ([] if face_weights else ["FaceID专用适配权重（普通IPAdapter Plus不替代）"]) + ([] if clip_weights else ["匹配的CLIP Vision ViT-H"]),
                    "unchecked": ["InsightFace实际本机权重与运行依赖（预设枚举不证明文件存在）", "FaceID变体与checkpoint/LoRA/CLIP Vision逐项兼容", "权重许可、授权肖像与实际结果"],
                    "template_available": False, "runtime_verified": False, "execution_authorized": False})
    photo_weights = choices("PhotoMakerLoader", "photomaker_model_name")
    identity_lora = "workflow_architect_photomaker_v1_lora.safetensors"
    photo_loras = [name for name in choices("LoraLoaderModelOnly", "lora_name") if name == identity_lora]
    reports.append({"route": "photomaker", "title": "PhotoMaker 参考路线",
                    "missing_nodes": [name for name in ["PhotoMakerLoader", "PhotoMakerEncode"] if name not in snapshot.nodes],
                    "weight_options": {"photomaker": photo_weights, "extracted_identity_lora": photo_loras},
                    "missing_weights": ([] if "photomaker-v1.bin" in photo_weights else ["PhotoMaker v1权重"]) + ([] if photo_loras else ["从同一官方v1文件提取的身份LoRA"]),
                    "unchecked": ["模型版本、checkpoint与提示词触发规则兼容", "本机运行与人物一致性结果", "权重许可和肖像授权"],
                    "template_available": False, "experimental_template_available": True,
                    "experimental_entry": "PhotoMaker身份参考（实验）", "runtime_verified": False, "execution_authorized": False})
    return {"registry_generation": snapshot.generation, "routes": reports, "execution_authorized": False,
            "notice": "只读节点/模型选择元数据，不加载或自动下载；列出权重不证明文件完整、匹配或可执行。"}
