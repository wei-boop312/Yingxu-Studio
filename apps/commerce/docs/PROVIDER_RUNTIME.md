# 供应商运行配置

当前用户选择百炼北京Qwen-Image-3.0生成和Hubway/Gemini检测。schema2显式运行身份、schema4分币种预算、双费率绑定、请求前预测及原文归档/恢复已通过离线协议与真实PG/S3验收；本机百炼密钥和连接指纹已准备，尚未请求模型。旧schema1 BFL路径保留给原任务，不能把其授权或费用单位复用给百炼。当前仍无live执行开关，AGENTS禁止实际付费模型。详见BAILIAN_SETUP和PROVIDER_SMOKE。

将`ops/provider-runtime.example.json`复制到被Git忽略的私有目录。模板全零指纹和EXAMPLE分组只是占位，不代表商户已审阅；正文禁止API key、密码或额外字段。schema1生成允许BFL官方精确origin和flux-3-image，schema2生成允许百炼北京业务空间精确origin和qwen-image-3.0；检测允许google_gemini官方origin或hubway_gemini的两个精确origin，固定gemini-3.8-flash。没有核实Hubway编辑协议前不虚构该生成选项。

`python scripts/provider_runtime.py --config work/private-runtime/runtime.json`只核验结构，输出配置SHA、模型、端点及应使用的环境变量名，不读取密钥，不创建客户端，不连接数据库/供应商。返回RUNTIME_BINDING_REVIEW_READY表示可供审阅，不表示已经接通或授权费用。`--check-credentials`额外在本机读取所选环境变量，检查格式及与配置中credential_fingerprint是否相同，只返回每步布尔值。BFL使用BFL_API_KEY，Hubway使用HUBWAY_API_KEY，官方Gemini使用GEMINI_API_KEY，百炼使用DASHSCOPE_API_KEY。

credential_fingerprint由前缀ASCII字节visual-provider-credential-v1、一个NUL字节、再拼接密钥ASCII字节计算SHA256。可在本地通过`visual_platform.providers.runtime.credential_fingerprint`生成，不能将密钥贴进聊天、配置、命令行参数或公开日志。该摘要用于绑定同一把密钥，不能证明该密钥属于配置所声明的商户；商户指纹和计费组仍需运营独立核实。更换密钥也会要求新的配置摘要和完整报价/预算审阅，不能借旧授权换账户继续提交。

配置摘要覆盖版本、各步模型/origin、商户指纹、分组及密钥指纹。`execution_binding()`产生受信服务器快照字段provider_runtime_sha256、provider_models、provider_accounts和完整provider_endpoints，受原报价SHA及预算授权绑定；HTTP客户端无权注入这些字段。配置origin是明确选择，不跟随HUBWAY_BASE_URL等环境变量静默变化，也不会在默认/直连/官方地址之间自动回退。

`offline_runtime`将同一配置组合现有BFL与Gemini/Hubway适配器，但必须显式注入两份httpx.MockTransport；混合或缺失transport在创建目录/客户端前拒绝，密钥指纹不符同样拒绝。带运行配置的任务不能由没有该配置摘要的旧协议实例代领，模型/端点/商户/运行SHA不符在供应商意图前拒绝；已有原回执时保留未知与冻结，不自动重投。该组合仍标offline_protocol_mock，不能冒充live。

真实执行尚需live专用报价/预算/任务领取和传输观察接线、实际Hubway响应兼容性、真实样本与账单证据及明确费用上限；当前AGENTS仍禁止实际付费调用。不得因本地配置校验成功就把现有Fake消费者切到真实请求。配置与密钥存在也不构成PROVIDER_VERIFIED或上线完成。

## 受限离线协议报价准备

