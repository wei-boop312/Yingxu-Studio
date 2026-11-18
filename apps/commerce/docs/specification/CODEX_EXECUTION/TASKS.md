# Codex 开发任务与验收 — V4.0

以下为待实施工作。所有验证命令都是P0需实现的项目接口，本次文档整理未运行这些未来命令。机器读取用 [tasks.json](tasks.json)。

每任务完成需同时满足产物、验收条件和真实验证；更新状态、commit/工作树标识及证据。局部修改先跑有意义的针对性检查，到阶段出口再跑相关完整集成，不以堆砌测试数量验收。

## 任务总览

| ID | 任务 | 前置任务 | 门槛 | 当前状态 |
| --- | --- | --- | --- | --- |
| P0-01 | 隔离工程与来源登记 | 无 | local | TODO |
| P0-02 | 锁定环境及基础服务 | P0-01 | native_services | TODO |
| P0-03 | 严格契约与假供应商 | P0-02 | local | TODO |
| P1-01 | 数据迁移与引用完整性 | P0-03 | native_pg | TODO |
| P1-02 | 数据库角色与事务隔离 | P1-01 | native_pg | TODO |
| P1-03 | 私有认证与会话 | P1-02 | native_pg | TODO |
| P1-04 | 钱包与不可变账本 | P1-02 | native_pg | TODO |
| P1-05 | 报价、任务与outbox原子提交 | P1-03, P1-04 | native_pg | TODO |
| P2-01 | 产品与外观API | P1-03 | native_pg | TODO |
| P2-02 | 私有上传与canonical资产 | P2-01 | native_pg_s3 | TODO |
| P2-03 | outbox发布及Worker领取 | P1-05, P2-02 | native_services | TODO |
| P2-04 | 一次付费意图与未知恢复 | P2-03 | native_services | TODO |
| P2-05 | 精确像素保护 | P2-02, P0-03 | local | TODO |
| P2-06 | 规则版本与最终图检查 | P2-04, P2-05 | local | TODO |
| P2-07 | 离线完整任务闭环 | P2-06, P1-04 | OFFLINE_BACKEND_READY | TODO |
| P3-01 | 中文页面与图片选区 | P2-02, P1-05, P2-05 | browser | TODO |
| P3-02 | SSE及轮询恢复 | P2-07, P3-01 | browser | TODO |
| P3-03 | 复核、取消与结果对比 | P3-02 | browser | TODO |
| P3-04 | 离线MVP综合验收 | P3-03 | OFFLINE_READY | TODO |
| P4-01 | BFL官方协议适配 | P2-04, P2-07 | local | TODO |
| P4-02 | Gemini官方结构化事实适配 | P2-06 | local | TODO |
| P4-03 | 最小真实供应商验收 | P3-04, P4-01, P4-02 | explicit_provider_budget | WAITING_EXTERNAL |
| P5-01 | CI与依赖产物 | P3-04, P4-01, P4-02 | local_or_ci | TODO |
| P5-02 | 运维、恢复和性能演练 | P5-01 | native_services | TODO |
| P5-03 | 发布前差距关闭与交付 | P4-03, P5-02 | release_decision | WAITING_EXTERNAL |

## 关键路径与阶段出口

P0-01→P0-02→P0-03→P1-01→P1-02→P1-03/P1-04→P1-05；同时P1-03→P2-01→P2-02。两支会合后P2-03→P2-04，图片支P2-05，汇合P2-06→P2-07→P3-02/P3-03→P3-04。前端P3-01可在上传/任务契约就绪后进行。

P4-01/P4-02的离线协议开发可提前，P4-03真实调用必须等样本、额度和密钥具备且离线验收完成。P5-01/P5-02不依赖真实费用，可继续；P5-03发布候选须关闭相关外部依赖。没有授权实际发布时交付候选即可，不能自行发布。

P3-04出口为OFFLINE_READY，P4-03出口为PROVIDER_VERIFIED，P5-03完成准备后为RELEASE_READY。平台规则资料不足可运行并人工复核，但不能把该平台标自动合规。排期按每个出口实测进度估算，不继承原稿固定25天承诺。

## P0-01 隔离工程与来源登记

依赖：无。状态：TODO。门槛：local。

输入：PROJECT_PLAN.md；resource_manifest.json；实际目标目录AGENTS.md。

