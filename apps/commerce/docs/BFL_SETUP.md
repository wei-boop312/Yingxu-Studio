# BFL 账号和本机密钥配置

1. 打开[官方控制台](https://dashboard.bfl.ai)，Login后选择Register，按页面完成邮箱注册/验证或已有账号登录。
2. 进入项目的API Keys，选择Add Key，取名“跨境电商视觉平台”。官方说明该密钥只显示一次；在本机保存，不发送到聊天或提交Git。账号/密钥流程见[官方账户说明](https://help.bfl.ai/articles/2699147243-managing-your-account)。本工程不会代用户注册、接受协议、充值或购买额度。
3. 在本机PowerShell的项目目录运行`./scripts/configure_provider_key.ps1 -Provider bfl`，按遮蔽提示粘贴密钥。脚本仅写当前Windows用户及当前PowerShell的BFL_API_KEY，不输出密钥、不写仓库文件、不调用接口。已有用户变量默认加载同一把密钥；确需更换时显式加-Replace，更换后必须重新审阅运行配置及费用范围。Hubway密钥可用同一工具的-Provider hubway配置。

仅查看工具说明可运行`./scripts/configure_provider_key.ps1 -Provider bfl -ExplainOnly`，此模式不读取或修改密钥。脚本应由用户在自己的PowerShell中交互运行；不要把密钥作为命令行参数。应用优先读取明确的进程环境变量；Windows下进程变量缺失时可读取这三个指定名称的用户变量，显式空值不回退到旧密钥。Linux服务使用其自身私有环境。密钥未用于默认Fake消费者。

账号和密钥创建后，可单独进行只读账户检查：先创建私有父目录`.local/provider-account`，再运行`python scripts/provider_account.py --bfl-credit-balance --output-dir .local/provider-account/check-001`。这个显式入口仅发送一次GET到官方`https://api.bfl.ai/v1/credits`，不上传图片，不发送生成POST，不重试或跟随重定向；原JSON和精确十进制余额凭据只保存在新的私有目录，公开输出不含余额数值或密钥。目录必须在本工程忽略的.local或work内，禁止覆盖。

HTTP200及符合结构的credits只说明观察到该端点的账户响应，不证明模型权限、样本质量、商户归属或费用已授权。余额单位保留BFL_CREDIT，不当作USD、人民币或项目费用上限；零余额是有效观察，不自动充值。读取规范见[官方OpenAPI](https://api.bfl.ai/openapi.json)。未来真实生成还需用户明确的样本、次数/费用上限、运行绑定与独立验收，当前AGENTS禁止实际付费模型调用。
