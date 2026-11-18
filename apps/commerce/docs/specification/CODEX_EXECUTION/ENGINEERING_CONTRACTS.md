# 工程契约 — V4.0

日期：2026-11-12。本文是实现规范，不是已经执行的迁移或可用API。与主计划配套使用；关键实体、金额和状态语义不得由不同模块各自解释。

## 1. 项目布局与依赖边界

```text
visual-production-platform/
  AGENTS.md                    # 实施时按本项目范围编写
  README.md
  pyproject.toml               # API/Worker共享包，锁Python及依赖
  uv.lock
  compose.yaml
  .env.example                 # 占位变量，无真实秘密
  apps/web/                    # React + TypeScript + Vite
  src/visual_platform/
    api/{auth,products,assets,quotes,tasks,credits,rules,reviews}.py
    core/{settings,errors,identity,logging}.py
    db/{pool,transactions,repositories}.py
    domain/{schemas,task_state,wallet,compliance}.py
    services/{upload,task_submit,reconciliation}.py
    providers/{contract,bfl,gemini,fake}.py
    imaging/{normalize,regions,paste,validate}.py
    worker/{consumer,claim,steps,recovery}.py
    events/{outbox,publisher,sse}.py
  migrations/{env.py,versions/}
  contracts/{openapi.json,task-create.schema.json,compliance.schema.json}
  rules/seeds/                 # 未验证种子，不是自动放行规则
  tests/{unit,integration,contract,e2e,fixtures}/
  scripts/{dev.py,check.py,provider_smoke.py}
  docs/{STATUS.md,DECISIONS.md,RUNBOOK.md,evidence/}
```

API、Worker、publisher 共用 domain 与数据库契约，不复制第三套状态机。图像CPU工作用隔离进程/受限执行器，boto3 用线程池；外网调用用异步传输。迁移可使用同步 psycopg，不把迁移owner连接泄露给API。

首次初始化必须把锁文件、任务脚本和实际命令写好，才可在README声称可运行。参考项目只在只读路径读取；取用时记录 source path、源commit/sha256、目标路径、修改摘要及许可。

## 2. 数据模型

### 2.1 公共字段和命名

PG schema：`app`（业务）、`auth`（凭据/会话）、`ops`（内部调度/审计）。UUID主键由服务端生成；时间 `timestamptz`，API ISO8601 UTC，界面按用户时区显示。所有可变业务记录含 created_at、updated_at；乐观锁使用 revision。金额/积分不使用二进制浮点。

用户拥有实体包含 `user_id UUID NOT NULL`；其 `UNIQUE(user_id,id)` 用于复合引用。表由专门迁移角色拥有；运行角色不可创建 schema/table/function，不可变更 RLS 或 SET ROLE 到 owner。

### 2.2 必须落地的表及核心列

下面字段和约束是最小完整业务模型。可补索引/审计时间等内部字段，但不能删除租户约束或以 JSON 自由字段替代关键唯一性、状态与金额约束。

