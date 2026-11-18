# 本机离线运维手册

适用范围：已验证的Windows原生PG17.11、RabbitMQ4.4.0、SeaweedFS4.53及offline Fake供应商。不是生产部署授权或集群高可用证明。凭据只通过忽略的`.local/runtime.json`或私有环境变量加载；禁止把连接串、签名下载URL、JWT私钥或备份归档提交Git。

## 日常启动与观察

先启动独立PG、MQ、私有S3，升级迁移，再分别运行`python scripts/run_local.py api`、`publisher`、`worker`。前端先构建，再运行`python scripts/run_local.py preview`。生产构建以5173端口验收；开发服务不作为生产构建性能证据。六个数据库运行角色、独立初始化与API健康检查见[READINESS_AND_ROLES.md](READINESS_AND_ROLES.md)。

`GET /api/v1/health/live`仅表示API进程存活；`GET /api/v1/health/ready`检查PG api/auth连接池及S3 HeadBucket，可能有短缓存。`python scripts/run_local.py monitor`补充MQ队列、Publisher/Worker心跳、任务积压与财务计数。HeadBucket成功不保证已有对象可立即读取：本次S3重启遇到master/volume定位恢复窗口，应再验证已知私有对象的SHA和带签名下载。不要通过重复生成新任务测试存储恢复。

## 故障分流

| 告警或症状 | 处理与恢复证据 |
|---|---|
| 钱包等式、流水总额、冻结关联或重复终态异常 | 保留快照和审计，暂停新增收费入口并排查原始事务；禁止直接改余额或用新流水“补平”掩盖异常。恢复前复核全部相关不变量为0。 |
| unknown积压 | 保留冻结，使用独立ops_admin执行`python scripts/admin.py inspect --task <UUID>`；核对供应商既有ID/回执和费用证据，再按受限CLI接纳回执或人工结案。严禁重发生成请求、删除意图或自动退款。 |
| outbox积压/MQ不可用 | 检查本项目节点、vhost、队列和持久化目录，恢复既有节点；publisher依赖confirm后标记发布，未知确认可重投同一事件。不要清空队列或直接标记published。观察积压年龄下降，重复投递必须不重复执行模型/结算。 |
| Worker心跳超时 | 核对该项目进程身份与私有profile，重启同一消费者；检查租约/回执及unknown数量。心跳恢复不足以证明任务闭环，还需离线新样本生成、报告与单次结算。 |
| S3不可用/对象读取失败 | 保护原数据目录和访问策略，恢复服务；等待master/volume可读后核验已有对象SHA、不可覆写冲突及签名访问，匿名访问仍应拒绝。已有意图不能因上传失败而重发供应商。 |
| 人工复核/旧规则报告 | 使用`admin.py review`或规则重检CLI，保留operation-id、原因和证据；新报告不可覆盖旧快照，人工通过不改变auto_overall事实。 |

完整告警阈值、只读权限与模板见[MONITORING.md](MONITORING.md)；运营对账及UI验证见[CONTROLLED_BROWSER.md](CONTROLLED_BROWSER.md)。CLI参数使用`python scripts/admin.py --help`及子命令帮助，不在共享日志输出运营明细。

## 受控原生重启演练

只对本项目独立测试环境运行：`pwsh -File scripts/native_restart_drill.ps1 -RuntimeRoot <隔离运行时work绝对路径>`。脚本要求固定本机测试端口；S3停止前核对监听PID、可执行文件、数据目录、命令行和创建时间；Rabbit控制命令固定独立节点visual_test@localhost及私有cookie/ePMD端口。不要对日常其他服务运行。失败保留私有诊断和fixture状态，finally尝试恢复已经停止的服务；确认实际进程/监听状态后处理，不因观察超时启动重复服务。

演练先投递专用持久化quorum消息并不确认，再执行Rabbit `stop_app/start_app`（Erlang VM未退出）；断开期间MQ严重告警，恢复后消息redelivered且内容相同，ack后专用队列为空。S3使用硬停止并启动新进程、同一数据目录；断开期间storage严重告警，恢复后对象SHA不变、等长不同内容覆写被拒、签名下载成功、匿名拒绝。成功后只删除专用fixture队列/对象；已有业务队列和资产保留。输出脱敏结果`docs/evidence/native-restart-drill.json`，私有日志留在忽略work目录。

首轮fixture短覆写触发文件大小异常；已清理并改成等长冲突。第二轮HeadBucket成功后对象GET仍超时；已清理、增加有界真实对象读恢复探测，第三轮完整演练通过。该记录不抹去早期失败；未证明Rabbit整个Erlang VM/操作系统重启、跨主机故障或生产集群容灾。不能把此单节点演练外推为高可用保证。

本轮对象读取到完成SHA/不可覆写/私有下载验证用时13.101秒，s3_get_calls=1表示应用层调用次数，SDK内部可能重试，不能解释为一次网络请求；该时间不是从服务停止开始的完整RTO。失败fixture确认服务恢复后可用`python scripts/restart_probe.py cleanup --state <私有state.json>`清理，仅操作记录中的专用队列和对象。

重启后未重新启动Publisher/Worker，原常驻进程依赖重连恢复。生产preview的`pnpm --dir apps/web exec playwright test smoke.spec.ts recovery.spec.ts`三项通过，包括新任务真实MQ闭环、保护像素下载验证、报告版本/会话/账号与产品草稿；原生服务`python scripts/check.py integration`74项全回归通过。日志分别为restart-browser-20261113.log与restart-native-full-20261113.log，最终监控restart-final-monitor.json为空告警、财务异常全0。未重复60秒性能测试，已有性能证据仍见PERFORMANCE.md。

## 备份、恢复及变更

按照[BACKUP_RESTORE.md](BACKUP_RESTORE.md)导出同步PG快照和引用S3/规则证据；恢复到不同system_identifier的空PG及独立空S3。先核对表/函数所有者/对象SHA，再撤销旧会话、清空旧心跳并隔离所有未结束任务，保留冻结和审计。只允许对核实过的既有回执恢复查询/归档。不得直接把恢复快照中的排队任务重新消费。

f4aea8a本轮恢复实测RTO25.14秒、恢复起点快照年龄3.66秒。主计划§13初始演练目标RPO24h/RTO4h现由ops/recovery-objectives.json版本化，本轮恢复报告绑定目标摘要并满足两项初始演练目标，结果operations-gate-f4aea8a.json。生产数据量/网络/备份频率仍未验证。数据库schema不自动降级；变更前做备份并验证候选迁移，出现问题先保全已提交账本与供应商意图，再决定兼容性修复或独立实例恢复。35694a4离线核心CI门槛已绿，但完整应用Compose、完整运行指标、真实供应商及目标发布仍见PLAN_GAPS，不宣告RELEASE_READY。

## Linux CI 同提交运维组合

`scripts/ci_native.py` 在独立冷恢复和服务心跳恢复后调用 `scripts/ci_restart.py`；入口只接受CI及父运行32位owner标识，逐次inspect核对容器标签。停止/启动同一MQ/S3容器，保留卷，通过真实监控及私有随机队列/对象验证恢复；finally恢复所属容器并清理fixture，清理失败也判FAIL。不会部署或调用供应商。Windows独立入口保留，但历史证据不能满足当前commit门槛。

`check.py task P5-02` 现在组合当次恢复、重启、生产构建性能及全量运行指标，输出 `work/ci-public/operations-gate.json`；完整 `check.py all` 必须交叉校验该当前commit报告。仅证明单节点CI演练及初始RPO24h/RTO4h目标，生产备份频率及SLA仍未验证。新增组合需实际CI通过后才能认定完成。