0049候选先持久化登记已审阅的运行元数据（不包含密钥）：`python scripts/provider_runtime.py --config work/private-runtime/runtime.json --register-config --reviewed-sha256 <运行配置SHA> --operation-id <登记操作uuid> --reason <配置审阅原因>`。此模式与check-credentials、prepare-protocol-quote互斥，单独配置审阅不是费用授权。版本正文和摘要存入Ops私有FORCE RLS表，只能追加，API/Worker无原始读写权限；登记与唯一审计同事务，相同正文可复用版本但不同审阅操作各留审计。

数据库在新报价和调用意图插入时独立核对登记的完整模型/端点/商户绑定，未登记的运行SHA和与登记正文不一致的字段拒绝；未使用运行配置的历史Fake/mock任务保持原兼容性。登记版本可通过受限provider_runtime_context函数读取并重建对象，不依赖创建时的本地JSON文件。密钥只保留领域隔离摘要，商户归属和兼容性仍未验证；备份恢复不会因此恢复费用授权。

独立冷恢复候选加入非空运行登记及原协议报价。恢复后仅从PG正文重建配置并重放原操作，必须保留原ID、配置/报价SHA及报价到期时间，不追加报价、不续期、不改变积分。0050用受限计数函数核对原登记/报价各一份审计及唯一报价，不扩大Ops对审计或业务表的原始读取权限。该标记须在真实独立PG/S3恢复中通过；离线语法分析不能替代恢复证明。

## 停用运行配置

0051候选提供`--revoke-config`，与其他模式互斥，必须给同一私有配置、reviewed-sha256、operation-id和reason。此操作只停用平台内的该配置版本，不修改第三方API密钥，不退款或删除原报告，也不构成实际费用已核实。复用同一操作返回原结果，修改原因必须新操作；重新登记相同正文不会撤销停用，恢复使用需要新的配置版本及新的报价/预算审阅。

登记正文不修改，另追加不可变停用记录及审计。配置校验持有事务级共享锁，停用持有同一配置的独占锁：停用提交后，新报价、任务及供应商调用意图均拒绝。此前已经提交的调用意图仍可能产生结果或费用，停用不保证取消它们，不能将其记成未收费。历史操作的元数据重放仍可读取原回执，不会因此重新授权执行。

任务尚无调用意图时，被停用配置阻断后按现有无意图路径退回内部积分；已有供应商尝试时保持uncertain及冻结，不能因为配置停用就退回可能已消耗的费用。已知生成ID仍可安全查询/归档，但后续新检测意图会被阻止，不能重发生成。普通API返回409中文可读错误和问题编号。新增原生候选覆盖提交前阻断且无冻结、已排队后零意图退款、已知回执后无新检测且保持冻结，以及停用提交与报价写入的真实PG锁竞争；所有供应商HTTP仍为MockTransport。

0048候选提供Ops入口，使用普通API刚创建且未消费的Fake报价作为业务来源：

`python scripts/provider_runtime.py --config work/private-runtime/runtime.json --prepare-protocol-quote --user <uuid> --source-quote <uuid> --reviewed-sha256 <运行配置SHA> --operation-id <uuid> --reason <审阅原因>`

仅此显式模式读取ADMIN_DATABASE_URL并写入新报价及唯一审计；结构预检不写数据库。来源用户、产品、源图、生成参数、规则与外观快照保持一致，PG独立拒绝业务改写、模型替换、NULL或任意端点、账户字段中夹带密钥。新报价标offline_protocol_mock并使用现有试验积分规则，不是供应商真实费率；原报价不覆盖，新报价仍须单独登记费用范围预算，再提交任务并显式调用匹配的离线Worker。CLI不登记预算、不创建任务、不执行模型。

相同operation/来源/用户/配置/原因重放返回原报价和原SHA，即使来源后续已消费也不新建或延长授权；任何改动不得复用操作。新的操作要求来源仍有效且未消费。审阅方随后使用provider_budget.py review-quote取得新报价完整input_sha256，不能把运行配置SHA当作完整费用范围SHA。普通API/Worker没有这两个Ops函数权限，未开放通用数据库写权或公开供应商切换参数。