| 表 | 核心列 | 主键、唯一性及关系 |
| --- | --- | --- |
| app.users | id、email_normalized、display_name、status(active/disabled)、created_at、updated_at | PK(id)；UNIQUE(email_normalized)；无password_hash或可写余额 |
| auth.credentials | user_id、password_hash、password_version、updated_at | PK/FK(user_id→users)，删除RESTRICT；仅auth角色可读 |
| auth.refresh_sessions | id、user_id、family_id、token_digest、expires_at、revoked_at、replaced_by、created_at | PK(id)，UNIQUE(token_digest)；轮换与检测重用同事务 |
| app.products | id、user_id、name(1..255)、category、revision、archived_at、created_at、updated_at | PK(id)，UNIQUE(user_id,id) |
| app.appearance_profiles | id、user_id、product_id、name、config JSONB、revision、archived_at | FK(user_id,product_id)→products(user_id,id)；配置用Pydantic校验 |
| app.assets | id、user_id、product_id、kind(original/generated/final/preview)、status(uploading/validating/ready/rejected)、current_version_id、created_at | UNIQUE(user_id,id)，UNIQUE(user_id,product_id,id)；产品复合FK；版本可空直到ready |
| app.asset_versions | id、user_id、product_id、asset_id、version_number、bucket、object_key、storage_version_id、sha256、mime、width、height、byte_size、color_space、canonical、source_version_id、created_at | UNIQUE(user_id,id)，UNIQUE(user_id,product_id,asset_id,id)，UNIQUE(asset_id,version_number)，UNIQUE(bucket,object_key)；asset归属复合FK；尺寸/字节>0 |
| app.upload_intents | id、user_id、product_id、asset_id、temp_key、expected_mime、expected_size、expires_at、status、completed_version_id | 临时key唯一；完成接口幂等，只有ready版本可作输入 |
| app.platform_rules | id、platform、marketplace、category、image_type、version、evidence_status、source_urls JSONB、retrieved_at、config JSONB、config_sha256、published_at | UNIQUE(platform,marketplace,category,image_type,version)；发布后不可变；用户只读 |
| app.quotes | id、user_id、operation、input_hash、input_snapshot JSONB、price_version、provider_model、rules_snapshot JSONB、credits BIGINT、expires_at、consumed_task_id | credits>0；UNIQUE(consumed_task_id) WHERE NOT NULL；不可变报价内容 |
| app.generation_tasks | id、user_id、product_id、appearance_profile_id(nullable)、source_asset_version_id、operation、idempotency_key、request_hash、quote_id、status、phase、credits_status、reserved_credits、input_snapshot JSONB、input_sha256、final_version_id、compliance_id、cancel_requested_at、lease_owner、lease_until、fencing_token、revision、error_code、created_at、updated_at、completed_at | UNIQUE(user_id,operation,idempotency_key)，UNIQUE(quote_id)，UNIQUE(user_id,id)；输入与输出归属复合FK |
| app.wallets | user_id、available BIGINT、reserved BIGINT、funded_total BIGINT、spent_total BIGINT、revision、updated_at | PK/FK(user_id)；各金额>=0；CHECK(available+reserved=funded_total-spent_total) |
| app.credit_transactions | id、user_id、task_id(nullable)、operation_id、type(grant/freeze/charge/refund)、amount BIGINT、available_delta、reserved_delta、funded_delta、spent_delta、available_after、reserved_after、actor_ref、reason、created_at | amount>0；UNIQUE(operation_id)，UNIQUE(task_id,type) WHERE NOT NULL；task/user复合FK；不可UPDATE/DELETE |
| app.provider_attempts | id、user_id、task_id、step(generate/detect)、attempt_number、provider、model、status、intent_at、request_hash、local_request_id、provider_id、polling_url_private、response_digest、error_code、submitted_at、last_polled_at、finished_at | UNIQUE(task_id,step,attempt_number)；一次意图对应至多一次提交；task复合FK |
| app.task_cost_records | id、user_id、task_id、attempt_id、cost_record_kind(estimate/settlement)、provider、model、currency、amount NUMERIC(20,8) nullable、cost_status(estimated/confirmed/unknown)、usage JSONB、price_source、price_version、observed_at | UNIQUE(attempt_id,cost_record_kind)；unknown不可写0冒充已确认 |
| app.compliance_checks | id、user_id、task_id、final_version_id、final_sha256、facts JSONB、facts_schema_version、rule_snapshots JSONB、per_platform_results JSONB、auto_overall、sampling_selected、report_sha256、created_at | 每份报告绑定具体最终版本/hash；同一份自动报告不可改写 |
| app.compliance_reviews | id、user_id、check_id、reviewer_id、decision、reason、evidence_refs JSONB、created_at | append-only；保留原auto_overall，单独计算effective结果 |
| app.task_events | id BIGINT identity、user_id、task_id、type、payload JSONB、created_at | PK(id)，INDEX(user_id,task_id,id)；持久SSE源，终态事件同事务写 |
| ops.outbox | id、user_id、task_id、event_type、dedupe_key、created_at、publish_attempts、next_attempt_at、locked_until、published_at | UNIQUE(dedupe_key)，INDEX(next_attempt_at) WHERE published_at IS NULL |
| ops.task_dispatch | task_id、user_id、ready_at、lease_until、fencing_token、state | PK(task_id)；仅内部调度器读，用来定位任务归属；与task同步事务更新 |
| ops.audit_events | id、actor、action、target_type、target_id、reason、evidence_digest、before_state、after_state、request_id、created_at | append-only；授额、规则发布、未知结案必留审计 |

