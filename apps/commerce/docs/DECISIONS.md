# ADR 001
采用 V4 Python/FastAPI、React/Konva、PostgreSQL、RabbitMQ、S3 路线。钱包与账本使用真实数据库事务。未复制旧项目源码，避免继承许可与已知缺陷。精确模式仅等尺寸平移；未知供应商提交禁止重发。SeaweedFS 已完成原生 S3 集成验证；Compose 独立启停及持久化验收仍以当前 CI 实际结果为准。

## ADR 002：原生数据库验证
缺Docker时采用EDB PostgreSQL 17.11 Windows二进制，在任务work目录初始化隔离实例，只监听127.0.0.1:55432，SCRAM随机密码。来源：https://www.enterprisedb.com/download-postgresql-binaries 。此证据属于真实PostgreSQL，不代表RabbitMQ/S3集成或生产部署。数据库角色为集群级，迁移必须在专属实例执行。

## ADR 003：规范来源与远端授权
docs/specification 保留原计划和执行包，不用实现进度改写源文件。source-map 记录原始来源摘要；实际实现及验证状态见 STATUS 和证据。源材料的“无远端推送”约束描述初始规划范围；用户在本聊天中明确授权持续上传 wei-boop312/-AI-，据此提交并推送该仓库。真实付费模型和实际部署仍未授权。

## ADR 004：非 root S3 容器读取私有配置
随机 S3 配置位于 Linux 0700 私有 profile 目录，state/env 为 0600，单独只读绑定的 S3 配置为 0644，以允许镜像内非 root UID 读取。宿主其他用户无法遍历父目录。此选择修复 CI 43319072663 的真实权限失败，不通过将容器改为 root 绕过限制；Windows ACL 不由 chmod 保证。

## ADR 005：完整本机Compose应用profile
基础compose.yaml与compose.app.yaml分离，显式bootstrap后app-up启用4应用服务，同profile标准up/down保留8服务身份和命名卷。当前Linux演练使用host network访问既有回环PG/MQ/S3，API/Nginx必须显式127.0.0.1监听，容器实际socket检查验证；不把该拓扑当生产HTTPS部署。镜像revision/UID与ID验证后固定owner私有本地tag，不发布、不隐式升级。每组件仅接收必要角色；命名Fake卷挂载/app/work保留本机回执缓存，私钥单文件只读绑定。CPU/内存/PID和健康/停止参数为本机候选参数，不外推生产容量。Windows原生路径继续保留，应用Compose仍待LinuxCI实测。

## ADR 006：服务退出与在途投递
Linux服务SIGTERM转为异步取消并执行清理；Consumer先停订阅/拒收新任务，对已有处理给20秒排空，再取消并等待清理后关闭连接池。Docker给40秒宽限并用init转发信号。取消不ACK未完成的处理，已有数据库意图与fencing仍阻止第二次提交；线程侧晚到结果仍只作既有回执处理，不据此新建付费请求。POSIX run_local用exec保留实际PID，避免CI只等外层包装进程退出就误认消费者已停止。单元协调与原生回归不替代Docker真实信号/缓存重建验收。
