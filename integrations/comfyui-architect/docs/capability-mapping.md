# 节点能力映射

运行时映射位于：

```text
workflow_architect/templates/capabilities.yaml
```

文件使用 JSON 语法；JSON 是 YAML 1.2 的子集，因此无需引入 YAML 解析依赖。

## 添加节点

在目标能力的 `candidates` 中加入真实 `object_info` 返回的节点类名：

```json
{
  "virtual_tryon": {
    "required": false,
    "candidates": ["来自真实环境的节点类名"],
    "search_terms": ["Manager 中可查询的关键词"]
  }
}
```

候选顺序即优先级。不要填写界面显示标题，也不要凭文档或记忆猜测类名。应在
ComfyUI 中请求 `/object_info` 或点击面板“扫描节点”，确认类名和端口。

## 需要记录的信息

为第三方节点增加映射前，应在对应具体模板和项目文档中记录：

- 节点类名及提供它的仓库、版本或提交。
- 能力标签。
- 输入名、输入类型和必填状态。
- 输出索引、输出名和类型。
- 默认参数及允许范围。
- 兼容的 ComfyUI / 前端版本。
- 模型分类、文件名和获取方式。
- 节点、模型、权重的许可证与商业使用备注。

能力文件只负责“类名可否承担能力”的候选匹配。具体输入/输出连线属于经验证的
具体模板，避免规划器内出现大量硬编码节点端口。

## 当前运行时能力

2.0 已配置的基础候选包括：

- `image_loader` → `LoadImage`
- `image_saver` → `SaveImage`
- `image_composite` → `ImageCompositeMasked`
- `mask_grow`（可选）→ `GrowMask`
- `image_scale` → `ImageScale`
- `checkpoint_loader` → `CheckpointLoaderSimple`
- `text_encoder` → `CLIPTextEncode`
- `empty_latent` → `EmptyLatentImage`
- `sampler` → `KSampler`
- `vae_encode` / `vae_decode` 及分块版本
- `upscale_model_loader` / `image_upscale_model`

虚拟换装、人物分割、服装蒙版、受保护区域和边缘融合登记了常见候选类名与 Manager
搜索关键词，但只有当前 `/object_info` 中真实存在的类名才会被匹配。测试目录的
`Fixture*` 名称不是运行时能力，不能复制到生产配置。

修改能力文件后重启 ComfyUI，或在代码热加载完成后点击“扫描节点”刷新内存缓存。
