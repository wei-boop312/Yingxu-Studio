# 本地开发与私有账号

安装依赖：`uv sync --locked`。迁移只对专属PostgreSQL实例运行：设置MIGRATION_DATABASE_URL后执行`uv run python scripts/dev.py migrate`。迁移会创建集群级NOLOGIN权限组；不要在用户现有业务实例运行。

分别创建非超级用户、非owner、NOBYPASSRLS的LOGIN账号，仅授app_api、app_auth或ops_admin中一个权限组。API和认证连接池启动时拒绝超级用户及对象owner。

设置API_DATABASE_URL、AUTH_DATABASE_URL、JWT_PRIVATE_KEY_FILE、JWT_PUBLIC_KEY_FILE、APP_ORIGIN与COOKIE_SECURE。API启动：`uv run uvicorn visual_platform.api.main:app --app-dir src --host 127.0.0.1`。HTTP仅允许显式本机origin配COOKIE_SECURE=0，其余使用HTTPS和安全Cookie。

设置ADMIN_DATABASE_URL后创建私有用户：`uv run python scripts/dev.py create-user --email user@example.com --credits 100`。密码通过隐藏终端输入并确认，不写命令历史。授额：`uv run python scripts/dev.py grant --user-id UUID --amount 100 --operation-id UUID --reason "test credits"`。重试使用同operation-id。CLI必须使用ops_admin专用LOGIN，不能使用超级用户。

登录POST /api/v1/auth/login需要精确Origin，其他写请求还需要visual_csrf Cookie对应的X-CSRF-Token头。HttpOnly的access/refresh Cookie不会暴露给脚本。refresh轮换一次；旧refresh重放会提交会话族撤销，旧access也随即失效。退出/账号停用同样使访问令牌失效。

测试：`uv run python scripts/check.py unit`。原生PG测试需在已迁移的独立测试实例设置TEST_DATABASE_URL（专用测试owner，允许创建测试LOGIN），再运行`uv run python scripts/check.py integration`。集成测试会建立随机账号和角色，不能连接生产数据库。该入口已覆盖真实PG/MQ/S3用例，浏览器闭环另行在生产预览执行。

/api/v1/health/ready检查有限API依赖（PG api/auth与S3），就绪200、不可用503；scope为api_dependencies，具有短超时、缓存和单轮并发探测。/api/v1/health/live仅表示进程存活。MQ/Worker及供应商健康另行判断，不能由API就绪推导OFFLINE_READY。详见READINESS_AND_ROLES.md。

S3配置：S3_ENDPOINT、S3_ACCESS_KEY、S3_SECRET_KEY、S3_BUCKET（默认visual-private）、S3_REGION（默认us-east-1）。远程endpoint必须HTTPS，本机可HTTP。请提前创建私有bucket。原生S3测试另设TEST_S3_ENDPOINT/TEST_S3_ACCESS_KEY/TEST_S3_SECRET_KEY/TEST_S3_BUCKET；没有这些变量时S3用例会明确skip，不代表集成通过。临时解码文件在项目work目录并自动删除，已加入gitignore。

独立Worker单任务入口：设置WORKER_DATABASE_URL（仅app_worker权限组）、上述S3配置、PROVIDER_PROFILE=offline，执行`uv run python -m visual_platform.worker --task TASK_UUID`（PYTHONPATH=src）。FAKE_PROVIDER_DIR可设置持久fake结果目录，默认项目work/fake-provider。当前只支持offline，不应改成真实供应商或复用旧密钥。MQ消费入口见下文。Worker结果retryable表示仅可恢复安全步骤；uncertain/stopped需查看数据库状态和对账，不能新建同意图调用。

MQ入口：AMQP_URL（远程必须amqps，本机可amqp）、QUEUE_PREFIX（默认visual.v1）。设置DISPATCH_DATABASE_URL（仅ops_dispatcher权限组）后运行`uv run python -m visual_platform.events`，--once可单轮发布/恢复。Worker改用`uv run python -m visual_platform.worker --consume`长期消费。两进程仍需要PYTHONPATH=src，Worker还需S3/WORKER_DATABASE_URL/PROVIDER_PROFILE=offline。恢复超过5次进入QUEUE_PREFIX.manual供人工处理，不授权新付费调用。原生MQ测试额外设置TEST_AMQP_URL。

运营CLI（ADMIN_DATABASE_URL，仅ops_admin LOGIN）：