输出：`README.md`；`AGENTS.md`；`docs/DECISIONS.md`；`docs/source-map.json`；`.gitignore`。

验收：

1. 新建独立项目目录，不覆盖旧项目或日常数据库。
2. 记录每个移植文件的源hash、许可、目的及修改范围。
3. ADR确认Python独立服务、矩形编辑范围、SeaweedFS替代和账务语义。
4. 没有真实密钥、自动费用授权、远端推送或旧账号配置。

验证入口：`python scripts/check.py task P0-01`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P0-02 锁定环境及基础服务

依赖：P0-01。状态：TODO。门槛：native_services。

输入：主计划§4、§13。

输出：`pyproject.toml`；`uv.lock`；`apps/web/package.json`；`前端锁文件`；`compose.yaml`；`scripts/dev.py`；`scripts/check.py`；`.env.example`。

验收：

1. doctor报告解释器、Node、包管理器、Docker及镜像支持情况。
2. postgres/rabbitmq/s3使用私有测试实例与明确镜像digest。
3. up/migrate/down与check命令实际可用，不依赖全局隐式安装。
4. 干净安装可复现；服务只暴露必要回环端口；生产无默认密码。

验证入口：`python scripts/dev.py doctor；python scripts/check.py task P0-02`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P0-03 严格契约与假供应商

依赖：P0-02。状态：TODO。门槛：local。

输入：ENGINEERING_CONTRACTS.md。

输出：`src/visual_platform/domain/schemas.py`；`src/visual_platform/providers/contract.py`；`src/visual_platform/providers/fake.py`；`contracts/`；`tests/fixtures/`。

验收：

1. 请求、结果、金额、坐标和视觉事实采用严格类型。
2. fake覆盖明确失败、超时未知、已知id、晚到结果和归档失败。
3. 离线profile阻止BFL/Gemini真实出网并统计调用次数。
4. 模型响应只是数据，不能触发工具或改规则。

验证入口：`python scripts/check.py task P0-03`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P1-01 数据迁移与引用完整性

依赖：P0-03。状态：TODO。门槛：native_pg。

输入：ENGINEERING_CONTRACTS.md§2；V2.1 SQL（修正后移植）；Workbench 001_workspace.sql（参考）。

输出：`migrations/env.py`；`migrations/versions/`；`tests/integration/test_migrations.py`；`tests/integration/test_foreign_keys.py`。

验收：

1. 全部最小实体、状态CHECK、非负余额、幂等与版本唯一约束落地。
2. 跨user product/asset/version/task引用在DB层拒绝。
3. 空库升级和上一版本升级通过；重复upgrade无变更。
4. 无级联删除财务历史；不使用DROP CASCADE生产回滚。

验证入口：`python scripts/check.py task P1-01`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P1-02 数据库角色与事务隔离

依赖：P1-01。状态：TODO。门槛：native_pg。

输入：ENGINEERING_CONTRACTS.md§3；V2.1 db_pool.py（含已知语法缺陷）。

输出：`src/visual_platform/db/pool.py`；`src/visual_platform/db/transactions.py`；`角色/授权迁移`；`tests/integration/test_rls_roles.py`；`tests/integration/test_pool_context.py`。

验收：

1. 运行角色非owner/NOBYPASSRLS，业务表ENABLE/FORCE RLS。
2. readonly视图具必要底层列权限，无法读取密码和秘密。
3. 并发请求、取消、异常、事务超时后复用连接不串user。
4. Python AST/导入通过；无从请求body取user；无外网长事务。

验证入口：`python scripts/check.py task P1-02`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P1-03 私有认证与会话

依赖：P1-02。状态：TODO。门槛：native_pg。

输入：认证路由及Cookie契约。

输出：`api/auth.py`；`core/identity.py`；`认证及session模块`；`create-user CLI`；`tests/integration/test_auth.py`。

验收：

1. 凭据独立schema，密码Argon2，RS256固定算法与iss/aud/exp校验。
2. refresh单次轮换、重放撤销、退出和停用生效。
3. Cookie安全属性、CSRF/Origin、登录限流覆盖。
4. 没有开放register、充值或用户密钥上传路由。

验证入口：`python scripts/check.py task P1-03`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P1-04 钱包与不可变账本

