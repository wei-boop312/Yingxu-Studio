# 本机隔离备份与恢复

本工具已在PostgreSQL 17.11、SeaweedFS 4.53、本机offline profile上演练。原生备份/恢复是P5-02的一部分；MQ/S3/Worker重启告警和完整运维门槛仍待验收。没有真实模型调用或部署。

## 备份内容与一致性

`scripts/backup.py create`在REPEATABLE READ READ ONLY事务导出快照，用同一快照运行pg_dump custom格式，同时枚举数据库引用的不可变原图、canonical、生成/最终图、JSON报告、规则证据。数据库各表按UTC规范化JSON计算数量/摘要，函数定义与所有者另记摘要。参见[PostgreSQL 17 pg_dump同步快照](https://www.postgresql.org/docs/17/app-pgdump.html)。

原生profile中还复制已有Fake供应商意图/结果缓存，供恢复后按已知ID查询。它只是本地模拟供应商；真实外部结果、过期URL和未入快照的提交必须单独核验。待上传临时对象可能尚不存在，会明确记录missing_at_snapshot；用户需重新上传或完成零计费校验。未引用的孤儿对象、S3全部历史版本和底层完整集群目录不在此逻辑备份中。

归档包括数据库内的用户密码哈希、会话摘要、私有图片与供应商记录，属于敏感数据；只允许写入被Git忽略的work/.local私有目录，不能上传仓库。POSIX新归档目录限制为0700；Windows沿用用户工作目录的ACL。生产留存需要受限/加密存储及独立完整性/来源验证策略。manifest.sha256用于损坏检测，不是数字签名；只恢复本工具创建且来自受信任存储的归档。JWT私钥、S3密钥和数据库LOGIN密码不写入归档。

```text
# MIGRATION_DATABASE_URL指向专属源实例DDL身份，.local/runtime.json为本机offline配置
python scripts/backup.py create --output work/backups/new-run --pg-bin <PostgreSQL-bin>
```

输出目录必须全新。备份使用只读源快照，不暂停普通数据读取；DDL/角色变更应在维护窗口外。演练为便于核对供应商提交边界，显式暂停了本项目publisher/worker。归档保留pg_dump诊断、SQL自定义归档、对象内容摘要文件及manifest；数据库摘要采用流式读取，S3单对象上限80MiB。尚未验证大规模或远程云存储吞吐。

新备份为format_version=2：从干净Git提交的对象中归档明确允许的Compose/Docker、依赖锁、规则/契约JSON、镜像清单、Nginx和恢复目标配置，并保存应用源码commit/tree及每文件SHA256；存在修改或未提交实现文件时拒绝备份。仅允许复制APP_ORIGIN、COOKIE_SECURE、PROVIDER_PROFILE、S3_REGION、QUEUE_PREFIX五项非秘密运行设置，源profile中的DSN、密码、JWT和其他新增字段均不会进入配置快照。Git commit标识备份时源码，不推断已运行进程或镜像版本。

恢复新增独立且全新的--config-output目录，先校验配置路径、完整清单、大小、摘要及字段白名单，再接触目标PG/S3。成功后该目录保存configuration/文件及application.json，逐文件读取复核；不执行脚本、不覆盖当前应用配置、不恢复密钥。旧format_version=1仍可读取，其configuration_restore明确标为unavailable_legacy_format，不能作为配置恢复已验证的证据。新版独立PG/S3演练已接入配置复核，实际整链重跑仍待完成；66单元覆盖配置正负例不是PG/S3恢复验收。

## 恢复前置条件

- `RESTORE_DATABASE_URL`必须是不同PostgreSQL system_identifier的独立新集群、空数据库和受控DDL身份。
- 目标S3必须是不同loopback endpoint且没有任何bucket；先配置目标S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET/S3_REGION。
- 输入归档摘要全部通过，新Fake缓存目录不存在。归档/输出路径仍在忽略目录内。

```text
python scripts/backup.py restore --input work/backups/new-run --fake-output work/restored-fake --config-output work/restored-configuration --pg-bin <PostgreSQL-bin>
```

源角色名和必要membership恢复为NOLOGIN、NOSUPERUSER、NOBYPASSRLS；目标自身DDL身份仅在与源DDL身份同名时保留。不会复制角色密码。pg_restore保留schema、约束、ACL及函数所有者，单事务/遇错退出；没有--no-owner把所有SECURITY DEFINER改给超级用户。仅在已验证的空目标中替换initdb的空public命名空间，无CASCADE，不删除已有业务表。参见[PostgreSQL 17 pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html)。非空目标和同源目标被拒绝。

对象按相同bucket/key复制并读取验证。数据库先与源快照逐表数量/内容、函数定义/所有者精确比对，再仅在隔离恢复DDL阶段适配新对象服务的storage_version_id；资产UUID/hash、成品像素、已有报告和规则配置不改写。本机非版本化对象服务已实测；云厂商版本化语义需另行验证。

随后撤销旧refresh/session families，并将所有queued/processing/uncertain任务变为uncertain/manual、增加fencing token和审计，保留冻结额度，不自动charge/refund。即使快照里尚无提交意图，源实例也可能在快照后付费提交，因此不能自动重发。对账必须覆盖快照之后的全部生成/检测边界，并隔离旧消费者；无法证明的缺失意图继续人工处理。只有核验过既有回执后才可用受限CLI resume查询/归档；不能把内部积分释放称为供应商退款。

使用目标MIGRATION_DATABASE_URL运行setup_local.py --profile-dir work/restore-profile可创建6个全新服务LOGIN与JWT密钥、S3 CORS；该脚本不会覆盖已有profile。将新profile的FAKE_PROVIDER_DIR指向恢复缓存。启动目标应用前核对API依赖、账本和任务隔离，禁止同时运行源/目标的生产付费消费者。

## 可复跑的原生演练

Linux CI 在普通/受控浏览器结束且自身消费者停止后运行 scripts/ci_restore.py。该入口只接受由ci_native.py创建的源PG/S3容器与对应owner标记，创建独立目标PG55434/S318334；使用固定PostgreSQL镜像里的pg_dump/pg_restore，通过同UID、仅本次归档目录的挂载执行。准备真实已交付/已知ID/未知/排队任务，完成format2备份、冷启动恢复、新身份重建、PNG/规则/人工复核/会话撤销/隔离/已知ID恢复及配置逐文件验证，最后结案源测试账号并删除自己创建的目标容器/匿名卷。清理失败同样验收失败。

报告写入work/ci-public/native-restore-v2.json，故障日志按已知密码/DSN/令牌脱敏后限量保留；数据库、对象、私钥和profile不上传。当前入口已通过静态/Windows拒绝执行预检，首次Linux整链结果仍待CI；不能把“已加入工作流”称为恢复已通过。新离线综合门槛明确要求当前提交的真实恢复报告。

PowerShell 7、原生PG与weed二进制、源.local profile和源MIGRATION_DATABASE_URL就绪后，暂停本项目publisher/worker终端：

```text
pwsh -File scripts/native_restore_drill.ps1 -PgBin <PostgreSQL-bin> -WeedBinary <weed.exe> -WorkersPaused
```

脚本建立自己的已交付/已知ID待归档/未知/排队测试任务及规则证据，在独立PG 55434和S3 18334（及独立master/volume/filer端口）恢复，使用新的服务身份/密钥验证。端口被占用会拒绝，不停止其他进程。临时进程隐藏运行并在finally关闭；测试源账号通过正常API/运营函数结案，清理可重放；失败时私有诊断与状态保留在work/native-restore-*。结束后恢复本项目publisher/worker终端。实际归档和凭据不会进入docs/evidence。

## 实测结果与边界

最新演练绑定 `f4aea8a`、schema 0024：26张表指纹精确一致、函数定义/所有者一致，321个对象引用和269个Fake缓存文件恢复校验；账本守恒、余额与流水汇总、单一终态结算检查均零异常。原已交付PNG/报告SHA、保护区RGB、人工复核及规则快照保留。三个未结束测试任务全部隔离，排队/未知尝试运行Worker不能提交；人工核验后已知ID恢复，只保留一次generate。

本轮冷目标RTO **25.14秒**，包括PG/S3初始化至API/下载及一次已知ID安全恢复验证；备份耗时1.94秒。恢复起点距离快照 **3.66秒**，快照之后创建的产品标记确实未被恢复。RTO使用Stopwatch单调计时，RPO年龄统一DateTimeOffset。旧报告写NOT_SET遗漏了主计划§13原有初始演练RPO24h/RTO4h；不回填旧证据，本轮从ops/recovery-objectives.json读取、规范化摘要并实际满足两项初始演练目标。单次快照年龄仍不是持续WAL/PITR或生产备份周期保障；本轮满足目标不证明生产备份频率或SLA，小体积单机离线数据不能外推大库/公网恢复速度。

本轮证据为backup-restore-drill.json，结合native-restart-drill.json与check.py e2e四项/当前性能记录，恢复重启性能门槛在`f4aea8a`实际通过，结果operations-gate-f4aea8a.json；最终监控objectives-and-chromium-final-monitor.json无告警、账务异常0。主计划要求的完整常驻运行指标仍见PLAN_GAPS，不能据此关闭整个P5-02或V4。原生服务/临时恢复凭据和完整日志只留忽略work，历史证据保留其原有范围。
