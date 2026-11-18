# Gemini结构化事实离线适配

P4-02模块：`src/visual_platform/providers/gemini.py`。指定模型保留`gemini-3.8-flash`，官方Python SDK精确锁定`google-genai==3.3.0`及uv依赖哈希。运行`python scripts/check.py task P4-02 --profile offline`，19项真实SDK加MockTransport契约通过；没有真实模型HTTP调用，账户权限、地区与费用仍未验证。

官方依据：[指定模型](https://aistudio.google.com/docs/models/gemini-3.8-flash)、[结构化输出](https://aistudio.google.com/docs/structured-output)、[Python SDK](https://googleapis.github.io/python-genai/)。2026-11-13来源响应SHA、SDK版本、schema和系统提示摘要见`docs/evidence/gemini-sdk-contract.json`。目前结构化输出页面也包含其他API示例，本实现使用锁定SDK仍支持的`aio.models.generate_content`路径，不擅自换模型。

## 一次提交与最终图绑定

调用方必须先在PG落付费意图；适配器额外用本机私有UUID.intent排他写入并fsync，防并发和实例重建后的同意图重复提交。所有重复意图均拒绝，不能删除文件来“重试”。该文件保护不替代多主机PG意图和租约。

提交前校验最终PNG SHA与期望值一致，通过现有隔离解码子进程校验格式和实际宽高。SDK图片Part直接承载这份最终PNG字节，没有原图URL、用户生成提示词或平台规则输入。锁定SDK实际在inlineData内部使用mime_type和URL-safe base64；契约按SDK实际编码解码并比较全部字节，保证不是仅比较文件名/宽高。SDK收到的是最终图，不是生成候选图或原始产品图。

显式固定HTTPS Google Developer API主机和v1beta模型路径，忽略环境代理；请求钩子拒绝目的地主机/路径/查询改变，不跟重定向。自定义异步transport将响应限制1MiB并拒绝压缩HTTP正文。SDK/transport配置30秒网络超时及35秒调用预算。

已核对SDK默认重试和自动续发行为：HttpRetryOptions.attempts=1包含首次；automatic_continuation=false；自动函数调用关闭，没有tools。429、503、超时、CONTINUATION各场景通过真实SDK HTTP计数证明仅一次POST。网络异常或取消保留意图，未知检测不得自动重发，也不凭缺缓存判定免费。

## 事实校验与人工路由

`contracts/facts.schema.json`与Facts模型生成的schema通过一致性检查；schema_version约束统一为整数最小/最大1，与原const=1语义相同。SDK请求JSON Schema，返回后仍用严格Facts校验，不信任SDK.parsed直接放行。

只接收单候选、STOP、纯文本JSON，核对事实宽高和最终图一致。截断、阻止、多个/缺少候选、函数调用、畸形JSON、缺字段、额外compliant字段、越界坐标、非法置信度均生成analysis_complete=false及明确不确定原因。合法观察中的任何置信度低于0.75也保留观察但标不完整、要求人工复核；0.75是初始路由阈值，不是校准准确率保证。服务端仍按已锁定规则判定平台结果，LLM不能直接决定compliant。

完成的SDK响应保存在受保护的忽略目录，包含最终图SHA、事实、响应摘要、真实response_id（缺失为null）、模型版本、SDK/schema/提示摘要及实际返回的非负usage计数。`gemini-<intent UUID>`只标识本地事实归档，不冒充Google任务ID或可查询的远端作业。fetch_facts只读该归档，不再检测。缺失或损坏归档转待核对，不重发。

usage缺失保持null；金额与币种均null，不根据token数或原计划猜测单价。受阻止/低置信响应也不宣称免费。当前尚未把真实供应商成本写入DB，私有记录目录需由运行方配置Windows ACL；不宣称自动加密。

## 验收边界

日志`gemini-contract-20261113.log`：19项SDK契约通过；包括原始请求字段、最终PNG字节、schema一致性、一次提交/取消/并发/重建、重试和续发关闭、手动降级、usage未知、缓存损坏、环境不能更换主机/后端、大小/压缩/重定向保护。49项单元和18项BFL契约回归通过，日志gemini-unit-regression与gemini-bfl-regression-20261113.log。uv锁文件与已安装60包兼容性检查通过。

默认Worker CLI仍仅offline Fake；显式MockTransport下的BFL/Gemini组合、未知成本观察及独立报告profile已验证，见[PROTOCOL_PIPELINE.md](PROTOCOL_PIPELINE.md)。真实图识别准确性、事实置信度校准、账户调用和费用核对均NOT_RUN；P4-03仍等待明确样本、次数/预算和安全密钥，不标PROVIDER_VERIFIED。真实传输、预算约束和真实成本确认仍需接线验收。
