# 隔离Compose基础服务

compose.yaml提供两套独立PostgreSQL17.11、RabbitMQ4.4.0和SeaweedFS4.53，均固定已核对的镜像digest。测试PG55432、开发PG55433、MQ5679、S318333仅绑定127.0.0.1；没有默认生产密码。新profile首先只启动基础设施；compose.app.yaml候选补API/Publisher/Worker/Web，显式初始化后同一profile的up/down会恢复/停止全部8服务，不自行部署到生产。

## 基础设施与原生应用路径

需安装可运行Linux镜像的Docker engine及Compose插件，并完成项目锁定依赖安装。本机当前没有Docker，原有原生服务验证不能代替Compose测试。

```text
python scripts/dev.py doctor
python scripts/dev.py up --profile-dir .local/compose
python scripts/dev.py migrate --compose-profile .local/compose
python scripts/dev.py bootstrap --compose-profile .local/compose --demo-account
python scripts/run_local.py api
python scripts/run_local.py publisher
python scripts/run_local.py worker
python scripts/run_local.py preview
python scripts/dev.py down --profile-dir .local/compose
```

preview要求先执行前端build。bootstrap会创建6个非owner LOGIN、JWT及私有runtime.json；已有profile会拒绝覆盖。demo-account仅在显式参数时生成，凭据留在忽略目录，不能上传。若需保留原有.local环境，可给bootstrap传`--profile-dir work/新目录/profile`，运行组件时也传同一个`--profile-dir`。

up生成私有state.json、compose.env、S3身份文件，项目名由该profile路径派生，另有随机owner标识。Docker容器/卷/网络操作前核对标签；遇到不属本profile的资源拒绝。首次启动遇到占用端口也拒绝，不停止既有原生或其他Docker服务。删除私有state文件不会授权接管原有数据卷，恢复时应保留相应私有配置。

down保留全部命名卷，不重置数据或凭据；不要用全局prune清理此项目。PG bootstrap超级用户只用于迁移/初始化，API、认证、Worker、发布器和只读视图各用独立运行身份。随机值和原始日志只在忽略.local/work，Windows文件ACL仍由运行者配置，不能把chmod当作完整Windows ACL隔离。

Linux私有profile目录为0700，state/env为0600。S3身份文件在该私有目录内设0644并只读绑定给非root容器；宿主其他用户仍无法穿过0700父目录，容器进程则能读取挂载文件。首轮Compose曾因该文件0600与容器UID不一致而读取被拒，已修正，保留实际失败记录。

## 完整8服务本机候选（Linux Docker host）

此路径使用Linux host network连接回环基础服务；API显式127.0.0.1:8000，Nginx私有配置显式127.0.0.1:8080。不是生产TLS拓扑。Windows仍可使用已验证的原生路径；当前应用profile不宣称已验证Docker Desktop host networking。

在端口空闲的新环境，完成锁定Python依赖后：

```sh
python scripts/dev.py up --profile-dir .local/compose-full
python scripts/dev.py bootstrap --compose-profile .local/compose-full --profile-dir .local/compose-full/profile --origin http://127.0.0.1:8080 --demo-account
docker build --build-arg VCS_REF=$(git rev-parse HEAD) -t visual-api:local .
docker build -f apps/web/Dockerfile --build-arg VCS_REF=$(git rev-parse HEAD) -t visual-web:local .
python scripts/dev.py app-up --compose-profile .local/compose-full --profile-dir .local/compose-full/profile --api-image visual-api:local --web-image visual-web:local
python scripts/dev.py down --profile-dir .local/compose-full
python scripts/dev.py up --profile-dir .local/compose-full
```

应用地址http://127.0.0.1:8080，体验账号只保存在忽略的profile/account.json。app-up核对图片revision等于当前提交、非root UID、服务profile与该基础设施一致，并生成各组件最小角色env及只读JWT挂载；迁移/admin/readonly角色不传给应用。所有key/env/state处于Linux0700私有父目录；JWT与Nginx单文件0644只读绑定允许容器UID读取。setup_local也显式保护profile/文件，Windows ACL仍需宿主配置。

image ID固定到该owner的私有本地tag并保存application.json；后续up/down保留身份、凭据和卷，不静默替换镜像或自动迁移。重复app-up如果传入不同镜像会拒绝，输出实际应用commit/镜像ID供核对；镜像升级需另行明确的版本/迁移流程。原生和完整Compose路径不能并行占用同一组固定端口，遇占用拒绝，不终止已有服务。

8服务均配置CPU/内存/PID限制、健康检查和停止宽限。S3继承固定基镜像的健康命令，明确探针节奏；实际私有认证与已存对象可读另由演练验证。API健康探测真实依赖；Worker/Publisher以各自身份探测PG/MQ、进程内成功DB心跳文件，Worker另探测私有S3。应用cap_drop ALL/no-new-privileges，后端UID10001、前端UID101。

Worker先取消订阅，停止接收新任务，在途最多排空20秒，之后取消并执行租约清理，再关闭MQ/PG。SIGTERM转换为异步清理和成功退出，Compose给40秒；这不意味着未知付费提交可以重发。POSIX run_local以exec保持实际服务PID，CI可以等待真正退出后才执行消费者暂停测试。4项单元验证排空/超时/新消息重排/信号清理，不能替代原生信号验收。

Fake意图/回执/图片缓存持久化到本项目命名卷挂载/app/work，daily down不删除。临时CI演练才显式删除该owner的卷。container_smoke.py候选实际验证8服务健康/资源/回环socket、登录上传至结算、停止exit0、删除重建4应用容器后缓存SHA/会话/最终图/余额保持；输出application-compose.json与container-runtime.json并供all/P5-01检查。当前Windows无Docker，Schema/单元/原生测试通过不等于应用Compose已验收，必须以实际Linux CI为准。

依据：[Compose services](https://docs.docker.com/reference/compose-file/services/)、[Docker host network](https://docs.docker.com/engine/network/drivers/host/)。

Rabbit固定容器hostname以便重建后仍能读取原Mnesia数据；S3工作目录和master/volume/filer元数据放入/data命名卷，身份文件只读挂载。API、MQ、S3密钥不写进镜像或Git。若手工调用Compose，所有私有变量均必填，.env.example故意留空；推荐使用dev入口而不是把示例当生产凭据。

## 原生Compose验收

`python scripts/check.py task P0-02`创建全新work/compose-smoke-UUID profile，实际up、fresh bootstrap/全部迁移和重复upgrade、6角色/FORCE RLS/登录/产品/只读验证，然后写私有S3对象及quorum持久消息。执行保卷down/up后比较PG system identifier及用户数量、S3内容SHA、MQ消息；最后只删除已核对标签的该测试项目资源和测试卷。日常dev down没有删卷入口。

成功输出compose-smoke.json及bootstrap-health-readonly.json，绑定提交和环境；失败保留脱敏诊断，私有凭据不上传。无Docker返回exit3 BLOCKED_ENV，不能称验收成功。CI已加入该真实演练，并要求P5-01消费本次结果；当前候选尚待首次Linux Compose执行，不提前标通过。

主计划§13的原样命令`python scripts/dev.py up --profile offline`现已兼容；up/down的--profile只允许offline，仍可另传--profile-dir选择独立资源目录。它不启用live或真实模型，不改变基础设施启动/显式migrate/bootstrap步骤。--help解析验证不启动服务，真实Compose仍以当次Linux CI演练为证。
