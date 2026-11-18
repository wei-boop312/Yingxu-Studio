# 百炼可选视觉检测适配

用户已允许增加百炼qwen3.8-flash检测适配，保留Hubway/Gemini路径。独立协议适配器及schema 3双百炼离线组合已实现，0064增加运行登记候选；平台报价/预算、PG检测归档和默认Worker尚未开放。不能使用旧Gemini授权、费率或用量字段调用它。真实传输仍在构造前被拒绝，只接受显式MockTransport。

schema 3显式配对qwen-image-3.0与qwen3.8-flash，并要求同北京业务空间、密钥指纹、商户指纹及计费分组。schema 1/2的Hubway检测路径保留。两端事实、请求端点及用量分别绑定；旧Gemini执行范围不能替代新检测范围。登记仅保存身份元数据，准备报价明确返回`qwen_vision_quote_budget_path_not_enabled`，不会借用旧报价和预算开启支出。数据库登记与拒绝报价的真实验收等待该提交的Linux CI。

两种运行示例分别位于`ops/provider-runtime-bailian-hubway.example.json`和`ops/provider-runtime-dual-bailian.example.json`，均仅占位、没有密钥或已审阅身份。复制到私有.local后才能填写实际业务空间和指纹；不要修改示例当作生产授权。`provider_runtime.py --config <配置>`仅验证身份结构且默认不读密钥；显式--check-credentials才比较本机指纹。双百炼输出额外列出报价预算、PG检测归档两个缺口，准备报价在连接数据库前拒绝，返回RUNTIME_QUOTE_NOT_IMPLEMENTED；有效配置不代表可执行。

官方[模型页](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)和[视觉接口](https://help.aliyun.com/zh/model-studio/vision/)确认模型支持图像理解，使用北京业务空间 `/compatible-mode/v1/chat/completions`。仅提交已隔离规范化的最终PNG内联字节，绑定SHA和实际尺寸；项目限制4MP/10,000,000字节。不开流式、工具或自动续轮，显式关闭思考并限制输出8192 token。

本机绑定连接的目录只读核验已得到HTTP200和精确qwen3.8-flash行。可用`bailian_metadata.py --probe --model qwen3.8-flash --config <私有连接> --output-dir <新的.local目录>`重新核验；不加--probe不读密钥或请求。目录存在不等于推理权限、质量或计费已验收；完整响应只留私有目录，不能提交供应商账户数据。

[结构化输出说明](https://help.aliyun.com/zh/model-studio/qwen-structured-output)明确多模态输入会将json_schema降级，不能假设服务端执行Schema。因此请求使用json_object并在系统提示附事实Schema，本地用既有严格Facts模型检查JSON唯一键、字段/尺寸/坐标和置信度。拒绝、截断、工具调用、错模型、无效JSON或不合规事实都保留人工复核，低置信度保留检出的文字/标志，不把未检出当作可靠不存在。模型不决定平台合规。

意图文件O_EXCL/0600/fsync先于HTTP，超时、显式失败或未知结果均不会重发同一意图。原HTTP字节/base64/SHA、最终图SHA、请求/提示/Schema摘要与派生事实/用量留不可变私有记录；读取时从原文重新校验，不信任被修改的结论。

OpenAI兼容用量与Gemini六计量规则分开：prompt包含缓存、completion包含思考；本请求已禁思考，计量必须有完整严格整数及缓存/思考明细，且总数相符。缺字段、布尔/浮点、额外收费量、错模型或意外思考时不生成精确数量，不推定零费用。金额/币种保持unknown，费用估算、账单和真实质量仍待后续独立接线及验收。

费率审阅候选使用独立schema 3/provider `aliyun_bailian_vision`，仅CNY及非缓存输入/缓存输入/输出三个互斥计量。completion已包含思考，不能再添加thinking收费计量；原始reasoning计数仍保留并要求为0。0065允许Ops显式证据审阅、私有S3归档和幂等登记，不自动发布公开参考价，也不开放schema 3任务报价、预算或付费HTTP。独立算术估算永远不代表实际账单，协议归档本身的金额仍unknown。

双百炼范围预检采用独立schema 5，generation_model/detection_model明确绑定两个模型，单次生成/检测各最多1次，沿用原图/报价/目标/UTC窗口摘要。两步都必须CNY，currency_totals恰好一项且至少覆盖两步上限之和；不接受旧Gemini/BFL字段、混币或把两个上限覆盖成最后一个值。`provider_smoke.py --config <私有范围> --preflight`只检查草稿与规范样本，返回DRAFT_SCOPE_REVIEW_READY、budget_registration_implemented=false、execution_enabled=false，不读取真实模型密钥。绑定数据库预检明确未实现，服务在DB连接前拒绝登记；当前不能创建schema 5原子支出预算，也没有替用户决定上限或取得费用授权。

官方模型页的北京公开参考为每百万token输入0.8元、缓存命中0.1元、输出2.7元；不适用于显式缓存创建、批量或其他地域，不代表本商户折扣或免费额度。公开摘记只用于人工审阅准备，不具备发布费率所需的商户、业务空间和有效期证据。

只读原回执审阅入口：`supplier_pricing.py estimate-vision-archive --price <已审阅价格UUID> --archive <私有facts.json> --scope <私有审阅范围.json>`，需要Ops数据库身份。范围明确列出final_sha256/request_sha256/response_digest、merchant_fingerprint/billing_group和UTC observed_at；不能输入替代用量。系统从原响应重新验证Facts/计量及归档派生值，再使用精确商户价格估算。缺计量或身份/有效期不符保持unknown，篡改派生数量或三个摘要不匹配直接拒绝。它不认证人工范围来源，不写任务成本、预算或账单，不提供执行授权；PG任务检测归档路径仍未开放。组合读取还拒绝来自其他业务空间的完整有效回执。

本机样本协议检查：`verify_offline_vision_sample.py --run --source <规范最终PNG> --output-dir <全新.local目录>`。不加--run不读样本；实现固定合成密钥、合成工作空间和MockTransport，不读真实密钥或远端。显式验证内联PNG完整字节及尺寸，保存私有意图/原文/事实归档；返回的事实始终未分析完成/人工复核，缺用量保持unknown。已用用户商品保护区域构成的2000²合成画布实际验证4MP边界，不外推真实AI检测或生成质量。
