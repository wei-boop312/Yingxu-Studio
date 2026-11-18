# 供应商价格证据与精确估算

## 当前范围

0032为供应商价表提供私有不可变版本、S3证据归档、明确运营审阅和精确估算。它与用户积分的trial price_version分开；不修改固定任务报价、钱包、原始用量观察或已确认费用，也不启用真实模型。没有默认/种子真实价格。当前估算仍是estimated或unknown，actual_spending_verified始终false。0033已接入任务估算、超额阻断与带数据冷恢复，9174020整轮通过。

当前普通API仍创建offline Fake报价；新价表绑定通过可信协议演练报价验证。SDK互斥计量、任务估算及保守超额阻断已实现；真实商户配置选择、显式多单位授权、供应商账单确认及live接线是后续工作，不能用此候选宣称费用已核实。

## 私有发布与查看

需要非owner的ops_admin LOGIN（ADMIN_DATABASE_URL）。发布另需S3_ENDPOINT、S3_ACCESS_KEY、S3_SECRET_KEY、S3_BUCKET及可选S3_REGION。文档与原始证据保留在私有本地目录，不提交真实账单/商户材料。

```text
python scripts/supplier_pricing.py publish --document <private-price.json> --evidence-file <private-evidence> --attest-reviewed --operation-id <uuid> --reason <review-reason>
python scripts/supplier_pricing.py inspect --price <uuid>
python scripts/supplier_pricing.py estimate --price <uuid> --metering-context <private-usage.json>
```

发布必须明确审阅，证据1..20MiB且SHA与文档一致。先校验专属运营身份，再将证据按operation与SHA写入私有不可变对象；数据库发布、审计和唯一版本在同一事务。S3失败不产生价格记录；精确operation重放返回原版本，改文档/理由不能覆盖。API/Worker不能直接读取或发布价格表。CLI不输出对象key、签名URL或凭据。

价格文档采用严格schema_version=1：provider、固定model、精确endpoint_origin、不可逆merchant_fingerprint、billing_group、version、currency、UTC valid_from/valid_until、evidence_kind、evidence_sha256、complete_meter_coverage=true和rates。额外字段（包括API key）、布尔冒充整数、浮点价格、指数/NaN、重复计费项均拒绝。SQL再次独立校验，不能仅靠Python。每次人工核验有效期最长31天是本工程的保守运营规则；过期不自动续期，不代表供应商承诺31天不变价。

支持USD/CNY，以及明确标为BFL_CREDIT/HUBWAY_BALANCE_UNIT的非货币单位。没有内建credit、站内余额、人民币、美元或用户积分之间的兑换率。非货币单位的估算不能与USD预算比较，也不能冒充实际美元支出。

## 互斥计量与估算

rate包含measure、精确十进制字符串amount与正整数per。允许bfl_credit、input_tokens、output_tokens、cached_input_tokens、cache_write_tokens、thinking_tokens；生成只接受bfl_credit，视觉检测只接受token项。价格与数量都不使用二进制浮点。

计量上下文必须明确provider/model/endpoint_origin/merchant_fingerprint/billing_group、UTC observed_at及quantities。数量集合必须与价表项完全一致：缺失项不补0，多出项不忽略；未知新增计费维度、过期或任一身份不符均返回unknown且amount/currency为null。BFL credit数量须精确十进制字符串；token数量须非负整数，布尔、float与负数不是有效数量。

使用精确有理数/整数累加，合计后以整数除法向上取整到8位小数以避免有限精度吞掉微小尾数；溢出为unknown。即便所有明确数量为0且估算为0，仍不表示账单已确认或免费。缓存、思考、输入等计费项必须来自可核验的互斥计量，不能从totalTokenCount猜拆，也不假定SDK的prompt/candidate字段已覆盖全部供应商计费。当前不会把三项现有Gemini用量自动当作完整发票依据。

## 冻结身份与恢复

可信报价可在_execution.provider_price_versions按generate/detect冻结价格UUID及完整文档SHA，同时在provider_accounts冻结商户指纹/计费分组。PG逐项核对原价表、有效期、步骤、模型端点和账户；旧报价不随新价表改变。完整quote SHA继续被0027预算授权覆盖。

