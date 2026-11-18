# 百炼真实供应商接入

2026-11-15 已按最新用户授权在本机启用百炼真实模式（qwen-image-3.0生成＋qwen3.8-flash检测），不设人为累计费用上限。一次正常API/MQ/Worker真实任务已完成：2个供应商意图均succeeded，2000² PNG与报告下载摘要核对通过，三个保护区像素通过、原始3000²商品图未变，检测Facts结构有效且analysis_complete=true；结果继续manual_review。生成参考估价0.20CNY，检测计量缺失保持unknown，actual_spending_verified=false。使用全新任务及追加授权，不复用旧实验；原图/响应/密钥均留私有。浏览器已刷新并观察到“百炼 · 真实供应商”，桌面启动器已兼容新模式。代码5af5f2c，完整CI43722102329进行中；不把实际链路跑通当作画质/规则/账单或整个项目完成。详见LIVE_BAILIAN.md和evidence/live-bailian-acceptance-20261115.json。

本次范围仅为真实供应商链路。用户于2026-11-15明确要求接入，并对费用上限回复“对我不设上限”。新模式为bailian_live：qwen-image-3.0生成、qwen3.8-flash检测，Hubway原配置与适配保留，不自动切换或混用。默认环境仍为offline，CI始终注入MockTransport，不调用真实模型。

## 用户操作

启用后的工作台顶部显示“百炼 · 真实供应商”。上传和选区流程保持原样；获取报价后，确认框显示实际费用提醒和人民币参考上界，用户确认才创建任务。平台积分与供应商人民币费用分别记录。参考估算不是账单，不把缺失用量记为0，也不宣称可上架。

旧页面不携带真实调用确认标记时，后端拒绝创建真实任务，需刷新后重新确认。已存在的离线任务继续按冻结的离线模式执行，不能因为切换配置变成真实付费任务。独立实验的旧授权与回执不复用。

## 管理员启用与停用

使用项目非owner的ops_admin数据库连接ADMIN_DATABASE_URL。密钥只在本机DASHSCOPE_API_KEY，配置中只保留指纹；不得放入前端、Git或命令参数。

1. 准备schema 3运行配置，固定北京业务空间、两个模型、商户/分组/密钥指纹；保留该空间新读取的两份原始模型目录。原始目录须通过独立价格解析器及已审阅参考费率核对。
2. 显式执行`python scripts/live_provider.py --enable --user USER_UUID --operation-id OP_UUID --reason "用户授权真实调用且不设金额上限" --runtime PRIVATE_RUNTIME_JSON --generation-catalogue PRIVATE_GENERATION_BODY --detection-catalogue PRIVATE_DETECTION_BODY`。登记运行配置和该用户的追加式授权审计，不会调用模型或自动改运行进程。默认不带enable/disable参数时不访问数据库，也不会启用任何功能。
3. 为API和Worker设置PROVIDER_PROFILE=bailian_live；Worker另设置LIVE_PROVIDER_RUNTIME_FILE和LIVE_PROVIDER_PRIVATE_DIR。停止旧消费者后再启动单个真实路由消费者，保留Publisher和原存储。真实路由消费者可处理旧offline任务；普通offline消费者不能领取真实任务。切换后刷新页面。
4. 停用用`python scripts/live_provider.py --disable --user USER_UUID --operation-id OP_UUID --reason "停用原因"`。不需要重新读取目录或创建密钥。新意图前再次检查授权；如果生成已发生而检测被撤销，保持人工对账，不重复调用。

授权按用户和冻结运行配置登记，累计费用上限为NULL（用户明确不设上限）。这不等于自动批量生产：仍按已确认任务运行，单次任务一张图，模型输出长度和体积仍有技术边界。

## 持久化与恢复

每步先在PG记录唯一意图，再进行唯一POST；本机也保留不可覆盖的意图和原始回执。超时或结果未知不重发POST，已知生成ID仅查询。下载固定可信OSS形式，使用独立无密钥客户端，不跟随跳转。旧MockTransport构造入口保持默认拒绝真实网络，真实工厂必须显式选择，拒绝混合传输。

生成提交原文、终态查询、最终PNG摘要及检测原文沿用现有PG/S3任务链路；检测使用独立representation 3，不套用Gemini SDK格式。费用行使用bailian_live/bailian_vision_live标识；原始计量齐全且核对一致才写人民币参考估算，缺少或不一致时保持unknown。原有图片计量校验在输入与输出档位不一致时可能保守降为unknown，原文保留供复核；不以推断数量补齐。缓存或推理明细缺失的检测同样不猜作0。实际账单核验仍单独进行。

恢复数据库后会追加停用全部已恢复真实调用授权的审计，旧会话和未完成任务继续按已有恢复规则隔离；管理员需重新核验后显式启用。配置备份只保留公开设置，不含密钥及私有路径，恢复不会自动应用真实配置。

## 当前验证

本机225单元、136契约（后新增默认CLI契约需当次CI计数）、141源码双平台静态，以及3项合成HTTP界面检查通过。隔离PG/S3完整283原生检查通过（当时包含5项新增用例）；随后真实路由、旧页面确认及停用幂等补齐至7项定向检查通过。HTTP供应商均为显式MockTransport，此处不能当成实际付费验收。完整CI及本机真实任务结果继续记录STATUS。

原始接口依据：[图像生成/编辑](https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference)、[qwen3.8-flash](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)、[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。多模态响应仍需本地Facts严格校验，空数组和不完整识别保持人工复核。IMG-001画质问题仍按用户决定留档，本次不优化合成算法。
