# 具体模板目录

V2.0 内置的基础任务模板只使用已验证的 ComfyUI 核心节点，仍不捆绑未经真实环境验证的第三方换装 workflow。把按
`docs/adding-a-template.md` 制作并验证过的 `kind=concrete` JSON 文件放在本目录，
插件会按文件名顺序选择第一个与当前节点环境兼容的模板。

不要把模型权重、API Key 或本机绝对路径放进模板。