ProtocolProvider收到明确运行时商户范围后先验证结构（不允许密钥字段）并独立复制；每一阶段与报价中的范围精确比较。缺失/更换账户或组，先于任何新意图/POST被阻断；旧无账户范围的offline协议测试保持旧语义。该入口仍只接受MockTransport，不能用此配置绕过付费禁令。

备份引用收集包含价格证据对象，冷恢复校验原表/函数指纹后只改写恢复后的S3版本ID，保留价表正文/SHA/审计；新增独立恢复断言实际证据可读且SHA不变。原预算恢复撤销与检测降级重新审阅规则不变。

## 验证边界

本地新增10单元（合计121）、新增1协议契约（合计47）、Ruff与Linux/Windows各96生产/运营源码检查通过。8项PG/S3候选覆盖发布/实际归档、精确估算、并发幂等/不可变、审阅与S3失败拒绝、绕过Python的类型负例、权限、冻结报价与正确账户交付、错误账户零POST、错误账户/端点/SHA/过期报价拒绝；新增冷恢复价表证据实际读取。原生与整轮CI尚待执行，不将单元或SQL生成当作PG证明。

## 原始响应计量准备

Gemini HTTP响应在SDK转换数值前校验JSON与六项计数，缺失值不补0，布尔/数字字符串/浮点不能成为整数计费量；重复键、矛盾总数、未知计费字段、工具token或非标准serviceTier不标完整。异步响应使用每个submit独立ContextVar，禁止并发意图互换计量。SDK事实解析和原回执摘要不因此改成合规/费用确认。

