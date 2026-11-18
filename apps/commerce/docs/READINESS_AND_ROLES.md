# API就绪与只读权限

## API就绪语义

GET /api/v1/health/live仅表示进程存活。GET /api/v1/health/ready现在执行实际、只读、有限资源检查，成功200、依赖不可用或未配置503。响应scope固定api_dependencies，只返回database/authentication/storage各自ready/unavailable/not_configured，不返回连接串、对象键、账号或原始异常。

数据库检查独立api/auth连接池中的必要表列及报价函数权限，API还确认只读视图迁移存在；没有用户GUC、租户数据读取或写事务。S3使用独立短超时客户端HeadBucket，0.5秒连接、0.75秒读取、单次尝试，不上传/下载对象。需给运行存储身份对指定bucket的探测权限。

三个探测并发，单项async预算1.5秒。健康结果缓存3秒、失败结果最多缓存0.5秒；并发请求共用一轮检查。线程中的存储调用若超过async预算或请求取消，保留并复用同一个尚未完成的调用，不继续堆积新的线程任务。原生I/O有独立短超时，退出时有界等待清理；认证池启动失败时也会关闭此前创建的API池和S3客户端，已有专项用例。

该scope不检测RabbitMQ消费、publisher/Worker心跳或真实模型：API以PG outbox接受任务，队列故障由独立调度恢复，不把API就绪当作完整任务链路或OFFLINE_READY证据。下游积压、消费者活跃性和供应商验收应分别监测。

## app_readonly安全视图

0022增加10个显式列视图，均设置security_invoker=true及security_barrier=true：

- readonly_users、readonly_products、readonly_appearances
- readonly_assets、readonly_asset_versions、readonly_tasks
- readonly_wallets、readonly_credits、readonly_compliance
- readonly_published_rules

视图位于app schema。业务视图沿用FORCE RLS及事务内受信任user上下文；无上下文返回0行。published_rules是已发布全局配置摘要，可无租户读取。视图省略邮箱、密码/会话、外观配置、prompt/input_snapshot、S3定位、模型facts和供应商payload。

app_readonly只得到视图SELECT与视图需要的底层逐列SELECT，没有底层表级SELECT、写权限、auth/ops schema访问或自定义SECURITY DEFINER调用权限。底层已授权的安全列同样可直接查询，仍受RLS过滤；PG invoker视图要求这些列权限，不能误称完全禁止底层读取。

app_readonly是服务端NOLOGIN权限组。单独的受信任服务LOGIN以tenant_transaction(pool,trusted_user_id)设置事务局部GUC；身份必须来自认证结果。持有SQL登录权限的操作者本身可以设置GUC，所以共享服务DSN不应发给终端用户；终端用户通过认证API访问。RLS隔离的信任边界包括服务端数据库身份和上下文设置代码。

## 初始化和验证

setup_local.py新建6个独立非owner LOGIN：api/auth/admin/worker/dispatcher/readonly；READONLY_DATABASE_URL仅写入被忽略的私有profile。原有profile不会覆盖或自动扩展。默认目录.local；另可用--profile-dir work/独立目录做新实例验收，拒绝把凭据输出到可提交的普通目录。

全新、独立、loopback原生PG实例，以及本机私有S3/AMQP配置就绪后：

```text
python scripts/setup_local.py --profile-dir work/bootstrap-profile --demo-account
python scripts/verify_bootstrap.py --profile-dir work/bootstrap-profile
```

verify_bootstrap需要同一实例的MIGRATION_DATABASE_URL，检查全部6个运行角色、24个表FORCE RLS、0001–当前head、重复upgrade schema指纹不变、私有登录/授额/产品创建、只读上下文和秘密列拒绝。该检查不是MQ消费或备份恢复验收。使用新的work目录和独立空PG实例；角色是集群级对象。

```text
python scripts/check.py task P1-02
```

该入口要求隔离TEST_DATABASE_URL；执行src/scripts全部Python AST、后端主要模块导入、原生PG角色/FORCE RLS、只读列权限/视图/写入负例，以及API/readonly连接池并发、取消、语句超时后上下文清理。其他尚未实现的task gate仍明确NOT_RUN，不能据单项通过宣称check all通过。

## 证据

35项单元通过；68项真实PG/MQ/S3全回归通过，另以最终生命周期代码复跑3个健康测试。P1-02入口含8个原生PG测试及AST/导入检查。新增全新PG17.11实例实际执行0001–0022、6角色与API验证；重复升级未改变schema指纹，临时PG已关闭，所有密码/密钥/profile留在忽略目录。实际8000端口ready返回200/api_dependencies。

日志：readiness-unit-20261113.log、readiness-native-full-20261113.log、readiness-health-final-20261113.log、P1-02-native-gate-20261113.log；fresh结果bootstrap-health-readonly.json及对应log；已运行服务响应api-readiness-live.json。浏览器、性能、备份恢复和供应商测试属于各自门槛。