原 `consistency_checks` 的用途由 `compliance_checks` 承接；如果已有实际历史数据，迁移应保留兼容视图与字段映射，不能直接DROP。新项目空库不导入旧日常库数据。

### 2.3 必须出现的约束/索引

```sql
-- 片段是实现要求示例，必须纳入实际可执行Alembic迁移并验证。
UNIQUE (user_id, id)                         -- products/tasks等对应实体
FOREIGN KEY (user_id, product_id)
  REFERENCES app.products(user_id, id) ON DELETE RESTRICT

CREATE UNIQUE INDEX credit_once_per_task_type
  ON app.credit_transactions(task_id,type) WHERE task_id IS NOT NULL;
CREATE UNIQUE INDEX one_generation_intent
  ON app.provider_attempts(task_id)
  WHERE step='generate';
CREATE UNIQUE INDEX one_detection_intent
  ON app.provider_attempts(task_id)
  WHERE step='detect';
```

MVP 每task只允许一次生成和一次检测调用，因而 attempt_number 默认1。新的一次付费再试创建新task和quote，而不是增加attempt绕过次数上限。poll和本地步骤不是新的付费attempt。

对 asset_versions、task 的 source/final/product 统一复合引用；`assets.current_version_id` 与所属asset的四列复合FK可用 DEFERRABLE INITIALLY DEFERRED解决创建顺序。task的source_asset_version_id/final_version_id均与(user_id,product_id)一起引用同产品的版本；appearance_profile_id同样验证产品归属。input_snapshot不是唯一的引用完整性手段。

检查状态枚举、金额非负、revision递增、final尺寸正数；`status=succeeded` 时 final_version_id、compliance_id、completed_at 必填。`queued`不得已含供应商意图。账本的grant无task，其余必须task；钱包与task所属user必须一致。

历史规则或版本被任务引用时不能删除；产品归档后拒绝新上传/报价/任务，但历史结果仍可由owner下载。账号停用立即拒绝新提交与下载，已付费任务允许内部归档和对账，不能重新提交。

## 3. 权限矩阵与可信身份

| 角色/连接 | 能力 | 拒绝 |
| --- | --- | --- |
| app_owner/migration | 执行DDL、角色和受控函数发布；仅迁移进程使用 | 不用于API/Worker常驻连接 |
| app_api（NOLOGIN权限组） | 当前用户产品/配置读写、ready资产与任务/账本只读、调用有限task/上传函数 | 直接写wallet/ledger、改任务状态、访问credentials/ops私密字段 |
| app_worker（NOLOGIN权限组） | 受控claim/step/settle函数、所领任务的必要资产读写 | 任意授额、访问密码、随意改产品、客户端直连 |
| app_auth | credentials与refresh_sessions读写、读取必要身份 | 图像/任务/账本写权限 |
| app_readonly | 仅显式安全视图及其底层必要列，仍按user隔离 | password、token、供应商原始payload、全库分析绕过 |
| ops_dispatcher | 读取内部dispatch/outbox、领租约和发布标记，有限claim函数 | 账务授额、凭据、用户私图 |
| ops_admin | 管理CLI调用授额/规则发布/人工结案函数，写审计 | 公开HTTP任意加积分 |

LOGIN服务账号只属于对应权限组，显式NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE；API不能SET ROLE为worker/admin。撤销PUBLIC schema CREATE、敏感函数PUBLIC EXECUTE及旧ALTER DEFAULT PRIVILEGES广泛授权。

每次租户业务事务的入口：

```python
async with pool.acquire() as conn:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', $1, true)", trusted_user_uuid)
        # 只做短时数据库查询和写入；不请求模型、不等待MQ、不跑Pillow。
```

RLS 使用 `NULLIF(current_setting('app.user_id',true),'')::uuid`；无上下文看不到行，非法格式拒绝。写策略显式 `USING/WITH CHECK`；安全视图 `security_invoker=true` 并授必要底层列。密码凭据不再依靠“SELECT *恰好不被调用”来隐藏。

