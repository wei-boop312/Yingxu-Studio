# BFL FLUX 3 离线协议适配

P4-01的协议模块为`src/visual_platform/providers/bfl.py`，离线入口：`python scripts/check.py task P4-01 --profile offline`。18项契约测试均使用httpx.MockTransport，不访问真实模型，无真实账户、费用或视觉质量结论。默认Worker CLI仍只启用offline Fake；显式MockTransport下的源图/最终图组合、观察记录和独立报告标识已接入Worker，见[PROTOCOL_PIPELINE.md](PROTOCOL_PIPELINE.md)。真实传输、真实费用确认和完整真实模型流水线仍未启用。

## 字段映射

| 项目输入/阶段 | 协议映射与约束 |
|---|---|
| 编辑提交 | 固定POST `https://api.bfl.ai/v1/flux-3-image`，鉴权`x-key`；没有Bearer、旧input_image或自定义幂等字段。 |
| 原图 | 调用方已核验的canonical PNG编码为base64放入images；不发送localhost、私有S3或用户提供URL。最多10张、总20MiB；本MVP构造器只用一张源图。 |
| 生成设置 | 严格Generation转换为prompt、images、width、height、grounding=false；不发送内部积分、规则快照、ROI字段或用户ID。白底场景添加明确背景指令；精确回贴仍由本地流程执行。 |
| 尺寸 | 当前OpenAPI列出width/height，单边256–6256；项目编辑限制总4,000,000像素。小于256的源/目标不能自动放大后宣称精确，真实任务前必须据配置验证兼容性。 |
| 提交回执 | 保存id、返回polling_url、原响应摘要。cost只按供应商报告的credit数量保留；未知/缺失保持null，货币和金额均null，不猜人民币汇率或混同用户积分。费用畸形则保留已知回执并转待核对。 |
| 查询与结果 | GET返回的polling_url，校验API主机、路径和id；Pending/Reasoning/Generating可稍后只查已有结果，Ready读取result.sample。终态失败与未知状态分开，不重发提交。 |
| 下载 | 独立无API Cookie/鉴权头客户端，仅接受HTTPS delivery.<region>.bfl.ai；禁止重定向、压缩HTTP响应，JSON上限1MiB、图像20MiB。PNG/JPEG返回原始字节、MIME及SHA；调用方必须在隔离解码器做canonical化、实际尺寸检查、保护区回贴后才可检测与交付。 |

官方依据：[生成](https://docs.bfl.ai/flux_3/flux3_image_generate)、[编辑](https://docs.bfl.ai/flux_3/flux3_image_layout)、[当前OpenAPI](https://api.bfl.ai/openapi.json)、[集成指南](https://docs.bfl.ai/api_integration/integration_guidelines)。2026-11-13核验元数据和完整OpenAPI响应SHA见`docs/evidence/bfl-official-contract.json`。文档叙述与schema可能不同，以本次核验字段为协议基线；不照搬第三方“width被拒绝”描述。官方页面对结果URL期限有不同描述，因此实现要求立即下载，不依赖精确到期保证；带签名的供应商结果URL只作私有恢复材料，不交给浏览器。

## 提交与恢复边界

调用方必须先在PG记付费意图，再调用submit。私有目录的UUID.intent用排他创建并fsync作为额外进程/本机重启防重复保护；它不能替代PG多主机租约和意图状态。相同意图无论已成功、超时或被拒绝，适配器均不再次POST；HTTP 429也不自动重试。已知回执用receipt_record读取，缺文件、损坏、网络中断、5xx或无法确认响应均不允许据此重发。

记录目录由调用方放到受保护的忽略目录，Windows需由部署者配置ACL；本模块不宣称自动加密或完成Windows权限配置。不得删除意图文件“解锁重试”，也不得将含polling_url的私有文件提交Git。受信任BFL域名校验不替代部署网络出口策略；真实运行需限制DNS/出口，不能引入任意自定义API/CDN主机绕过限制。

费用来源保留响应摘要、credit单位及reported/missing/invalid状态，尚无可核实的法币账单关联。当前不提供自动价格换算，也没有真实供应商成本DB写入。后续组合适配器必须将来源成本与现有内部冻结/结算分离，禁止沿用Worker的record_fake_cost当作真实费用。

## 已执行与未执行

`python scripts/check.py task P4-01 --profile offline`：18项通过；覆盖精确字段、严格schema、同UUID并发只有一次POST、实例重建后不重发、超时/HTTP拒绝/5xx/畸形响应、未知费用、已知回执保留、URL越权、鉴权与Cookie分离、大小限制、禁止重定向及压缩。日志bfl-contract-20261113.log。现有49项单元回归通过；uv锁文件离线一致性检查通过，httpx已列为运行依赖。

真实传输下的API调用、账户可用性、输入图可读性实测、生成图尺寸/保护回贴、成本账单、Gemini检测及完整Worker组合：NOT_RUN。P4-03继续等待明确样本、调用次数/费用上限和安全密钥，不能标PROVIDER_VERIFIED或RELEASE_READY。
