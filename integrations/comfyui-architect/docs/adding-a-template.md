# 添加经验证的具体模板

具体模板放在：

```text
workflow_architect/templates/concrete/*.json
```

插件按文件名顺序选择第一个与任务、运行模式和当前注册表兼容的模板。V2.0 不会自由生成节点图，
也不会根据第三方节点名称猜测端口。

## 前置条件

1. 在目标 ComfyUI 环境中人工搭建并成功运行参考 workflow。
2. 保存原始 workflow JSON 和 `/object_info` 快照用于审计。
3. 确认所有节点类、端口、参数与模型来自该真实环境。
4. 核查节点、模型和权重许可证。
5. 把类名加入 `capabilities.yaml` 的正确能力候选。

## 模板格式

```json
{
  "kind": "concrete",
  "verified": true,
  "template_id": "vendor_tryon_comfy_x_y_v1",
  "task": "virtual_tryon",
  "required_models": [
    {"folder": "checkpoints", "name": "真实文件名.safetensors"}
  ],
  "nodes": [
    {
      "key": "person",
      "capability": "image_loader.person",
      "title": "人物图片",
      "stage": "inputs",
      "group_title": "01 人物与服装输入",
      "position": [0, 0],
      "inputs": {"image": "${person_image}"}
    },
    {
      "key": "implementation",
      "capability": "virtual_tryon",
      "stage": "generation",
      "group_title": "04 虚拟换装",
      "position": [800, 100],
      "inputs": {
        "真实人物端口名": {"link": ["person", 0]},
        "真实宽度参数名": "${width}",
        "真实高度参数名": "${height}"
      }
    }
  ]
}
```

节点可以使用 `capability` 让注册表选择候选，也可以写 `class_type` 固定到一个
已经验证的真实类。所有 `key` 必须唯一。连线格式为：

```json
{"link": ["源节点 key", 0]}
```

第二项是源输出索引。可用占位符：

- `${person_image}`
- `${garment_image}`
- `${source_image}`
- `${width}`
- `${height}`
- `${format}`
- `${filename_prefix}`
- `${positive_prompt}` / `${negative_prompt}`
- `${seed}` / `${steps}` / `${cfg}` / `${denoise}`

模型依赖建议写成 `{folder, name}`。支持的安全 folder 为 `checkpoints`、
`diffusion_models`、`unet`、`vae`、`clip`、`controlnet`、`loras` 和
`style_models`。插件通过 ComfyUI `folder_paths.get_filename_list` 检查精确文件名，
不会扫描任意路径。

## 验证清单

1. 用保存的 `object_info` 增加注册表测试。
2. 编译两次并比较 JSON 完全相同。
3. 校验节点类、必填输入、输出索引和类型。
4. 确认 7 个中文分组与受保护区域说明。
5. 在目标 ComfyUI 中插入到非空画布，检查不重叠和撤销。
6. 实际执行至少一组已获授权的测试图片。
7. 人工检查脸部、头发、耳朵、头饰、手部、衣领和背景。
8. 记录节点/模型版本、已知失败姿势和许可证。

只有完成以上流程后才设置 `verified: true`。不要提交模型权重、API Key、本机绝对
路径或无法再现的模板。