上述tenant GUC策略适用于app内用户业务表。登录前尚无user上下文：auth内表使用仅限app_auth的专用RLS策略及最小列权限，认证模块按规范化email/token digest进行参数化查询；不能把认证所需权限给app_api。ops内调度表同样采用仅允许对应内部角色的专用策略，业务用户不可访问。平台已发布规则属于全局只读配置，使用明确的发布状态/读取策略，不能因缺user_id强塞通用tenant策略。

Worker 不能先读task再凭空得到user_id。消息只含服务端写入的 task_id/outbox_id；通过内部dispatch记录验证映射，再设置tenant上下文读取task。消息体不能直接决定计费user或覆盖任务输入。调度视图/函数只暴露领取所需字段，不给全租户普通表SELECT。

## 4. 钱包和创建任务的事务

### 4.1 金额不变量

对每一用户始终 `available + reserved = funded_total - spent_total`。所有delta与type检查：

| type | amount | available_delta | reserved_delta | funded_delta | spent_delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| grant | X | +X | 0 | +X | 0 |
| freeze | R | -R | +R | 0 | 0 |
| charge | R | 0 | -R | 0 | +R |
| refund | R | +R | -R | 0 | 0 |

一个任务只允许 `none→frozen→charged` 或 `none→frozen→refunded`。已终态重放返回原记录；非法跨终态返回409。账务函数必须验证状态和任务额度，不能让调用者传任意amount。

全局锁顺序：user wallet → task（创建时如已存在）→ quote → provider attempt/step → ledger/outbox/events，代码和迁移函数统一采用，避免取消/结算/创建反向加锁。claim不操作wallet时仅锁task，释放事务后才能进入需要wallet的下一事务；禁止持task锁再嵌套申请wallet。

### 4.2 POST task 原子算法

1. 验证身份、CSRF、请求schema及幂等键；计算规范化request_hash。非可信body.user_id拒绝。
2. 先按user/operation/key读取已有任务：同hash安全重放；不同hash409。此检查不依赖quote仍有效。
3. 开短事务，锁wallet。再次查幂等（覆盖竞争）；锁quote，核验user、未过期/未消费及input_hash一致。
4. 核验产品/源版本当前可用且归属同user、规则/模型/输出参数与quote一致。余额不足返回409，事务不留残留。
5. 建task，转移R至reserved，写freeze流水，消费quote，写dispatch/outbox和queued事件。
6. 全部提交后返回201。不在此事务调用RabbitMQ。

unique冲突时读取胜出的现有记录并校验hash，不能吞掉异常再新建。任意一处失败无孤立冻结/任务/outbox。若quote已消费且使用新key，409 quote_consumed；不能利用新key对同一quote重复付费。

规范化规则在服务端唯一实现：补齐显式默认值、移除允许省略的null、字符串保持原义、对象键按字典序、UTF-8 JSON无多余空白、数字只允许业务schema声明的有限值、数组保持顺序。quote.input_hash只含标准化业务输入；task.request_hash包含operation、quote_id和同一份标准化业务输入；task.input_sha256另对包含模型/规则/报价版本的执行快照计算。三种hash不得混用。浮动时间、签名URL和客户端display字段不进入业务hash。

### 4.3 结算及取消

成功结算验证：最终S3版本已归档且校验hash、compliance绑定同一版本、task当前处理阶段与fencing匹配、无terminal ledger。wallet锁下将reserved减R、spent增R，追加charge并设task.succeeded，一次事务完成。

取消锁wallet和task，只有queued且无generate intent才可将状态cancelled，释放R，写refund事件。取消赢得锁后consumer不得发付费请求；submit意图先提交则取消409。明确失败也经同一个释放函数。系统失败但供应商已花费用，仍按内部试用政策释放用户额度并保留真实cost；不伪造供应商退款。

## 5. 任务状态和外部调用

### 5.1 任务状态表

