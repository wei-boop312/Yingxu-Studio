# 百炼国内图片编辑接入

当前适配固定使用北京业务空间的 `qwen-image-3.0`，检测保留独立 Gemini/Hubway 身份。百炼适配仅接受显式 MockTransport，默认 Fake 不变；创建密钥和离线验收不表示真实出图已接通。账号此前提示余额不足，尚未充值或调用真实供应商。

## 本机配置密钥

在自己的 PowerShell 执行下面命令，按遮蔽提示输入控制台密钥，不要发送到聊天或提交到仓库：

```powershell
& "C:/Users/MECHREVO/Documents/Codex/2026-11-12/ai/outputs/visual-production-platform/scripts/configure_provider_key.ps1" -Provider bailian
```

工具保存本机 Windows 用户/当前进程变量 `DASHSCOPE_API_KEY`，不请求模型；已有值不会无提示替换。环境变量不是加密保险库。若控制台已无法再次查看密钥，需要在控制台创建新的专用密钥，再本机输入。

本机连接预检使用下面命令；将示例域名替换为实际北京业务空间域名，输出目录必须全新：

```powershell
.venv/Scripts/python.exe scripts/bailian_connection.py --prepare-local --endpoint-origin https://ws-EXAMPLE.cn-beijing.maas.aliyuncs.com --output-dir .local/bailian-connection
```

该命令只读本机密钥、写连接身份与指纹，不保存密钥、不发 HTTP、不建立执行授权。`--config .local/bailian-connection/connection.json` 默认不读凭据，附加 `--check-credentials` 才比较本机密钥指纹。用户提供本机 CSV 路径后，密钥已保存本机用户环境，回读一致；`.local/bailian-csv-connection/connection.json` 本地预检 `credential_matches=true`。配置仅存身份与指纹，不存密钥。随后显式只读目录GET已返回HTTP200并列出目标模型；这只证明目录认证观察。权限查询已授权筛选为空，调用权限仍未确认；没有发送推理请求，也不能据此确认余额、实际费率或出图成功。

## 协议与验收范围

按[阿里云官方接口文档](https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference)，适配使用异步 POST `/api/v1/services/aigc/image-generation/generation`，带 `X-DashScope-Async: enable`，再以已知 task_id 查询 `/api/v1/tasks/{task_id}`。不混用同步或 OpenAI 兼容接口。项目限定最多三张输入图、输出面积 512² 至 4MP；下载隔离认证信息、禁止重定向，并独立规范化 PNG。

显式运行身份采用 schema2，百炼演练预算采用 schema4，生成 CNY 与检测 USD/Hubway 单位分别约束，不换汇或合并。不能复用旧 BFL 预算。原始提交与成功查询回执分别保存到不可变私有 PG 记录，查询计量绑定原下载和规范图 SHA；缺失或不合规计量保持 unknown。

价格必须经过私有证据审阅发布，再以生成/检测两个版本 ID 与 SHA 显式绑定到报价。百炼四档图片计量不得套用 BFL 积分或 Gemini token。异步 POST/PENDING 不提前估算；成功查询有完整计量后才派生精确估算。缺失计量或超过 CNY 上限时停止后续检测、保留图片和冻结。估算不是账单，内部积分不是供应商人民币支出。

最新完整通过基线为 e9b8943e / CI43636041162：217 单元、92 契约、270 原生、4 普通与 11 受控浏览器，以及 Compose、独立恢复和离线交付包验证。百炼非空冷恢复已由本轮独立验收证明，后续源码仍须自身 CI；详细候选及失败记录见 STATUS.md。

## 尚需完成

本机密钥已配置，业务空间连接指纹预检通过，生成/可选检测模型的只读目录认证已观察；用户商品原图已保留并准备2000²私有副本。商户实际价格证据与预算、真实模型输出质量、真实账单核对及部署仍未完成。离线测试不替代这些验收；现阶段不开放真实付费调用。可选检测的登记与费率候选详见BAILIAN_VISION.md，不会自动替换Hubway或既有任务。

## 请求前价格预测

0062候选新增只读 `python scripts/provider_budget.py project-generation --user <用户UUID> --quote <报价UUID>`，需要本机Ops数据库身份。按冻结请求的单张输入/输出面积和已审阅价格计算CNY预测；不读模型密钥、不请求模型、不创建成本账本或费用授权。未知费率返回unknown，不当作免费。PG在生成预算hold前再次核验，已知超上限时拒绝整个意图事务；真实供应商计量仍须从成功查询独立归档与估算，预测不等于账单。

0063候选增加真实原图前置检查：每边1–8000像素、宽高比最多10:1、项目保守大小上限10,000,000字节，不能用平台通用8192边长上传能力推断百炼能接收。PG依据所属不可变原图而非调用者JSON，在报价和任务写入前检查；请求适配器再次核规范PNG头与CRC。普通上传仍保留原能力，只有百炼路径应用此限制。

## 显式远端只读检查

```text
python scripts/bailian_metadata.py --config .local/bailian-csv-connection/connection.json --probe --output-dir .local/new-catalogue-review
python scripts/bailian_metadata.py --config .local/bailian-csv-connection/connection.json --kind permissions --probe --output-dir .local/new-permission-review
```

不加--probe时不读配置/密钥或请求远端。显式操作只请求官方GET /api/v1/models，或GET /api/v1/models/permissions的精确模型/AUTHORIZED/INFERENCE筛选；各一次，不跟随跳转、不改授权、不提交推理、不认为免费额度存在。响应有界2MiB；原文与审核摘要只写全新忽略目录，不要上传。401/403代表该元数据操作未获准，不自动扩大密钥范围；空筛选也不等同于断言实际推理无权。目录认证、工作空间授权、密钥自身范围、余额、真实出图及费用必须分别核验，任何成功都不会开启执行。

空的已授权筛选不能直接当作拒绝推理：官方权限文档说明默认业务空间与子空间的显式授权机制不同。控制台目标卡片目前可见模型/价格及体验入口，仍不等于实际推理成功，也未因此修改任何授权或密钥。新增可选检测协议见BAILIAN_VISION.md，当前仅离线适配。