依赖：P1-02。状态：TODO。门槛：native_pg。

输入：ENGINEERING_CONTRACTS.md§4。

输出：`domain/wallet.py`；`账务受控函数迁移`；`api/credits.py`；`管理员grant CLI`；`tests/integration/test_wallet_concurrency.py`。

验收：

1. 100→冻结16=84/16→charge=84/0；refund=100/0。
2. 两单争抢一单余额只接受一单；重放不重复扣款。
3. charge/refund竞争只能一个终态；守恒约束与流水一致。
4. 运行角色无法直接UPDATE/DELETE流水或任意授额；受控函数权限负例通过。

验证入口：`python scripts/check.py task P1-04`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P1-05 报价、任务与outbox原子提交

依赖：P1-03, P1-04。状态：TODO。门槛：native_pg。

输入：任务请求、hash及quote消费契约。

输出：`services/task_submit.py`；`api/quotes.py`；`api/tasks.py`；`task/outbox函数`；`tests/integration/test_submit_idempotency.py`。

验收：

1. 20并发同key同hash仅一task/冻结/quote消费/outbox。
2. 同key异payload409；过期quote仍可重放已有task。
3. 注入outbox写失败后task/wallet/quote一起回滚。
4. input_snapshot固定源版本、外观、坐标、模型、规则及计价版本。

验证入口：`python scripts/check.py task P1-05`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P2-01 产品与外观API

依赖：P1-03。状态：TODO。门槛：native_pg。

输入：CRUD与归档契约。

输出：`api/products.py`；`数据repositories`；`tests/integration/test_products.py`。

验收：

1. 分页、revision冲突与跨user404。
2. 归档拒绝新任务但保留已有结果和流水。
3. 外观配置schema验证及快照不随当前配置改变。

验证入口：`python scripts/check.py task P2-01`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P2-02 私有上传与canonical资产

依赖：P2-01。状态：TODO。门槛：native_pg_s3。

输入：上传意图、S3和图像约束。

输出：`api/assets.py`；`services/upload.py`；`imaging/normalize.py`；`S3 gateway`；`tests/integration/test_assets.py`。

验收：

1. 真实S3临时上传→完成校验→不可覆写ready对象。
2. 解码炸弹/动画/MIME伪造/超限/他人key拒绝。
3. EXIF1..8、ICC/RGB、透明图预览与canonical一致。
4. complete重放不重复创建版本；存储hash、尺寸与原始版本。

验证入口：`python scripts/check.py task P2-02`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P2-03 outbox发布及Worker领取

依赖：P1-05, P2-02。状态：TODO。门槛：native_services。

输入：MQ/租约契约；Workbench localRunDispatcher.ts。

输出：`events/publisher.py`；`worker/consumer.py`；`worker/claim.py`；`ops dispatch函数`；`tests/integration/test_delivery_recovery.py`。

验收：

1. 真实RabbitMQ confirm/return/ack/DLX接线。
2. DB已提交但未发布、确认丢失重复投递均不丢任务。
3. PG SKIP LOCKED或条件更新领取；旧fencing无法写入。
4. user只从可信dispatch记录定位，不采用消息user字段。

验证入口：`python scripts/check.py task P2-03`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P2-04 一次付费意图与未知恢复

依赖：P2-03。状态：TODO。门槛：native_services。

输入：ENGINEERING_CONTRACTS.md§5；fake供应商。

输出：`worker/steps.py`；`worker/recovery.py`；`services/reconciliation.py`；`unknown对账CLI`；`tests/integration/test_provider_recovery.py`。

验收：

1. 提交前落意图，事务外请求；每task fake生成submit最多1次。
2. 提交灰区/租约失联标uncertain，余额保持冻结。
3. 有id只poll，归档失败只重试存储，不再生成。
4. 晚到receipt可追加留证但不能由旧worker结算；对账结案有审计。

验证入口：`python scripts/check.py task P2-04`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P2-05 精确像素保护

依赖：P2-02, P0-03。状态：TODO。门槛：local。

输入：V2.1 pixel_paste.py与28项历史测试；坐标与PNG定义。

输出：`imaging/regions.py`；`imaging/paste.py`；`imaging/validate.py`；`tests/unit/test_pixel_exact.py`。

验收：