| 当前 | 允许目标 | 前置条件 |
| --- | --- | --- |
| queued | processing | 有合法dispatch，claim成功 |
| queued | cancelled | 无付费意图，与取消竞争原子决胜 |
| queued | failed | 已确认无法执行，释放额度 |
| processing | succeeded | 最终图+报告落库，结算 |
| processing | failed | 结果确定失败/本地交付永久失败，释放额度，保留cost |
| processing | uncertain | 已有可能付费提交意图但结果/计费未知 |
| uncertain | processing | 对账找到已知供应商结果，恢复原attempt的查询/归档；禁止新submit |
| uncertain | failed | 有可审核证据的明确失败/无法交付决定，释放额度并记审计 |
| succeeded/failed/cancelled | 无 | 终态；人工纠错用新记录，不覆写历史 |

`phase`：queued / submitting / polling / archiving / protecting / detecting / validating_rules / finalizing。task总状态与phase、credits_status分离；compliance不参与task是否succeeded的系统状态判断。

### 5.2 Provider attempt

状态：`intent_recorded → submitted → polling → succeeded/failed`，任一可能已发网但未能确定结果的环节可转uncertain。若发送前校验明确拒绝、且没有网络动作，可明确failed；5xx、连接重置、超时、空body等不能仅凭错误类型假定供应商没收到。

持久化调用意图与真正HTTP发送之间无法与供应商形成单个事务，因此不能承诺跨此边界exactly-once。策略是每个生成意图只发一次，断电后的灰区保留unknown，牺牲自动可用性来防重复花费。内部UUID仅关联日志，不是供应商去重能力。

有provider id和polling_url后，GET查询可退避重试（默认1.5s起，上限15s，带jitter；到最大观察窗口后待对账），不得重新POST。查询401/403表示凭据问题，404或5xx表示查询未知，不能立即释放资金。只采用已验证的供应商终态表；未知新状态保留原始摘要并停止自动推进。

### 5.3 MQ、租约与fencing

消息JSON仅 `{schema_version:1,outbox_id,task_id}`。durable exchange/queue、persistent message、publisher confirms、mandatory return处理、manual ack、prefetch可配（默认2）、DLX与最大5次消息恢复后人工队列。MQ消息重试只重新检查DB，不授权新的付费调用。

claim 使用PG条件更新或 `FOR UPDATE SKIP LOCKED`；lease默认60s、20s续租，fencing单调递增。所有阶段写入带task_id、owner、fencing及期望revision；旧worker返回不能覆盖新状态。

HTTP返回很晚但原lease失效时，可通过只追加的attempt receipt入口记录供应商id/响应摘要，由当前持租者验证并应用；该入口不能直接结算或再提交。避免把晚到成功完全丢弃造成永久无法对账。

publisher确认后标published；确认丢失保留outbox重发。consumer完成一次可持久恢复的阶段后ack；进程在ack前死亡由MQ重投，ack后死亡由PG恢复扫描发现。不得把“租约过期”写成“重新调用generate”。

## 6. HTTP 契约

通用：`/api/v1`，JSON UTF-8；拒绝额外敏感字段；OpenAPI由实际Pydantic类型生成并纳入检查。GET列表为 `{items,next_cursor}`，默认20/max100，游标按created_at/id，不用全表读取。

错误：

```json
{"error":{"code":"insufficient_credits","message":"可用积分不足","request_id":"uuid","retryable":false}}
```

状态码：401未登录、403权限不足、404不可见资源、409冲突/余额不足/已开始不可取消、413文件过大、422参数/图像不符合要求、429容量/限流、503依赖暂不可用。不得把供应商凭据或原始报错透出。

