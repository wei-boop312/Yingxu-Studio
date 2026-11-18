# 官方协议组合与Worker离线闭环

`providers/pipeline.py`将BFL编辑、既有结果查询/下载、最终PNG的Gemini事实检测接入Worker，仍只允许双方明确使用httpx.MockTransport。默认CLI和API报价继续是offline Fake，不能通过HTTP业务请求选择真实供应商或注入_execution。构造器拒绝真实或混合传输；本阶段零付费模型调用，也不宣称真实图片质量/账户/费用已验收。

## 已实现链路

Worker读取并校验锁定版本源图SHA，把canonical原图编码为BFL images；先构建严格请求，再落PG意图及请求体摘要，之后才调用一次供应商。BFL响应保存独立回执，PG的provider id和polling_url用于后续只查已有结果。Pending或已知ID查询暂不可用时归还租约，等待持久化恢复调度，不生成新意图。

PNG/JPEG下载根据实际MIME进入原有隔离解码器，检查生成图实际尺寸与锁定输出一致，禁止悄悄缩放；随后本地精确回贴保护区，再将最终PNG原字节送给Gemini。检测完成只读私有归档，核对最终SHA/尺寸，按锁定平台规则生成报告，持久归档后才结算内部积分。报告provider_profile显式为offline_protocol_mock，不再将协议假传输链路写成offline_fake。

Worker保留同步Fake兼容，并支持原生异步SDK调用。续租与心跳在网络等待时继续工作；未知提交响应按原规则保留冻结、待对账且不再领取。明确终态失败记录attempt失败并只释放一次用户冻结积分；这不表示供应商费用退回。已知回执的下载/归档失败仅重做安全步骤，丢失本地BFL费用元数据也不能阻止按PG既有ID查询或成为重发理由。Gemini缓存损坏后不能重新检测，需保留对账状态。

## 快照与观察记录

迁移0024给受信任服务构造的报价增加明确offline_protocol_mock配置，模型/计价标签为协议测试专用；已有缺少profile字段的快照保持offline行为。意图分别记录bfl_mock/flux-3-image、gemini_mock/gemini-3.8-flash；Worker在执行前比较锁定profile，错配不发模型请求。普通前端仍使用原有Fake报价；协议测试通过原生受限app_api函数创建显式fixture快照，不能把这些fixture当作真实报价功能。

task_cost_records新增intent_observation、response_observation两种追加记录，分别记录意图后未知结果、回执摘要与供应商credit/token观察。金额、币种保持NULL，cost_status=unknown，price_source明确offline-protocol-mock；不把样例credit数量或token数换成真实货币，不再调用record_fake_cost写已确认0美元。内部钱包仍按协议测试报价冻结/扣除一次模拟用户积分，与供应商财务观察完全分开。

写观察函数仅app_worker可调用，绑定有效租约、任务/attempt归属及回执摘要；同内容幂等，不同内容冲突，不允许UPDATE/DELETE。恢复后根据PG既有观察记录跳过重复写入，不因私有元数据丢失而覆写原观察。没有真实费用确认接口或账单证据，本轮不宣称成本对账完成。

## 验证

9项原生PG/S3专项覆盖完整闭环、JPEG canonical化、最终PNG与SDK请求字节一致、保护RGB、单次charge、BFL请求摘要绑定、四条追加成本观察、Pending后只查既有ID、私有费用元数据丢失、生成/检测响应丢失不重发、不确定状态零charge/refund、终态失败释放一次、归档故障恢复不生成、错误尺寸不检测、错配profile、角色/租约/不可变权限。

执行命令：设置隔离TEST_DATABASE_URL及TEST_S3_*后`python -m unittest discover -s tests/integration -p test_protocol_pipeline.py -v`。日志protocol-pipeline-focused-20261113.log；脱敏完整样本结果protocol-pipeline.json。原生服务全回归使用`python scripts/check.py integration`（还需TEST_AMQP_URL），不将供应商MockTransport称为真实模型验证。

49项单元、39项协议契约回归通过；83项真实PG/MQ/S3全回归通过（170.918秒），日志protocol-native-full-20261113.log。3项生产preview浏览器流程通过（37.5秒），使用实际重启后的新版Worker和真实MQ/PG/S3。证据protocol-unit/contract/browser-20261113.log。test/dev都升级0024，四个相关函数定义摘要一致（protocol-migration-state.json）。最终monitor空告警、全部财务异常为0，见protocol-final-monitor.json。不重复未受影响的60秒性能基准，已有性能报告仍见PERFORMANCE.md。

## 尚待完成

真正BFL/Gemini组合传输尚未启用。P4-03需要授权配置、样本、调用次数和费用上限、安全密钥与可验证价格/账单；默认Worker CLI继续拒绝非offline环境，不能改环境变量就发起真实调用。真实任务报价策略、供应商费用确认、运行报告来源标识与授权预算上限仍需闭环验证。完整task/all验收、CI/部署产物也未完成，不标OFFLINE_READY、PROVIDER_VERIFIED或RELEASE_READY。
