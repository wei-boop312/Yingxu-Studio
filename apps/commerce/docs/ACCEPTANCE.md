# V4 验收入口与范围

原始 `specification/CODEX_EXECUTION/tasks.json` 是规划快照，不修改其中 TODO。实现状态以当前提交的真实测试、CI 和 STATUS 为准；命令存在不等于任务通过。退出 0 为指定范围通过，1 为失败（含缺失测试和跳过），3 为环境/本次证据不足，4 为等待外部授权或发布条件。

所有 Python 命令使用项目 `.venv`（或 `uv run`），工作目录为仓库根。默认离线，不需要供应商 API 密钥，不执行付费模型调用。`check.py all`等同`all --profile offline`；`check.py e2e`要求本项目离线5173 profile、生产dist及已运行原生服务，再执行常规生产浏览器全套，不隐式暂停消费者。

| 入口 | 实际验证及实现映射 |
| --- | --- |
| `check.py task P0-01` | 12 份导入规范原始/Git 摘要和目录登记；原始字节与记录的换行规范化严格区分。 |
| `check.py task P0-02` | Docker Compose 独立临时项目：空库升级、上一版升级、bootstrap、6 角色/RLS/API、S3/MQ 与保卷重建。需要 Docker 和空闲回环端口。 |
| `check.py task P0-03` | 严格类型、规范化、模型事实不可携带规则/工具、丢失响应可查询而不重发。 |
| `check.py task P1-01` | `test_migrations/foreign_keys/accounting` 真实 PG，再运行独立 Compose 证明空库/上一版升级。现有已迁移数据库不替代新库证据。 |
| `check.py task P1-02` | AST/导入、原生运行角色、readonly 权限及连接池事务上下文。原计划 `test_rls_roles` 合并于 `test_native/readonly`。 |
| `check.py task P1-03` | `test_auth_api`：Argon2、私有账户、会话轮换/重放/注销/停用、CSRF/Origin/限流/跨用户。 |
| `check.py task P1-04` | `test_accounting/task_api`：真实账本守恒、双终态竞争、20 同步请求重放、不同产品争抢同一余额。原计划 wallet 专项合并于此。 |
| `check.py task P1-05` | `test_task_api`：报价快照、过期重放、20 同步 HTTP 请求、outbox 失败原子回滚。 |
| `check.py task P2-01` | `test_auth_api/task_api`：产品分页/revision/归档、跨用户、外观快照不随编辑变化。 |
| `check.py task P2-02` | `test_normalize/assets`：真实私有 S3、不可变版本、上传重放、解码/方向/ICC/透明图及越权负例。 |
| `check.py task P2-03` | `test_broker/worker_db`：原生 Rabbit confirm/return/ACK/DLX、数据库领取、fencing。 |
| `check.py task P2-04` | `test_late_fake/worker_db/reviews_reconciliation/protocol_pipeline`：持久化意图/回执、未知冻结、仅查询/归档、不重发、旧 worker 与运营审计。 |
| `check.py task P2-05` | 像素/规范化/完整闭环，另要求本次生产浏览器指针/CSS 映射证据，门槛与综合离线一致。 |
| `check.py task P2-06` | `test_rules/facts/rule_operations/reviews_reconciliation`：未核实种子、最终 hash、复核、规则快照及重检。 |
| `check.py task P2-07` | 两套原生完整 Worker 流水线：离线 Fake 及官方适配器 MockTransport；真实 PG/MQ/S3、上传至最终下载、账务及未知安全。 |
| `check.py task P3-01` 至 `P3-04` | 当前提交完整生产浏览器及受控故障证据（4+5）、构建资产 SHA、性能及原生集成。复用综合门槛，禁止历史截图替代本次运行。 |
| `check.py task P4-01 --profile offline` | BFL 官方 HTTP 协议 mock；不是实际模型调用。 |
| `check.py task P4-02 --profile offline` | Gemini 官方 SDK + MockTransport；不是实际模型调用。 |
| `check.py task P4-03` | WAITING_EXTERNAL，未调用模型，需单独明确费用上限及真实验证授权。 |
| `check.py task P5-01` | 静态 API/固定版本/法律文本/SBOM + 本次测试/Compose/镜像/审计证据；非 root 镜像在本 Docker engine 中实际存在。 |
| `check.py task P5-02` | 核对本次恢复/重启/性能演练和当前迁移。需先按运维文档在专用环境实际演练；旧报告返回 3，不伪造为 PASS。 |
| `check.py task P5-03` | WAITING_EXTERNAL；目标环境、政策证据、真实模型及发布决定未满足，不自动部署。 |

P1/P2 原生入口需显式配置专用 `TEST_DATABASE_URL`；涉及存储/流水线的 P2 入口还需 `TEST_AMQP_URL` 和 `TEST_S3_*`。缺失返回 3，不跳过。不在共享日志打印凭据。P1-01 会另建临时 Compose 项目；不能在占用同端口的日常环境旁直接执行。

`python scripts/check.py all --profile offline` 是本次证据综合门槛，而非一条隐式安装/启停命令。干净 Linux CI 依次进行锁定安装、单元/契约、依赖审计/SBOM、秘密扫描、生产构建、`ci_native.py`（原生服务与浏览器）、P0-02、两个镜像构建、`container_smoke.py`（实际镜像离线任务闭环）、P5-01，再执行 all。完整可复现命令以 `.github/workflows/ci.yml` 为准。只接受同一 commit、当前构建、无跳过的全量证据；实现文件未提交则拒绝验证。成功状态只为 OFFLINE_READY，范围为当前实现的P0–P3、P4 mock契约及P5-01/P5-02 CI演练。原规范仍逐项复核，不能仅凭标签认定完整V4。P4-03/P5-03 仍待外部条件；OFFLINE_READY 不等于真实模型或发布验收。

P5-02 的 Windows 原生准备见 OPERATIONS_RECOVERY / BACKUP_RESTORE：专用 RuntimeRoot 重启演练、暂停本项目消费者后的独立恢复演练、恢复消费者和生产 preview 后执行 performance.spec.ts。两份演练写 base_commit，性能证据也绑定 commit；恢复 schema 必须等于当前 Alembic head。`ops/recovery-objectives.json`记录主计划§13原有的初始演练RPO24h/RTO4h及来源SHA；新恢复报告记录规范化目标摘要和逐项目标结果，门槛重新计算并拒绝超标或旧目标报告。P5-02 不把单节点 stop_app/start_app 外推为 Rabbit 整机或生产集群容灾；单次快照年龄不证明生产备份频率或SLA。

完整all要求当前application-compose.json（8服务、回环、健康、资源、优雅退出/清理标记与持久缓存重建）、frontend-sse-cursor.json（重复/旧revision/刷新/自动头/reset/账号隔离）、format2独立冷恢复、运行指标和operations-gate.json。41712be/CI43426595570已实际全部通过，原始报告见evidence/ci-43426595570。缺失、失败或旧提交证据仍返回3或1；不把早期较窄绿灯扩展到新提交。

当前0026修正原task-create.schema要求的Amazon/Temu/Ozon；旧基线误用eBay/Shopify不能证明该要求。新增原生三平台报价/交付/规则报告和SQL负例、生产浏览器草稿/站点参数都须经新CI验证，详见PLATFORM_SCOPE。真实供应商原子预算/live接线及P4-03/P5-03外部条件仍未完成。
