# 最小真实供应商验收的离线预检

当前状态（2026-11-14）：0027原子预算绑定已在后续完整原生CI验证；0034多计价单位授权/独立恢复撤销、0038 mock领取隔离及0037离线账单证据恢复也已通过，见[当前任务状态](TASK_STATUS.md)。下文早期“原生待CI”记录仅为当时状态。真实执行入口依旧缺失，不能通过省略preflight开启调用；费用授权配置通过不构成真实供应商验收。

`python scripts/provider_smoke.py --config work/private-smoke/review.json --preflight` 只检查授权范围和样本，不读取模型密钥、不请求供应商、不创建业务任务。返回 `SCOPE_REVIEW_READY` 仅表示配置可供审阅，不是费用授权，也不是 `PROVIDER_VERIFIED`。省略 `--preflight` 固定返回 `WAITING_EXTERNAL`/exit3；当前没有启用付费执行入口。

将 `ops/provider-smoke.example.json` 复制到忽略的私有目录后编辑。示例故意过期、摘要为占位值、样本不存在；金额只是格式示例，不是供应商价格。配置不允许 API key 或其他额外字段。实际密钥不应进入配置、命令行、Git 或公开报告。

schema_version=2配置固定授权ID、用户/产品/标准化版本及完整报价input_sha256、审阅人、UTC起止时间（最多24小时）、源文件路径和SHA-256、完整生成参数/保护矩形和平台目标、精确模型、各一次生成/检测调用上限、USD各步骤及合计上限。金额必须是正定点十进制字符串，合计不得小于两个步骤上限之和；浮点数、科学记数法、布尔调用数、重复JSON字段及范围扩张均拒绝。规范化配置摘要随请求和预算变化而变化，后续执行必须绑定已审阅摘要，不能只凭文件名引用可变配置。

预检读取有界样本，校验原始字节摘要，再经现有受资源限制的独立解码进程确认已是canonical PNG。pixel_exact至少1区、输出尺寸必须与源一致，源矩形不得越界；BFL请求构造使用已验证内联图像字节，配置不能将localhost或私有S3 URL当作供应商图片输入。公开结果只含摘要、尺寸、模型、调用及金额上限、到期时间；错误仅输出类型，不回显配置或Pydantic输入。

0027已新增费用上限与持久意图同事务绑定候选（见PROVIDER_BUDGET，原生待CI）。尚未完成：实时Worker路由、已知ID只查询恢复、一次性费用执行及完整归档/回贴/最终检测/费用交叉验收。配置中的金额不能证明供应商收费封顶；还需可核验价表与成本来源，BFL credits不能直接当USD，Gemini未知费用不能当0。超时/未知必须停止新submit并保留预算占用，不能因预检成功就绕过这些条件。当前工程AGENTS禁止实际付费调用，保持这一限制。

当前授权配置单元行为/负例，包含真实独立解码和CLI拒绝/脱敏；这些是离线配置证明，不能替代实际模型样本或PG原子预算竞争测试。真实样本、模型次数/费用范围、安全配置及外部验收仍按P4-03记录。

## 百炼完整绑定的只读审阅

仅本机文件预检不足以证明数据库报价、预算和价格属于同一任务。新增显式模式：

```text
python scripts/provider_smoke.py --config .local/private-scope/authorization.json --preflight --bound-review --runtime .local/private-scope/runtime.json --bailian-connection .local/bailian-csv-connection/connection.json --quote <报价UUID>
```

需要专属Ops身份ADMIN_DATABASE_URL。命令先以现有隔离解码器核样本原文与规范SHA，再在一个只读REPEATABLE READ数据库事务里读取各受限上下文。交叉核对完整报价/授权SHA、运行配置/停用、本机百炼连接指纹、未使用预算及逐币种上限、两份不可变费率/商户/端点/有效期/完整计量和请求前预测。报价未绑定完整费率时拒绝，不从网页猜测费率。

默认要求两份reviewed_price；合成CI演练才显式使用--offline-fixtures，报告review_mode明确不同。默认不读凭据；--check-credentials额外读取两个所选本机密钥并只报告布尔匹配，不能据此证明远端认证。

BOUND_PROTOCOL_SCOPE_REVIEW只代表当次元数据快照与样本审阅通过，不是执行授权或付费开关。不会登记预算、创建任务/意图、写成本或请求供应商。事务结束后停用/到期/使用状态仍可能变化，真正调用意图必须重新通过既有PG原子检查。原价格证据的当前S3可读性、真实商户/兼容性、质量和账单仍独立待验。

可选--check-price-evidence会在上述元数据检查全部通过后，读取同一数据库快照中两份已冻结费率的私有证据位置，使用S3_ENDPOINT/S3_BUCKET/S3_REGION/S3_ACCESS_KEY/S3_SECRET_KEY回读精确对象版本并检查原SHA。沿用Ops已有费率读权限；端点继续执行项目HTTPS/回环HTTP校验。读取有界20MiB，跨桶或非费率归档路径在发请求前拒绝。默认不请求S3，模型密钥读取仍由独立--check-credentials控制。输出仅有每步布尔结果，不包含正文、bucket/key或存储密钥；回读成功只证明当次对象可读，不证明资料真实、费率仍适用于远端商户或未来可用性。
