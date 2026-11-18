# 当前API与容器候选使用说明

实际机器契约为[contracts/openapi.json](../contracts/openapi.json)，由当前FastAPI路由生成；P5-01检查会与运行代码重新生成的schema比对。业务与健康接口统一前缀`/api/v1`，当前31条路径；不要把文档表格中的短路径直接作为根路径。

## 私有账号与会话

没有公开注册、充值或API key管理入口。用专用非owner ops_admin通过`python scripts/dev.py create-user --email <私有邮箱> --credits <内部积分>`创建账号；密码在隐藏终端输入，不能写进shell历史或Git。参见RUNBOOK与READINESS_AND_ROLES。

浏览器以`POST /api/v1/auth/login`提交email/password和精确Origin，服务端写HttpOnly access/refresh Cookie及可读CSRF Cookie。后续业务写请求需要Cookie、匹配的Origin与X-CSRF-Token；refresh/logout也需要CSRF。所有用户/资产归属取服务器身份，不接受客户端user_id。refresh轮换有重放撤销；401后安全刷新只重放原操作，不能换任务幂等键自动多次收费。

## 产品到最终下载

1. 创建产品、选择产品后创建upload-intent，声明真实filename/mime/size。使用返回的私有上传URL和必要headers原样PUT，再调用complete。保留ready版本ID/SHA，未完成的临时对象不能用于生成。
2. 在canonical源图坐标系配置保护区域；目标只允许等尺寸整数平移，不允许重叠或越界。source_asset_version_id绑定原版本，不能把画布缩放后的显示坐标当源像素。精确模式限制4MP。
3. POST /quotes获取版本化报价和有效期；POST /tasks提交同一业务输入、quote_id及Idempotency-Key。同一次提交网络丢失时复用原键与内容；不同内容返回冲突，不能更换键“保险重试”。内部积分是产品用量单位，不等同人民币或供应商credits。
4. 读取tasks/{id}或events（SSE携带Last-Event-ID可恢复），断线可退回轮询。queued可取消，付费意图后409；unknown保留冻结、持续接收运营结案或既有回执恢复，不出现普通模型重试。
5. succeeded后获取download和report/download；每次服务端重新检查归属，返回短期私有签名地址。核对最终版本/SHA与报告相同。compliance/versions提供报告版本，compliance与report/download支持check_id选择历史报告；人工复核不覆盖自动事实或原报告。

完整请求字段/约束以OpenAPI及task-create.schema.json为准。返回404同时用于资源不存在/不可见，不向客户端暴露其他用户的ID、对象key、供应商回执或秘钥。需要人工审核只表示当前证据不足，不代表保证上架通过。管理员复核、规则发布和uncertain对账使用受限CLI，普通用户HTTP不能执行。

## 健康、运行与镜像

GET /api/v1/health/live只看API存活；ready只探测api/auth PG及S3，检查范围不是整个MQ/Worker/供应商。全局聚合监控另见MONITORING.md。未配置/依赖失败返回503，不能用live200代替ready或发布验收。

后端候选：`docker build --build-arg VCS_REF=<提交SHA> -t visual-api:local .`，默认启动API；同一镜像可用`python -m visual_platform.worker --consume`或`python -m visual_platform.events`分别启动进程。前端候选：`docker build -f apps/web/Dockerfile --build-arg VCS_REF=<提交SHA> -t visual-web:local .`，Nginx8080将/api代理到同网络api:8000。镜像内不含私有profile、JWT私钥或默认生产密码；API/Worker必须配置各自非owner身份和私有S3/MQ，原生CLI仍只启用offline Fake。

后端UID10001、前端UID101；挂载的JWT/凭据必须给相应进程最小只读权限，工作临时目录单独可写。非本机运行需要HTTPS Origin和Secure Cookie、受信任代理/网络、独立迁移身份、备份与告警；不能把本机演练凭据复制生产。此文档提供构建候选，不授权部署；尚未完成的上线门槛以STATUS和实际验收结果为准。

## P5-01构建门槛

`python scripts/check.py task P5-01`验证当前路由schema、镜像/Actions固定引用、法律文本SHA、SBOM锁文件摘要，并要求本次原生/浏览器/性能、依赖审计及两个实际镜像结果对应当前提交。缺Docker或新鲜记录返回BLOCKED_ENV，旧的已提交证据不能替代本次执行。工作流只读仓库，只构建测试和上传短期公开产物，不发布镜像、不部署。

新增容器运行候选验收 `scripts/container_smoke.py`：在 Linux CI 建立独立临时 Compose 基础服务，运行刚构建的 API/Nginx/Publisher/Worker 四个非 root 容器，经过 Nginx 登录、私有上传、MQ 消费、最终图/报告下载、保护 RGB 和唯一结算检查。报告 container-runtime.json 绑定当前 commit 与两个实际镜像 ID，并纳入 P5-01；代码存在不等于该轮已通过，必须等待实际 CI。

该候选现改为完整compose.app.yaml编排，Linux host network访问回环基础服务，Origin为http://127.0.0.1:8080；不提供生产网络/TLS配置。每容器仅接收自身数据库角色，迁移/admin凭据不传给容器；JWT单文件只读挂载、私有父目录0700。Fake缓存改为受保护的命名卷，并新增8服务资源/健康/实际回环socket、SIGTERM退出、4应用容器删除重建后缓存/会话/最终图/账务不变检查。仅按owner标签清理临时资源；原生恢复另有P5-02实测。早期f53fc1f仅手写docker run/一次性缓存的结果不能代表新Compose重建候选已通过，详见COMPOSE与当前CI。


上传工作台新增真实字节进度与独立校验阶段；HTTP上传成功不等于图片ready。暂时校验失败可用原意图“重新校验”，不重传、不触发模型。切换产品或退出会中止旧浏览器流程；服务器已经归档的图片仍归原产品，再选回原产品可查看，未完成临时上传按UPLOAD_CLEANUP的单独运维流程处理。该变更需当次生产浏览器CI验证。

报价响应现在绑定请求时的用户、产品、原图和配置，范围变化时丢弃迟到响应；首次确认前再检查是否仍匹配。任务提交如果已尝试但响应丢失，继续用原输入、报价和同一幂等键重放，不自动更换报价或创建新任务。
