# 聚合监控与进程心跳

0025业务聚合已在0008ce1/CI43413351842整轮通过，94项原生包括去重阶段、不同费用优先级、未知金额/币种和越权负例，实际快照见evidence/ci-43413351842。请求/归档采集随后也在cd4dc47/CI43416750729整轮通过：943个实际API样本、p95 89.82ms、5xx 0及一次明确归档故障/安全恢复；原始报告见evidence/ci-43416750729。以下说明其范围：

API ASGI在响应最后一个body发送完成或应用异常时写非秘密JSON，排除health与任务SSE；网络中断和未完成响应单独计数，响应完成后的后台任务不延长HTTP延迟。S3不可变写入连同读回SHA校验记录success/failure，重复幂等写仍是一次操作，不冒充新资产或供应商计费调用。日志只有随机观测ID、UTC时间、耗时、状态/结果，无请求路径/参数/正文、对象key、URL、Cookie、密码或异常内容。

用`python scripts/monitor.py --api-log <API日志> --archive-log <API日志> --archive-log <Worker日志>`指定实际运行日志，可重复参数包含轮转文件；日志不得上传。采集最多8文件、每文件32MiB、100000条唯一观测，24小时窗口内以nearest-rank计算精确样本p95。重叠轮转按ID去重，冲突、截断或非法记录使采集不能通过；无API/归档样本时指标为null，绝不伪造0。完整性只针对提供的文件，不证明生产所有实例或日志轮转均已覆盖；运维必须监控日志运输/保留及缺口。

新CI会汇总所属API/Worker日志及一次明确归档写入故障fixture，只从fixture读取归档事件，避免将其TestClient调用混入常驻API延迟；检查至少500个真实API样本、失败及恢复归档、DB阶段/供应商/费用/人工率。runtime-metrics报告须当前提交、来源完整、数字与金额单位/状态一致后才能通过综合门槛。cd4dc47的78单元/96原生及整链指标报告已通过；本轮格式/类型整理后的原生回归仍需新CI，P5-02当前提交的重启/性能/恢复全组合也仍须对应证据。

0025通过`ops.business_metrics_snapshot()`提供24小时业务聚合，monitor JSON的business_metrics字段包含：合并连续重复事件后的已结束阶段墙钟跨度、供应商提交意图/已知回执/未知状态数量、每个attempt优先采用settlement→response→intent→estimate的一项费用观察，以及成品规则检查人工率。费用按供应商/币种/状态分组，金额以十进制字符串保留；未知币种和未知金额仍是null，绝不把估算与结算双计或跨币种相加。平台规则重算属于新的检查记录，人工复核决定不冒充自动规则结果。

阶段跨度只包含24小时窗口内开始并已结束的持久阶段，包含阶段内I/O/等待，不能称为CPU耗时或每个内部函数耗时。提交意图不是已确认计费HTTP调用。Prometheus增加固定阶段/供应商/步骤标签的数量、p95和人工率、未知费用次数；精确币种金额保留在JSON中。读取仍只授予受限ops_monitor/ops_admin的聚合函数；不授予其业务表、事件载荷或可切换reader身份。该函数4项原生正负例已在0008ce1的94原生测试中通过；请求/归档两个原生负例也已在cd4dc47通过。

单独的DB聚合函数仍在missing_measurements中注明API/归档指标需来自日志；CLI结合有效日志后另给measurement_coverage，完整采集证明已在cd4dc47验证；本轮源码整理需继续CI回归。历史监控证据如下。

迁移0023提供`ops_monitor` NOLOGIN组及`ops.monitor_snapshot()`。部署时创建独立非owner LOGIN，仅继承ops_monitor，通过私有环境变量MONITOR_DATABASE_URL传入；不得授予superuser、BYPASSRLS或表所有权。只返回全局数量、年龄、财务异常计数与24小时任务耗时p95，不返回用户ID、邮箱、对象地址、提示词或凭据。监控组不能直接读业务表或修改积分；本机CLI也拒绝高权限连接。

运行`python scripts/monitor.py --probe-dependencies`，或本机已有私有profile时`python scripts/run_local.py monitor`。退出码0正常、1警告、2严重、3监控无法完成。专用DSN优先；本机兼容回退到既有非owner ADMIN_DATABASE_URL。`--prometheus`生成文本；`ops/alerts.yml`为规则模板，尚未部署Prometheus/Alertmanager或配置通知接收人。运维采集器须以临时文件加原子替换写入textfile collector，并自行监控采集时间，禁止把陈旧指标当作持续健康。

连续publisher/worker每5秒写一次数据库服务时钟心跳，默认15秒过期；单任务/单次CLI不宣称常驻服务存活。心跳只是进程可访问DB，不代表每项供应商调用或MQ消费成功。可选MQ探测以非robust短连接被动检查既有generate队列；S3只做HeadBucket，不创建对象。API ready仍只代表其PG/S3依赖，不等于整个系统就绪。

财务等式、流水汇总、冻结任务与钱包关联、任务计费状态、重复终态异常为严重告警；unknown超过300秒、outbox超过30秒或租约过期为警告。告警只读，绝不自动释放冻结积分或重发供应商。阈值可用CLI参数调整；Prometheus规则中的默认阈值需同步修改。24小时任务耗时包含任务从创建到终态的全部等待，不是HTTP API延迟；无样本不导出伪造的p95。

证据：49项单元、74项原生PG/Rabbit/S3集成测试通过（monitor-unit/native-full-20261113.log）。专用LOGIN越权、unknown重复读取仍冻结、账本异常检测/回滚、心跳超时与配置错误脱敏有真实数据库覆盖。monitor-healthy.json、monitor-worker-stopped.json、monitor-worker-recovered.json记录实际常驻Worker停止后18.9秒触发严重告警，重启后恢复且全部账本异常为0。恢复脚本在精确快照核验后清空历史服务心跳，防止恢复库误报存活。

尚未在本轮完成MQ/S3真实重启演练或生产告警接收部署；不得据此宣告P5-02或整体上线验收完成。

此前所列测试数对应早期阶段；8f7687a/CI43531050538已完成完整离线P5-02、重启/独立冷恢复与全量运行指标，生产告警接收/定时采集仍未部署。后续错误关联候选为api_request_observed增加可选request_id，仅来自服务端生成并验证的scope UUID；不采集客户端请求编号或私有请求内容。旧日志格式继续接受，新增坏编号使输入不完整，汇总输出不导出编号。需要排查时仅在有权访问的服务日志中按用户提供的完整问题编号检索。