1. 只做同尺寸矩形平移，严格拒绝越界/重叠/缩放。
2. CSS缩放前后映射到canonical整数像素。
3. 保存后再次解码，目标矩形RGB与source完全相等。
4. 原图版本不变；EXIF只归一一次；无法对齐不静默resize。

验证入口：`python scripts/check.py task P2-05`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P2-06 规则版本与最终图检查

依赖：P2-04, P2-05。状态：TODO。门槛：local。

输入：V2.1 rules.json（仅历史种子）；视觉事实及证据契约。

输出：`domain/compliance.py`；`api/rules.py`；`rules/seeds/`；`rule-publish CLI`；`tests/unit/test_compliance.py`。

验收：

1. 平台种子unverified，证据不足不自动compliant。
2. 检测绑定回贴后的final hash；后续改像素使旧报告失效。
3. 区分实物标识与附加水印；低置信/失败/未知来源人工复核。
4. 多平台分别结果，稳定5%抽检重放不变；确定性硬失败优先。

验证入口：`python scripts/check.py task P2-06`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P2-07 离线完整任务闭环

依赖：P2-06, P1-04。状态：TODO。门槛：OFFLINE_BACKEND_READY。

输入：已完成上传/任务/Worker/图像/规则模块。

输出：`完成与释放事务`；`私有下载与报告接口`；`tests/integration/test_offline_pipeline.py`；`docs/evidence/离线闭环证据`。

验收：

1. 真实PG/MQ/S3加fake模型完整跑通上传至下载。
2. final和报告均已存储才succeeded；成功一次charge。
3. 不合规或manual_review仍可下载，且收费策略清楚。
4. 明确失败释放，未知保留冻结；检查步骤顺序为回贴后检测。

验证入口：`python scripts/check.py task P2-07`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P3-01 中文页面与图片选区

依赖：P2-02, P1-05, P2-05。状态：TODO。门槛：browser。

输入：主计划§10；changxiang交互参考（只读）。

输出：`apps/web/src/登录/产品/上传/任务/余额页面`；`Konva选区组件`；`浏览器真实指针测试`。

验收：

1. 缩放/平移/框选与target位置预览可用且坐标正确。
2. 产品/节点切换参数不串；刷新保留未提交配置。
3. 防连点用同幂等键；普通界面不出现key/endpoint等内部字段。
4. 编辑和浏览操作模型调用计数保持0。

验证入口：`python scripts/check.py task P3-01`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P3-02 SSE及轮询恢复

依赖：P2-07, P3-01。状态：TODO。门槛：browser。

输入：task_events与SSE契约。

输出：`events/sse.py`；`前端任务订阅`；`tests/integration/test_events.py`；`SSE浏览器场景`。

验收：

1. Last-Event-ID持久恢复、重复/乱序忽略、清理游标reset。
2. 会话过期refresh后重连；断线回退轮询不重复订阅。
3. SSE无URL token、无长PG事务；跨user/task游标隔离。

验证入口：`python scripts/check.py task P3-02`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P3-03 复核、取消与结果对比

依赖：P3-02。状态：TODO。门槛：browser。

输入：取消/报告/人工审计契约。

输出：`api/reviews.py`；`结果/原图/回贴对比页面`；`复核CLI或受限管理页`；`取消竞争测试`。

验收：

1. 排队取消一次释放；付费意图已创建则409并清楚解释。
2. 人工决定不改原auto报告；留操作者、理由和证据。
3. unknown不出现普通重试按钮；不误称为失败或免费。
4. 最终图下载hash、版本及报告一致。

验证入口：`python scripts/check.py task P3-03`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P3-04 离线MVP综合验收

依赖：P3-03。状态：TODO。门槛：OFFLINE_READY。

输入：所有P0-P3产物。

输出：`tests/e2e/核心用户场景`；`docs/STATUS.md`；`docs/evidence/offline-acceptance.json`。

验收：

1. 登录→产品→上传→选区→报价→任务→下载→余额完整通过。
2. 重开、会话过期、跨用户、断线、失败与uncertain场景通过。
3. 生产构建下真实指针操作与50对象基准记录。
4. 标OFFLINE_READY并明确尚未真实模型验收。

验证入口：`python scripts/check.py all --profile offline`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P4-01 BFL官方协议适配

