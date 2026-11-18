# 跨境电商 AI 视觉生产平台

基于V4计划的私有图片工作台：FastAPI/Worker、PostgreSQL、RabbitMQ、私有S3、React/Konva。已验证的离线MVP包含私有登录、产品与外观配置、真实原图上传、保护区域平移、报价/任务、进度与历史、PNG/报告下载、积分账本，以及独立备份恢复和故障演练。规则证据不足时保持人工复核，不保证商城上架。

初版操作与反馈收集见[初版试用说明](docs/INITIAL_TRIAL.md)。工作台包含“试用指引”“下一步操作”和仅本机反馈导出；这些新增交互已通过桌面与窄屏本机浏览器验证，并通过对应提交的完整CI。

初版交互基线[5bc6c273 / CI43709997483](https://github.com/wei-boop312/-AI-/actions/runs/43709997483)整轮通过：225单元、133契约、278原生PG/MQ/S3、6普通浏览器（4真实服务闭环+2合成HTTP界面）、11受控浏览器、8服务Compose及独立恢复，1790文件交付包本机独立校验通过。本轮零付费调用。原始报告和日志摘要见[当次证据](docs/evidence/ci-43709997483)。2026-11-15本机完整环境已启动并通过真实浏览器闭环，使用[本机试用入口](docs/LOCAL_WINDOWS_TRIAL.md)；其他电脑仍需按下文准备专属环境。

**默认代码配置仍为offline；当前这台电脑的体验账号已按用户授权启用百炼真实供应商。** qwen-image-3.0生成与qwen3.8-flash检测已通过一次正常API/MQ/Worker真实任务，2000²成图及报告可下载；检测返回有效结构，报告保持人工复核。用户明确不设累计费用上限，每次仍需报价确认。真实任务的生成参考估价0.20CNY，检测费用因计量不完整保持unknown，不是已核实账单。见[真实接入与管理](docs/LIVE_BAILIAN.md)和[脱敏实际证据](docs/evidence/live-bailian-acceptance-20261115.json)。Hubway配置与适配保留，当前未启用，也不会自动切换。

出图合成问题[IMG-001](docs/POST_TRIAL_IMPROVEMENTS.md)仍按用户决定留档，本次仅接入供应商。完整规则、真实账单及生产发布仍见[剩余工作](docs/REMAINING_WORK.md)，不标记整个项目完成。

## 安装与本地运行

需要Python 3.13–3.14、Node 24、pnpm 11.25，以及独立PostgreSQL 17、RabbitMQ 4和私有S3服务。

```text
uv sync --locked
pnpm --dir apps/web install --frozen-lockfile
```

在隔离的本机环境配置MIGRATION_DATABASE_URL（专属DDL角色）、S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET及AMQP_URL，然后执行：

```text
uv run python scripts/setup_local.py --demo-account
```

该命令迁移新实例，创建6个独立非owner LOGIN、JWT密钥、S3 CORS和体验账号；角色为集群级，仅用于专属测试实例。私有profile与账号写入被忽略的.local目录，已有profile不会覆盖。项目不包含公开注册、充值或用户配置模型密钥功能。

四个终端分别启动：

```text
uv run python scripts/run_local.py api
uv run python scripts/run_local.py publisher
uv run python scripts/run_local.py worker
uv run python scripts/run_local.py web
```

打开http://127.0.0.1:5173。本机HTTP profile仅用于loopback；其他环境需配置HTTPS安全Cookie。不要同时运行dev web和production preview占用同一端口。Docker独立基础服务及完整应用Compose说明见[COMPOSE](docs/COMPOSE.md)，不是托管发布。

## 验证

始终离线的单元与协议契约：

```text
uv run python scripts/check.py unit
uv run python scripts/check.py task P4-01 --profile offline
uv run python scripts/check.py task P4-02 --profile offline
pnpm --dir apps/web build
```

原生集成需要隔离的TEST_DATABASE_URL，以及TEST_S3_ENDPOINT/TEST_S3_ACCESS_KEY/TEST_S3_SECRET_KEY/TEST_S3_BUCKET/TEST_S3_REGION和TEST_AMQP_URL。缺依赖不能以skip作为验收证据。

```text
uv run python scripts/check.py integration
```

生产浏览器要求API/publisher/worker、.local/runtime.json和Chrome；停止本项目dev web后另一个终端启动preview，再运行e2e。测试创建隔离账号，不复用体验账号。

```text
uv run python scripts/run_local.py preview
pnpm --dir apps/web e2e
```

完整离线门槛是Linux CI下当前提交的原生、构建、生产浏览器、Compose、恢复和运行指标证据组合，不会隐式启动缺少的服务，也不会访问真实付费供应商：

```text
uv run python scripts/check.py all --profile offline
uv run python scripts/check.py task P5-02
```

逐任务命令与合并实现映射见[ACCEPTANCE](docs/ACCEPTANCE.md)。真实模型P4-03和发布P5-03保留独立门槛；不能用离线成功标记PROVIDER_VERIFIED或商业发布就绪。

生产性能基准见[性能验收说明](docs/ACCEPTANCE.md)：4MP、双画布50绘制对象、60秒真实指针、20并发500次非模型API。只证明所属CI环境，不代表生产容量或真实视觉质量。

## Hubway 与运营

用户指定检测供应商候选为Hubway，默认https://hubway.cc、可显式选择https://z.hubway.cc，已在登录网站确认gemini-3.8-flash。实际密钥仅放私有服务端环境；.env.example中为空。设置环境变量不会自动将普通Worker切到付费模式。

[Hubway接入与冻结路由](docs/HUBWAY_INTEGRATION.md)区分SDK MockTransport证明和真实服务端兼容性。FLUX图片编辑API尚未在Hubway核实，不能拿聊天文本接口冒充图像生成。费用、授权及未知结果处理见[PROVIDER_BUDGET](docs/PROVIDER_BUDGET.md)。

API/Cookie/CSRF与幂等使用见[API_USAGE](docs/API_USAGE.md)；角色/健康见[READINESS_AND_ROLES](docs/READINESS_AND_ROLES.md)；产品与外观管理见[PRODUCT_MANAGEMENT](docs/PRODUCT_MANAGEMENT.md)、[APPEARANCE_EXECUTION](docs/APPEARANCE_EXECUTION.md)。

备份与独立恢复见[BACKUP_RESTORE](docs/BACKUP_RESTORE.md)。原计划初始演练目标是RPO24h/RTO4h，已有对应CI演练证明；生产备份调度、保留策略和SLA仍需目标环境验证。监控及故障处理见[MONITORING](docs/MONITORING.md)、[RUNBOOK](docs/RUNBOOK.md)；锁文件、SBOM与实际许可文本见[SUPPLY_CHAIN](docs/SUPPLY_CHAIN.md)。原始12份规范保留在[specification](docs/specification)，不覆盖或改写。

检测不可用时按证据交付既有成品、保留供应商未知费用的受限运营候选见[ADR_DETECTION_MANUAL_DELIVERY](docs/ADR_DETECTION_MANUAL_DELIVERY.md)，该分支已在上述CI整轮通过，未证明真实供应商检测或费用。


完整离线CI通过后会生成准确源码/当次前端/原始证据/实际许可文本的ZIP交付包；下载与独立校验方法见[离线交付包](docs/HANDOFF.md)。真实供应商与生产发布按各自门槛另行验收。