| 方法/路径 | 主要输入/输出 | 验证及幂等 |
| --- | --- | --- |
| POST /auth/login | email/password → 安全资料和Cookie | 通用错误，IP/账号速率限制；不返回hash |
| POST /auth/refresh | refresh Cookie + CSRF → 轮换Cookie | token family重用撤销 |
| POST /auth/logout | Cookie →204 | 幂等撤销session |
| GET /auth/me | 当前安全资料 | 401或用户自身 |
| GET/POST /products | 创建name/category；列表 | owner来自身份 |
| GET/PATCH/DELETE /products/{id} | 详情/带revision更新/归档 | PATCH If-Match，DELETE保留历史 |
| GET/POST/PATCH /products/{id}/appearances | 版本化配置 | 产品同user |
| POST /assets/upload-intents | product_id、filename、mime、size → intent_id/asset_id/upload_url/headers/expires_at | 先鉴权；只允许指定临时对象 |
| POST /assets/upload-intents/{id}/complete | →200 ready版本，或明确校验错误 | 等待有界隔离解码；重放不重复建版本；服务端解码后才ready |
| GET /assets/{id} | 状态、版本、尺寸/hash | 私有访问 |
| GET /assets/{id}/download?version_id=... | →短期下载URL/302 | 重查owner；不能由客户端传bucket/key |
| POST /quotes | 与task的generation配置 →quote_id/credits/expires_at/input_hash | 无模型调用；签定输入和规则/模型/计价版本 |
| POST /tasks | 下节JSON →task/credit snapshot | 必需Idempotency-Key；同键异payload409 |
| GET /tasks、/tasks/{id} | status/phase/credits/compliance/result摘要 | 私有分页，未知状态解释 |
| POST /tasks/{id}/cancel | →cancelled/409 | queued且无意图；状态重放安全 |
| GET /tasks/{id}/events | text/event-stream | Cookie认证、Last-Event-ID、无DB长连接 |
| GET /credits/balance | available/reserved/funded_total/spent_total | server整数值 |
| GET /credits/transactions | 流水与任务关联 | 分页，个人只读 |
| GET /rules/platforms、/rules/{platform} | 版本/适用范围/证据状态 | 不返回内部未经清理payload |
| GET /tasks/{id}/compliance | 自动与effective报告、最终图hash | 权限及版本绑定 |
| POST /compliance/{id}/reviews | decision/reason/evidence_refs | 仅管理员；幂等operation_id，保留自动结果 |
| GET /health/live、/health/ready | 进程/依赖就绪 | live不泄露配置，ready检查有限资源 |

没有公开 register/recharge/API key 管理路由。私有账号、授额、发布规则和uncertain结案由CLI实施。若未来补管理员HTTP，单独扩展权限和测试。

上传complete是零计费的有界流程：短事务领取upload校验租约→释放连接→在受限进程完整解码/标准化并保存不可变对象→短事务写ready版本。最多等待20秒，超时返回明确可重新完成的错误；不得在API进程直接跑阻塞Pillow或持有DB事务。进程崩溃后upload租约过期可从同一临时对象恢复，重复complete返回已有版本；不需要先创建一个收费generation_task来验证上传。暂不使用无法恢复的进程内background task，也不返回未经实现的202后台处理承诺。

### 6.1 报价和创建任务输入

```json
{
  "product_id":"uuid",
  "appearance_profile_id":"uuid",
  "source_asset_version_id":"uuid",
  "generation":{
    "type":"white_background",
    "prompt":"在白色背景中呈现此产品，保留实物标签",
    "output":{"width":1024,"height":1024,"format":"png"},
    "protection_mode":"pixel_exact",
    "regions":[{"source_x":100,"source_y":120,"width":80,"height":50,"target_x":100,"target_y":120}]
  },
  "targets":[{"platform":"amazon","marketplace":"US","category":"generic","image_type":"MAIN"}],
  "quote_id":"uuid"
}
```

POST /quotes 使用同样业务字段但无quote_id。task请求body不含user、provider endpoint、成本、状态、balance或供应商密钥。quote中的全部generation输入固定，task只提交源配置与quote_id供服务端比对，不能请求时偷偷改报价参数。

source_asset_version必须canonical/ready且hash存在。prompt最长4000字符，最多20矩形，最多3平台。像素精确模式要求output与canonical尺寸相等且≤4MP；若原图超过该范围，返回422并让用户明确创建一个新的工作图版本后重新框选，不能静默缩小原图。无保护模式可独立选择服务端支持的输出档位。

几何前景变化无法自动证明，提交页面需源/目标框预览；用户调整target位置只平移。只要存在目标保护矩形互相重叠就422，避免后贴区域破坏前一区域的精确性。

### 6.2 task响应

```json
{
  "id":"uuid",
  "status":"queued",
  "phase":"queued",
  "revision":1,
  "credits":{"status":"frozen","amount":16},
  "result":null,
  "compliance":null,
  "idempotent_replay":false,
  "request_id":"uuid"
}
```

成功结果只含资产版本id、尺寸/hash、下载端点和报告id；预签名URL可临时生成但不纳入不可变task结果hash。不得将 `provider submitted` 当 `succeeded`。