依赖：P2-04, P2-07。状态：TODO。门槛：local。

输入：官方FLUX 3 Image生成/编辑文档。

输出：`providers/bfl.py`；`tests/contract/test_bfl.py`；`供应商字段映射说明`。

验收：

1. 正确endpoint/x-key/images/polling_url/Ready/result.sample。
2. schema校验、鉴权主机与结果CDN隔离、不泄露key。
3. 成本保持币种与来源，未核实价格不填猜测值。
4. mock契约验证不是已真实调用，文档明确。

验证入口：`python scripts/check.py task P4-01 --profile offline`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P4-02 Gemini官方结构化事实适配

依赖：P2-06。状态：TODO。门槛：local。

输入：gemini-3.8-flash官方模型文档及锁定SDK。

输出：`providers/gemini.py`；`tests/contract/test_gemini.py`；`facts schema`。

验收：

1. 官方SDK封装、图片输入与严格结构化输出校验。
2. 截断/阻止/缺字段/低置信全部降级人工。
3. 一次检测submit上限；usage与实际费用不伪造。
4. 调用最终PNG，不能由LLM直接决定平台compliant。

验证入口：`python scripts/check.py task P4-02 --profile offline`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P4-03 最小真实供应商验收

依赖：P3-04, P4-01, P4-02。状态：WAITING_EXTERNAL。门槛：explicit_provider_budget。

输入：用户明确给定的样本、模型、调用次数及费用上限；安全配置密钥；供应商可访问的受限图片传输方案。

输出：`scripts/provider_smoke.py`；`docs/evidence/provider-smoke.json`；`真实归档/最终图报告与成本证据`。

验收：

1. 默认一次BFL+一次Gemini，额度授权足够且不自动扩增。
2. 供应商能够读取输入（不得发送localhost/private S3不可达URL）。
3. 出现超时/未知即停止submit，保留供应商和本地关联证据。
4. 真实图归档、回贴、最终检测和费用可核对；才标PROVIDER_VERIFIED。

验证入口：`python scripts/provider_smoke.py --config <授权范围配置>`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P5-01 CI与依赖产物

依赖：P3-04, P4-01, P4-02。状态：TODO。门槛：local_or_ci。

输入：锁文件与测试矩阵。

输出：`.github/workflows/ci.yml`；`生产Dockerfile`；`SBOM/NOTICE`；`API与运行说明`。

验收：

1. CI运行原生PG/MQ/S3和浏览器，无付费秘密也能全绿。
2. 安装锁定且干净重建，产物有版本和digest。
3. 秘密扫描/依赖检查，源码复制遵守实际许可。
4. 仅构建验证，不默认推生产或向旧仓库写入。

验证入口：`python scripts/check.py task P5-01`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P5-02 运维、恢复和性能演练

依赖：P5-01。状态：TODO。门槛：native_services。

输入：Compose配置与主计划运维目标。

输出：`docs/RUNBOOK.md`；`backup/restore脚本`；`metrics与告警`；`生产构建压测报告`。

验收：

1. DB+S3+规则快照恢复到独立实例并核对数量/hash和账本守恒。
2. 原生多连接下20并发500请求p95目标与失败样本记录。
3. 模拟Worker/MQ/S3重启及unknown积压告警。
4. 报告RPO/RTO实测值，达不到目标如实保留。

验证入口：`python scripts/check.py task P5-02`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。

## P5-03 发布前差距关闭与交付

依赖：P4-03, P5-02。状态：WAITING_EXTERNAL。门槛：release_decision。

输入：用户目标服务器/地区/域名和发布决定；适用平台/类目官方规则证据；实际代码/依赖/素材权属资料。

输出：`发布候选说明`；`未决项及风险清单`；`部署预演证据`；`最终状态报告`。

验收：

1. 只关闭有证据的未决项，不把manual_review种子冒充平台全合规。
2. 目标环境密钥、备份和恢复验证，真实费用策略可见。
3. 完整交付代码/文档/测试/已知限制，引用对应commit。
4. 用户授权发布后才部署；未授权保持可审阅发布候选。

验证入口：`python scripts/check.py task P5-03（部署预演）；实际发布另按用户明确指令`。

完成证据：实际命令、exit code、环境/版本、结果与未通过项、日志/报告路径；未执行的检查明确NOT_RUN。