[Google GenerateContent UsageMetadata](https://aistudio.google.com/api-reference/generate-content)说明prompt计数包含缓存，总量另含思考、工具和候选输出。因此后续互斥输入量必须扣除明确缓存项，思考不能静默漏掉；该字段语义依据不是Hubway真实兼容性或价表证明。当前只保存经原始响应核验的计量完整性，不写confirmed账单。

0032阶段实际全量证据：e714f7d / CI43495545487成功，121单元/47契约/153原生、4+7浏览器和完整Compose/独立恢复/重启/P5-02通过，包含supplier_price_archive_preserved实际冷恢复校验。原始计量a9cb71a及后续0033仍按各自提交验收，不将价表阶段的绿灯用于账单/live宣称。

## 0033任务估算与超额阻断候选

任务响应观察保持unknown/null，不覆盖原始记录；持有有效Worker租约者只能从已应用回执及不可变response_observation推导，不传入金额。新增规范化估算关联表把cost_record_kind=estimate、同用户/任务/意图的原始响应观察、原回执摘要和冻结价表UUID/SHA以复合FK绑定；货币值只保存在app.task_cost_records。发布新价表不能重写旧估算。

BFL数量来自已报告的BFL_CREDIT，必须有明确价表才能估算；Gemini必须有原始响应完整性、精确报告模型和六项明确整数计数，缓存从总输入扣除、思考独立计算；工具、缺项、未知新维度、模型不符或价表不覆盖全部互斥项都记录unknown/null，不补0。元数据缺失与供应商报不同模型均不触发新POST；报告也不会把明确不同模型的事实当作完整检查。

Python与PG分别以整数有理数累加，不经有限精度中间除法；最终向上取整到8位，溢出仍unknown。新增微小尾差边界单元验证超过普通Decimal上下文的正尾数不能被吞掉。没有二进制float或站内单位的隐式美元兑换。

超出步骤/合计授权，或明确绑定的价表无法与USD预算比较，将预算阻断；后续新付费意图与hold在同一事务拒绝，先前回执/归档可安全完成本地步骤，内部积分保留冻结等待人工结案。过期价表在第一步前拒绝并释放无付费意图的冻结；已有意图后过期保留冻结。预算原上限不增加，旧review-blocked不能绕过估算审阅阻断。此阻断是保守估算的风控，不证明真实发票超额。

```text
python scripts/supplier_pricing.py task-costs --task <uuid>
python scripts/provider_budget.py inspect --authorization <uuid>
```

私有输出含估算/未知金额、币种、价表来源、原因与ceiling_state；known_estimate_subtotal只是已知同币种估算的小计，不是全部实际支出。estimate_coverage_complete要求生成与检测两步都存在同币种已知估算；即使完整，actual_spending_verified仍false。用户积分、原始供应商观察与最终账单确认各自独立。

本地新租约/预算测试及有理数边界合计128单元、SDK计量与模型不符合计50契约已通过本地完整回归；新增11项PG/Worker/S3候选涵盖两步精确估算、原未知保留、一次结算、超额后零检测POST/保留冻结/禁止重开、非货币单位、缺计量/模型不符、并发单记录、中断与审计失败回滚、权限/FK/不可变、实际到期前置拒绝及小数向上取整。原生/完整冷恢复必须看新CI，不用本地单元冒充。

## 带数据的恢复候选

独立恢复演练新增非空估算账本：固定MockTransport和合成凭据在loopback profile生成已知回执与最终PNG，明确BFL_CREDIT价表不可比较USD预算，故不得产生检测POST。快照新增第5项被隔离任务，恢复验证原估算金额/单位/关联ID/价表SHA与待审阻断完整保留，不变成美元或实际已确认支出。源fixture仅通过受限fail结案释放内部积分，成本记录不删改；目标保持隔离。该演练不证明live私有SDK元数据或真实账单已验，必须单独看当次冷恢复CI结果。

0033与非空账本恢复最终证据：9174020/CI43503294037成功，128单元/50契约/164原生、4+7浏览器、完整Compose/独立冷恢复/重启/P5-02通过，包含cost_estimate_history_and_review_block_preserved。这里只验证合成供应商与估算，不代表真实发票、实际USD支出或Hubway live接通。


## v3 多计价单位预算（0034，aecd209/CI43511616685整轮通过）

授权 JSON 的 `schema_version: 3` 用 `step_budgets.generate/detect` 各自指定 `{currency, ceiling}`，`currency_totals` 是同结构的单位总额数组。允许 USD、CNY、BFL_CREDIT、HUBWAY_BALANCE_UNIT；上限必须是正数的精确字符串，最多6位小数。相同单位两步的总上限必须覆盖两步上限之和；不同单位分别授权，禁止重复、缺失或多余的单位总额，禁止重试步骤和 v2 标量字段混入。模型和每步最多1次仍固定。

登记前报价必须冻结两步已归档价表，且每步价表单位与预算一致；缺失价表或单位不一致在登记阶段拒绝，尚未创建任务或供应商意图。价格版本已绑定完整报价摘要、商户/分组/端点；更换单位或上限改变审核摘要。v2仍接受原USD格式，原有不可比单位估算会阻断，不被v3静默升级。

v3私有快照的 `currency/total_ceiling/reserved_ceiling_total/known_estimate_subtotal` 为 null；请读 `limits_by_currency` 中每个单位的独立金额，`step_limits/holds`也明确带单位。即使两步使用同单位，v3仍使用这套输出，不产生新的全局金额。所有数字均为授权上限或估算，`actual_spending_verified`始终false，不能凭Hubway页面的美元符号或充值比例转换为现金USD。

登记/查看仍使用 `scripts/provider_budget.py register/inspect`；预检 `scripts/provider_smoke.py --preflight` 同时支持v2/v3，并保持execution_enabled=false与零付费调用。真实账单、价格凭证与live供应商接入仍待完成。


## 同步检测事实的持久化（0035，cd29656/CI43512330026整轮通过）

Gemini同步响应没有可供恢复查询的远程任务ID；`gemini-<意图UUID>`仅是本地归档标识。新Worker在应用检测回执之前，用单个数据库事务保存已知回执与完整的受限事实归档，含原响应摘要、最终图摘要、原始计量和SDK/提示版本信息；任意一步或审计写入失败都会回滚两者。私有表与受限函数不向API用户暴露，数据不可修改或删除。Worker后续估算和交付读取已存归档，不依赖本地`.facts.json`，不会因文件丢失再次调用检测。

已知响应返回到首次持久化之间仍存在不可消除的进程崩溃窗口：若数据库未保存且本地原结果也丢失，只能保持未知/人工处置，不能自动重发或宣称检测成功。旧回执仅在原本地事实文件仍在时补归档。归档证明保存的是同一次响应，不证明供应商实际质量、发票或商户真实扣费；默认仍双MockTransport。


## 账单作用域预检（0036，0e1d772/CI43514389341整轮通过，未确认真实费用）

私有Ops命令：

```sh
python scripts/supplier_pricing.py billing-context --task <uuid> --attempt <uuid>
python scripts/supplier_pricing.py review-bill --document <私有账单映射JSON> --evidence-file <私有证据文件> --attest-reviewed
```

账单映射包含schema_version=1、task_id/attempt_id、quote_input_sha256、provider/model/endpoint_origin、merchant_fingerprint/billing_group、provider_request_id/response_digest、currency/amount、evidence_kind/evidence_sha256、statement_fingerprint/line_fingerprint。金额为非负精确字符串，最多12位整数和8位小数；指纹均为SHA256，不放API密钥或完整账单账号。配置最多64KiB并拒绝重复键；证据最多20MiB，必须与审核映射SHA一致。

上下文只从PG原回执/冻结价格/归档结果取值。生成使用BFL已知请求ID；同步检测必须用真实响应里的provider_response_id，不能把本地gemini-意图ID当供应商请求编号。缺远程ID、商户/分组/根地址/模型/报价SHA/单位不一致或未应用回执均拒绝。Hubway余额仍保持HUBWAY_BALANCE_UNIT。

当前只验offline_fixture和offline_protocol_mock，reviewed_statement或live组合拒绝。输出仅OFFLINE_BILL_SCOPE_MATCHED、单步上限关系及同单位估算差额；未知/不同单位估算的差额为null，全预算累计尚未核对。actual_spending_verified、confirmation_enabled、evidence_archived始终false，无账单入账、预算重开或积分变动。真实证据归档、去重、正式账单确认与live上下文仍需后续实现和独立验收。


## 离线账单证据归档（0037，5829b86/CI43515956403整轮通过，仅fixture）

```sh
python scripts/supplier_pricing.py archive-bill --document <私有账单映射JSON> --evidence-file <私有证据> --attest-reviewed --operation-id <uuid> --reason <审阅原因>
python scripts/supplier_pricing.py bill-inspect --review <uuid>
```

沿用预检的严格范围，只接收离线fixture，不启用reviewed_statement或live账单。先用Ops身份读取PG上下文并预检、核对证据SHA，再向私有S3写不可变原字节，最后在单个PG事务重新核对上下文/文档及审计。上下文变化时拒绝旧审核；SQL也独立拒绝金额类型、额外字段、未知身份和本地Gemini ID。

按attempt_id、供应商根地址+商户/分组+请求ID，以及商户+原账单/行指纹三层去重；同操作原文档/原因/上下文可并发幂等，不允许用新operation再次记同意图。更正或退款尚不支持，不能改写已有证据。存储失败不产生PG记录；审计失败回滚PG记录，但可能留下无数据库引用的私有S3对象，重试原operation复用原字节，不自动删除审计证据。

输出review_status=offline_fixture_matched，actual_spending_verified=false；它是审核证据记录，未写app.task_cost_records的settlement/confirmed，未改变预算或用户积分。证据文件随独立备份复制并核对SHA，恢复只重绑存储version，不改原文档/上下文/金额/状态。真实账单确认、跨账单累计/退款、live费用验证仍不在已验范围。


## 检测响应原文（0040候选）

新版私有归档保存HTTP响应体的Base64原文和SHA256，以及SDK规范化JSON及原有response_digest。HTTP体限1MiB，整个记录限16MiB；它不含请求认证头，也不是TCP/TLS抓包或供应商签名。读取时重新验证响应身份、严格JSON、事实和计费用量；外层重复键响应保留原文并进入人工/未知费用，事实字符串内部重复键也拒绝。旧格式不升级为原文已验证。费用观察必须与归档中的用量、SDK及原文摘要一致。原文和私有账单不进入公开交付ZIP；备份通过原有私有归档恢复。全部供应商传输仍硬绑定MockTransport，真实供应商/账单未验收。