- `python scripts/admin.py inspect --task UUID`：读取对账所需任务/attempt/receipt摘要，不返回polling URL或凭据。
- `python scripts/admin.py review --task UUID --check UUID --reviewer USER_UUID --decision approved --reason "核验理由" --evidence-json refs.json --operation-id UUID`：refs.json为证据引用字符串数组，决定可approved/rejected/needs_review。自动报告不改写。
- `python scripts/admin.py accept-receipt --task UUID --attempt UUID --provider-id ID --response-digest SHA256 --reason "核验来源" --evidence-digest SHA256 --operation-id UUID`：只接纳人工核验的既有供应商回执，不发模型请求。
- `python scripts/admin.py reconcile --task UUID --action resume --receipt RECEIPT_UUID --reason "核验结论" --evidence-digest SHA256 --operation-id UUID`：只恢复已知回执；无回执不能resume。
- action=fail（不传receipt）为有记录的内部无法交付结案，释放用户冻结额度，不表示供应商费用已退款或已知。

操作重试必须复用原operation-id和同一payload。证据摘要仅是记录绑定，实际资料仍由运营者核验。Cookie注销/停用立即拒绝访问；人工通过结果标记human_review，与未核验自动规则分开展示。

浏览器验收会通过create_e2e_account.py在显式本机offline profile创建独立临时账号，使用work目录保存随机凭据；不再依赖共用demo账号。verify_browser_png.py独立校验下载文件与原图fixture的保护像素，结果保存在docs/evidence/browser-pixel-verification.json。


生产浏览器验收改用build后的5173 preview，不能同时运行同端口开发web。50对象/4MP/60秒与20并发500请求基准见docs/PERFORMANCE.md；只读性能测试不调用模型或创建任务。


只读视图、运行角色和隔离新实例初始化验证见docs/READINESS_AND_ROLES.md。新初始化会生成第6个只读LOGIN及READONLY_DATABASE_URL，既有profile不自动覆盖；--profile-dir仅允许.local或work内目录。


受控故障阶段使用scripts/run_fake_task.py --task UUID --mode failed/uncertain/late，只能针对本机offline profile，流程及恢复消费者要求见docs/CONTROLLED_BROWSER.md。unknown页面保持SSE，运营结案或晚到回执resume后自动更新；等待期间禁用同产品普通生成入口。


DB+S3+规则恢复已在独立原生实例演练，命令与边界见docs/BACKUP_RESTORE.md。恢复端默认把未结束任务隔离为uncertain/manual，保留冻结额度；不能根据旧快照自动重发生成/检测。仅核验既有回执且确认源侧提交边界后，使用受限运营resume。旧会话撤销，服务账号与JWT重新签发；只有受信任私有归档可交给DDL恢复。

故障分流、常驻监控和原生MQ/S3重启恢复步骤见[OPERATIONS_RECOVERY.md](OPERATIONS_RECOVERY.md)。重启演练只针对本项目隔离的本机服务。


### 执行profile与协议演练通知（0038候选）

默认常驻Worker只领取冻结profile为offline的任务；协议演练Worker必须明确声明offline_protocol_mock。领取时先核对profile，错误Worker返回not_claimed并保持状态、租约、冻结积分与预算不变，不能先领取再因配置冲突退费。旧SQL两参数ops.claim_task等同offline，协议内部测试/明确单任务演练使用三参数接口；未知profile拒绝，manual隔离仍不能领取。

新协议任务的初始/恢复通知使用既有task.manual队列，普通消费者验证也拒绝协议消息。它们由专用双MockTransport实例显式调用单任务Worker，当前没有面向真实供应商的常驻消费者。人工队列的通知不等于task_dispatch已被设为manual；若任务因恢复/unknown已经隔离，必须继续既有审计核验流程，不能靠指定profile绕过。

迁移前已发布的旧协议消息即便误入普通队列，也会被新的delivery/profile检查拒绝；已有恢复/人工通知保持历史证据，不改写消息或供应商结果。真实供应商接线仍按独立授权、预算、回执和计价门槛推进。


### 启动地址与Cookie配置

APP_ORIGIN使用无路径/查询/片段/凭据的Origin，HTTPS必须COOKIE_SECURE=1；HTTP只接受localhost/127.0.0.1且明确COOKIE_SECURE=0。其他COOKIE_SECURE值直接拒绝。域名大小写、默认80/443端口与国际域名按浏览器Origin规范化，入站Origin仍要求精确匹配规范值。S3_ENDPOINT支持合法路径前缀，远程必须HTTPS，并拒绝userinfo、查询/片段、控制符和非法端口；客户端创建前验证。以上适用于API、Worker与Ops构造S3客户端。

setup_local是本机专用初始化器；无效origin在目录/迁移/角色创建前拒绝。配置备份使用同一Origin/Cookie规则保存公开规范值。CI中的HTTPS ASGI客户端验证安全Cookie与真实PG会话，不证明生产证书、反向代理或远程服务器已就绪。