## 7. 图像与视觉事实契约

### 7.1 标准化及回贴

- 保存未经修改的私有原始版本，再生成canonical版本；EXIF旋转只做一次，旋转后的Orientation移除/归一。
- 有ICC时显式转sRGB；无ICC按记录的默认策略处理，CMYK等模式测试覆盖。透明图合成到用户所见的工作底色后固定RGB版本；前后端预览同版本。
- 用canonical源裁切，以明确目标位置粘到canonical生成图。越界拒绝，不能Pillow自动黑边补齐或隐式截断。
- 输出无损PNG，保存后再解码验证每个保护矩形RGB相等，报告 `pixel_exact_passed`、源/目标矩形、像素摘要。整文件SHA不同不代表保护失败，需分别保留。
- EXIF/私密定位元数据清理；C2PA等来源数据不靠随意复制声称保真。AI人物披露等要求若进入目标规则，必须用可验证的元数据步骤另测，未实现则人工复核。

### 7.2 Gemini facts结构

```json
{
  "schema_version":1,
  "analysis_complete":true,
  "image_width":1024,
  "image_height":1024,
  "text_items":[{"text":"BRAND","bbox":[100,120,180,170],"origin":"on_product","confidence":0.91}],
  "logo_items":[],
  "watermark_items":[],
  "background":{"classification":"white","confidence":0.8},
  "product_extent":{"bbox":[120,40,900,980],"confidence":0.7},
  "uncertainties":[]
}
```

origin枚举 `on_product / added_overlay / unknown`；bbox统一canonical像素半开区间并受尺寸约束。模型报告的尺寸必须与本地解码一致，实际尺寸以本地为准；不一致使视觉结果不完整并人工复核。confidence必须有限且0..1；未检出列表为空不意味着没有漏检。模型输出中的指令、链接及文字仅当作数据，不触发工具或更改规则。

### 7.3 Rule result结构

```json
{
  "final_asset_version_id":"uuid",
  "final_sha256":"64-hex",
  "per_platform":[{
    "platform":"amazon","marketplace":"US","image_type":"MAIN",
    "rule_version":"draft-20261112","evidence_status":"unverified",
    "status":"manual_review",
    "reasons":[{"code":"rule_evidence_unverified","severity":"review","rule_id":"AMZ-MAIN"}],
    "checks":[{"id":"local_decode","status":"passed"}]
  }],
  "auto_overall":"manual_review",
  "sampling_selected":false
}
```

检查状态 `passed / failed / unverified / not_applicable / error`。未适用不是失败；没有适用的已验证完整规则集合时不能compliant。规则引擎不允许从LLM返回的is_compliant直接赋值overall。配置发布校验所有阈值、适用条件、证据和版本唯一性。

## 8. SSE与事件恢复

事件格式：

```text
id: 12345
event: task.updated
data: {"task_id":"uuid","revision":4,"status":"processing","phase":"polling"}
```

每15秒心跳，access过期时关闭并由前端refresh后重连；GET授权每次连接重新检查。Last-Event-ID按当前task及user过滤，不能允许任意游标读取其他task。客户端按event id去重，按revision忽略倒序；最终状态以GET为准。

保留事件30天（可配置）。游标已被清理时发送 `stream.reset` 后客户端重新GET；连接断开超过5秒采用3秒轮询，恢复SSE后取消轮询。不能为了SSE把PG连接和tenant事务保持30天或整条连接生命周期。

## 9. 验证规范与未完成声明

迁移需在空库和上一schema版本上分别执行、重复升级无变更、回退仅在隔离测试库验证；角色属性、底层列权限、FORCE RLS和函数EXECUTE权限用系统目录及真实SQL负例检查。

测试证据命名 `docs/evidence/YYYY-MM-DD/<task-id>-<check>.json/log`；敏感payload只留schema、hash与去秘密后的摘要。`check all`禁止真实BFL/Gemini网络，不能因为本地已有密钥就发送。

本文定义的表、函数、脚本和HTTP尚未实现。实际完成后更新OpenAPI、Schema、迁移和任务状态；如果偏离本文需ADR说明原因、影响和验证，不能让文档与实现并列成互相矛盾的真相。
