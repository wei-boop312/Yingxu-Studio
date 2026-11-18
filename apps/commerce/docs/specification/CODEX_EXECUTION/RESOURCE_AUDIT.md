# 本机与 GitHub 资源调查报告

核验日期：2026-11-12（北京时间）。与主计划V4.0配套。所有文件路径、字节hash、仓库元数据及核验限制可在 [resource_manifest.json](resource_manifest.json) 复查。

## 1. 调查范围与可信程度

文件名检索覆盖五个相关根目录，再定向读取命中项目；排除.git、node_modules、venv、build及.env等。未检索浏览器密码、邮箱或无关个人内容。GitHub元数据来自仓库API，官方技术文档直接打开核对。没有执行资料中要求的GitHub推送、付费模型测试或部署。

| 本机根目录 | 列出文件数 | 匹配候选数 |
| --- | ---: | ---: |
| `D:\Desktop\运营材料` | 2252 | 4 |
| `C:\Users\MECHREVO\WorkBuddy AI` | 1009 | 694 |
| `C:\Users\MECHREVO\Documents\Codex` | 6674 | 318 |
| `D:\codex` | 40362 | 34 |
| `D:\workbuddy` | 8611 | 2 |

计数是此次文件名检索结果，不是每个文件内容均已审读；仅下面选定的实际资源做了hash登记。安装目录内的厂商示例、演示素材未作为自有产品代码复制。

## 2. 可落到具体文件的本机资源

| ID | 文件（绝对路径） | 建议 | 核验及接入限制 |
| --- | --- | --- | --- |
| L01 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\migrations\001_schema_baseline.sql` | 改造后移植 | 11表而非10表；补状态CHECK、复合FK、钱包/outbox/events；原SQL不能直接作为最终基线 |
| L02 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\migrations\002_roles_permissions.sql` | 改造后移植 | 撤销账本UPDATE及广泛默认授权；修readonly列授权、认证和owner边界 |
| L03 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\src\database\db_pool.py` | 设计参考并重写 | AST失败line111；不属于可直接运行模块，补取消/超时/真实PG回收验证 |
| L04 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\src\models\bfl_adapter.py` | 重写协议 | 当前官方endpoint/x-key/images/polling_url及状态字段与旧文件不符 |
| L05 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\src\models\gemini_adapter.py` | 改造后移植 | 严格schema、事实识别、SDK封装、费用与超时处理；非真实调用证据 |
| L06 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\src\pixel_protection\pixel_paste.py` | 优先移植并补约束 | 保留EXIF和回贴思路；补canonical RGB、坐标、尺寸及输出像素验证 |
| L07 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\src\platform_rules\rules.json` | 历史种子 | 全部按来源重新验证；MAIN allowed与通用尺寸不得直接自动放行 |
| L08 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\config\models.json` | 配置参考 | 不能照搬端点、单价、速率限制及client request_id假设 |
| L09 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\IDEMPOTENCY_DESIGN.md` | 设计参考 | 保留unknown原则；修冻结/扣费语义与提交前意图 |
| L10 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\tests\test_schema_rls.py` | 历史回归参考 | 67项，主要读SQL/源码字符串，不能证明真实角色权限 |
| L11 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\tests\test_model_adapters.py` | 历史回归参考 | 16项，主要检查常量/源码，不证明供应商可用 |
| L12 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\tests\test_platform_rules.py` | 历史回归参考 | 45项配置检查，不能证明商城规则是最新或完整 |
| L13 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\tests\test_pixel_protection.py` | 优先移植测试 | 28项；含真实内存图像测试和源码检查，补全精确模式边界 |
| L14 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\db\migrations\001_workspace.sql` | 移植设计模式 | FORCE RLS、复合FK、budgets/runs/outbox，非整套搬入电商数据模型 |
| L15 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\apps\api\src\imageProviderContract.ts` | 移植契约模式 | canonical hash、严格输入和快照确认；TS逻辑需独立Python实现 |
| L16 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\apps\api\src\imageProviders.ts` | 接口参考 | 网络/错误归一；原提供者不等于BFL已支持 |
| L17 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\apps\api\src\localBoundRuns.ts` | 优先参考事务边界 | 固定快照、预算、run/outbox同事务；检查源实现存储和身份假设 |
| L18 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\apps\worker\src\localRunDispatcher.ts` | 优先参考恢复边界 | 持久claim/outbox及submitted不重发；原CPU/本地适配器不是云模型验收 |
| L19 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\apps\api\src\imageTaskLeases.ts` | 优先参考付费意图 | write-ahead意图、租约、已存在意图不可再次transport |
| L20 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\apps\api\src\runKernel.ts` | 成本状态机参考 | 请求幂等、运行和预算核对；不可混入原SQLite会话/数据库 |
| L21 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\apps\api\src\objectStorageGateway.ts` | 存储边界参考 | 核实字节与元数据/授权；改为当前S3实现 |
| L22 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\apps\api\src\staticImageDecoder.ts` | 安全解码参考 | PNG/JPEG/WebP格式与解码边界，Python仍需独立真实字节负例 |
| L23 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\implementation\docs\IMPLEMENTATION_STATUS.md` | 证据索引 | 历史记录最后含3002项阶段结果；本次不复跑、不映射到HEAD |
| L24 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\04_DEVELOPMENT\TEST_PLAN.md` | 测试策略复用 | T01-T20证据结构，按电商MVP删减，不要求本期实现Agent/全画布 |
| L25 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\04_DEVELOPMENT\PROJECT_RULES.md` | 流程参考 | 来源规则不继承旧预算/自动任务授权；新任务以用户当前要求为准 |
| L26 | `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\03_TECHNICAL\DATABASE_SCHEMA.md` | 逻辑设计参考 | 不能用逻辑schema替代实际迁移与角色执行验证 |
| L27 | `C:\Users\MECHREVO\Documents\Codex\2026-09-26\xian-z\work\ai-commerce-canvas\docs\run-batch-cost-kernel.md` | 历史设计参考 | 原幂等/成本冻结脉络；不改旧仓库、不引入旧自动接手约定 |
| L28 | `D:\Desktop\运营材料\changxiang\app\infinite-canvas.tsx` | UI/交互参考 | 选区、拖动、参数隔离及结果节点；不是可直接复用的FastAPI后端 |
| L29 | `D:\Desktop\运营材料\changxiang\lib\canvas-document.ts` | UI数据模式参考 | 保存恢复和局部参数类型；保留原线上兼容标识，不修改原项目 |
| L30 | `D:\Desktop\运营材料\changxiang\lib\generation-batch.ts` | 恢复/调度参考 | 一次请求和批量停调度原则；本期仍是单图 |
| L31 | `D:\Desktop\运营材料\changxiang\lib\tool-executors.ts` | 参数规划参考 | 将工具参数与执行请求分开，不新增本期未要求的13工具 |
| L32 | `D:\Desktop\运营材料\changxiang\scripts\phase5-e2e.mjs` | 浏览器测试参考 | 真实指针交互；生成接口是拦截假结果，不代表真实API验证 |
| L33 | `D:\Desktop\运营材料\changxiang\PHASE5-QA.md` | 限定证据 | 明确mock生图与零真实费用边界 |
| L34 | `C:\Users\MECHREVO\Documents\Codex\2026-07-29\yi\outputs\ComfyUI-WorkflowArchitect-2.2.2-package\ComfyUI-WorkflowArchitect\workflow_architect\ecommerce.py` | 后续模板参考 | 仅参数/分类思路；ComfyUI与模型权重不进当前MVP |
| L35 | `D:\Desktop\运营材料\商业无限画布\商业无限画布工作台.docx` | 产品愿景参考 | 406段、提取4827字符；保留双层/Agent长期愿景 |
| L36 | `D:\Desktop\运营材料\商业无限画布\商业无限画布工作台完整项目实施方案_V1.0.docx` | 范围对照 | 633段、提取25048字符；更宽五场景不能替代本次点名图片MVP |

同名文件hash详见JSON；未复制旧Git配置、令牌、业务数据或工作目录缓存。历史自有代码在用户当前项目内的复用以实际权属为前提，对外分发时另核其上游依赖和素材。

## 3. 测试复核与代码缺陷

本次实际运行：`python -B -m pytest <V2.1路径>/tests -q -p no:cacheprovider`，退出码0，**156 passed in 0.72s**。没有真实模型请求或费用。四文件实际为 schema/RLS 67、rules 45、adapter 16、pixel 28，与原计划表里的57/44数字不同。

Schema/RLS用例读取SQL字符串，适配器用例检查常量/源码和对象；它们不连接原生PG，也没有证明BFL/Gemini真实调用。像素用例包含内存图像与EXIF操作，仍需要补充canonical色彩、坐标、精确模式和输出验证。

另对V2.1 Python文件作AST解析：`src/database/db_pool.py:111` 因错误注释片段出现 `SyntaxError: unmatched }`。测试没有导入它，因此仍可156项全过。这是必须修复并用真实导入、数据库角色测试补上的缺口。

Workbench当前本地HEAD为 `1d8142fdc7d522bfb3daaaa1f88db2f68e9fd549`；原稿2951项是历史阶段数字，之后状态文档有3002项阶段记录。本次未跑其完整TS套件，不给当前HEAD盖章。旧画布HEAD为 `19499e7693378f8cccedab9ac9bb6c8afa3e2e76`。畅想第五阶段报告明确mock图片响应，真实模型请求数为0。

## 4. GitHub 候选和取舍

共查询57个仓库路径，其中55个取得元数据；失败也保留，不用猜测替代。以下许可是仓库线索，选入生产前仍须固定版本、保存LICENSE/NOTICE和检查传递依赖。没有把star数当安全或商用许可证明。

| 仓库 | 许可线索 | 维护核验 | 采用方式 |
| --- | --- | --- | --- |
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | MIT | 未归档；push 2026-11-11 | 默认Web API依赖 |
| [encode/starlette](https://github.com/encode/starlette) | BSD-3-Clause | 未归档；push 2026-11-06 | 比较候选，当前不引入 |
| [sanic-org/sanic](https://github.com/sanic-org/sanic) | MIT | 未归档；push 2026-07-29 | 比较候选，当前不引入 |
| [MagicStack/asyncpg](https://github.com/MagicStack/asyncpg) | Apache-2.0 | 未归档；push 2026-10-08 | 默认运行时PG驱动 |
| [psycopg/psycopg](https://github.com/psycopg/psycopg) | LGPL-3.0 | 未归档；push 2026-11-11 | 迁移连接驱动备选/默认工具链 |
| [sqlalchemy/sqlalchemy](https://github.com/sqlalchemy/sqlalchemy) | MIT | 未归档；push 2026-11-10 | 仅迁移工具链 |
| [celery/celery](https://github.com/celery/celery) | NOASSERTION | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [Bogdanp/dramatiq](https://github.com/Bogdanp/dramatiq) | LGPL-3.0 | 未归档；push 2026-11-10 | 比较候选，当前不引入 |
| [python-arq/arq](https://github.com/python-arq/arq) | MIT | 未归档；push 2026-04-16 | 比较候选，当前不引入 |
| [taskiq-python/taskiq](https://github.com/taskiq-python/taskiq) | MIT | 未归档；push 2026-11-07 | 队列框架备选 |
| [taskiq-python/taskiq-aio-pika](https://github.com/taskiq-python/taskiq-aio-pika) | MIT | 未归档；push 2026-09-18 | 队列框架broker备选 |
| [mosquito/aio-pika](https://github.com/mosquito/aio-pika) | Apache-2.0 | 未归档；push 2026-11-08 | 默认MQ异步客户端 |
| [boto/boto3](https://github.com/boto/boto3) | Apache-2.0 | 未归档；push 2026-11-10 | 默认S3 SDK，同步放线程池 |
| [minio/minio](https://github.com/minio/minio) | AGPL-3.0 | 已归档 | 不作新项目默认：服务端已归档 |
| [minio/minio-py](https://github.com/minio/minio-py) | Apache-2.0 | 未归档；push 2026-09-28 | S3 SDK备选；SDK独立于服务端归档 |
| [seaweedfs/seaweedfs](https://github.com/seaweedfs/seaweedfs) | Apache-2.0 | 未归档；push 2026-11-11 | 默认本地S3服务 |
| [jpadilla/pyjwt](https://github.com/jpadilla/pyjwt) | MIT | 未归档；push 2026-11-09 | 默认JWT库 |
| [mpdavis/python-jose](https://github.com/mpdavis/python-jose) | MIT | 未归档；push 2026-04-14 | 比较候选，当前不引入 |
| [authlib/authlib](https://github.com/authlib/authlib) | BSD-3-Clause | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [konvajs/react-konva](https://github.com/konvajs/react-konva) | MIT | 未归档；push 2026-10-15 | 默认React图像选区 |
| [konvajs/konva](https://github.com/konvajs/konva) | MIT | 未归档；push 2026-11-10 | 默认Canvas底层 |
| [xyflow/xyflow](https://github.com/xyflow/xyflow) | MIT | 未归档；push 2026-11-10 | 比较候选，当前不引入 |
| [fabricjs/fabric.js](https://github.com/fabricjs/fabric.js) | MIT | 未归档；push 2026-11-10 | 比较候选，当前不引入 |
| [ant-design/ant-design](https://github.com/ant-design/ant-design) | MIT | 未归档；push 2026-11-11 | 默认UI组件 |
| [mui/material-ui](https://github.com/mui/material-ui) | MIT | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [shadcn-ui/ui](https://github.com/shadcn-ui/ui) | MIT | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [pmndrs/zustand](https://github.com/pmndrs/zustand) | MIT | 未归档；push 2026-11-06 | 默认局部状态 |
| [pmndrs/jotai](https://github.com/pmndrs/jotai) | MIT | 未归档；push 2026-11-09 | 比较候选，当前不引入 |
| [reduxjs/redux-toolkit](https://github.com/reduxjs/redux-toolkit) | MIT | 未归档；push 2026-11-08 | 比较候选，当前不引入 |
| [Donaldcwl/browser-image-compression](https://github.com/Donaldcwl/browser-image-compression) | MIT | 未归档；push 2024-03-08 | 比较候选，当前不引入 |
| [dominictobias/react-image-crop](https://github.com/dominictobias/react-image-crop) | ISC | 未归档；push 2026-06-21 | 比较候选，当前不引入 |
| [sqlalchemy/alembic](https://github.com/sqlalchemy/alembic) | MIT | 未归档；push 2026-10-30 | 默认迁移工具 |
| [marcan/yoyo-migrations](https://github.com/marcan/yoyo-migrations) | 未识别/未取得 | 路径未验证：HTTP Error 404: Not Found | 此次GitHub路径404，不能作为已核实可用依赖 |
| [sqitchers/sqitch](https://github.com/sqitchers/sqitch) | MIT | 未归档；push 2026-09-26 | 比较候选，当前不引入 |
| [hynek/structlog](https://github.com/hynek/structlog) | MIT | 未归档；push 2026-11-02 | 默认结构化日志 |
| [Delgan/loguru](https://github.com/Delgan/loguru) | MIT | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [open-telemetry/opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python) | Apache-2.0 | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [prometheus/client_python](https://github.com/prometheus/client_python) | Apache-2.0 | 未归档；push 2026-10-30 | 默认指标 |
| [grafana/grafana](https://github.com/grafana/grafana) | AGPL-3.0 | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [docker/compose](https://github.com/docker/compose) | Apache-2.0 | 未归档；push 2026-11-11 | 默认单机编排 |
| [googleapis/python-genai](https://github.com/googleapis/python-genai) | Apache-2.0 | 未归档；push 2026-11-11 | 默认Gemini SDK |
| [python-pillow/Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU | 未归档；push 2026-11-11 | 默认图像处理 |
| [ageitgey/imagehash](https://github.com/ageitgey/imagehash) | 未识别/未取得 | 路径未验证：HTTP Error 404: Not Found | 此次路径404，正确候选另列JohannesBuchner/imagehash |
| [opencv/opencv](https://github.com/opencv/opencv) | Apache-2.0 | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [tesseract-ocr/tesseract](https://github.com/tesseract-ocr/tesseract) | Apache-2.0 | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Apache-2.0 | 未归档；push 2026-09-16 | 比较候选，当前不引入 |
| [exiftool/exiftool](https://github.com/exiftool/exiftool) | GPL-3.0 | 未归档；push 2026-11-10 | 比较候选，当前不引入 |
| [fal-ai/fal](https://github.com/fal-ai/fal) | Apache-2.0 | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [Invoke-ai/InvokeAI](https://github.com/Invoke-ai/InvokeAI) | Apache-2.0 | 未归档；push 2026-11-11 | 比较候选，当前不引入 |
| [aws-powertools/powertools-lambda-python](https://github.com/aws-powertools/powertools-lambda-python) | MIT-0 | 未归档；push 2026-11-10 | 参考幂等的in-progress/过期恢复设计，不引入Lambda/DynamoDB依赖 |
| [JohannesBuchner/imagehash](https://github.com/JohannesBuchner/imagehash) | BSD-2-Clause | 未归档；push 2026-10-12 | 可选感知去重，不证明像素相等 |
| [kdpisda/fastapi-rls](https://github.com/kdpisda/fastapi-rls) | BSD-3-Clause | 未归档；push 2026-08-01 | 参考RLS/上下文/负例测试，不直接引入SQLAlchemy集成 |
| [kapoorkanav/python-ledger](https://github.com/kapoorkanav/python-ledger) | 未识别/未取得 | 未归档；push 2026-08-20 | 只参考账务不变量；未发现明确许可，暂不复制 |
| [Canlah-AI/ecom-image-benchmark](https://github.com/Canlah-AI/ecom-image-benchmark) | README声明MIT，未核到完整LICENSE | 未归档；push 2026-04-23 | 参考8类图片/5品类评估维度；缺完整LICENSE，暂不复制数据/代码 |
| [rogerjeasy/originshot](https://github.com/rogerjeasy/originshot) | MIT | 未归档；push 2026-08-03 | 产品图原件/产物hash、来源manifest与报告参考，不引入其自动再生成 |
| [photoshootapp/product-photoshoot-prompts](https://github.com/photoshootapp/product-photoshoot-prompts) | README声明CC-BY-4.0 | 未归档；push 2026-07-20 | 可参考提示词结构；README声明CC BY 4.0，复用原文需署名和标改 |
| [lorena-bordonaba-pau/product-studio](https://github.com/lorena-bordonaba-pau/product-studio) | MIT | 未归档；push 2026-07-10 | 参考产品保真复核流程；不安装插件、不执行付费步骤 |

### 必须保留的许可与维护差异

- MinIO服务端仓库本次API `archived=true`，GitHub页面显示2026-04-25归档；不用它作新MVP默认。minio-py仍是另一个SDK仓库，不得混为同一维护状态。[MinIO仓库](https://github.com/minio/minio)
- SeaweedFS仓库未归档，LICENSE为Apache-2.0；作为本地S3替代仍需实际测试presign、CORS、私有桶与重启恢复。[仓库与许可](https://github.com/seaweedfs/seaweedfs)
- Konva API许可字段为NOASSERTION，但实际LICENSE是MIT；Pillow实际LICENSE说明MIT-CMU；structlog使用LICENSE-MIT而不是根LICENSE。NOASSERTION不等于无许可或可任意使用。
- OriginShot根LICENSE已取回为MIT，可参考原件/产物hash和来源manifest。其云平台、自动重试/再生成及合规宣传不直接继承。[OriginShot](https://github.com/rogerjeasy/originshot)
- ecom-image-benchmark README声称MIT，但API未识别许可、完整根LICENSE未找到；只参考八类图片/五品类评估结构，不复制代码、样本或提示词。[benchmark](https://github.com/Canlah-AI/ecom-image-benchmark)
- python-ledger未取得明确LICENSE，只作账户转移与幂等设计对照，不作为依赖。aws-powertools只参考幂等状态/过期设计，不引入其Lambda/DynamoDB架构。
- 提示词集合README声明CC BY 4.0，复用原文需署名、链接许可并标改；图片和商标不跟随文档许可。Grafana的AGPL、ExifTool的许可证和OCR权重各自独立，不因“工具免费”省略审核。

## 5. 官方接口和平台规则证据

| 来源 | 本次核验结果 |
| --- | --- |
| [BFL生成](https://docs.bfl.ai/flux_3/flux3_image_generate) | 已读官方页面；/v1/flux-3-image、x-key、polling_url、Ready/result.sample；非账户调用验证 |
| [BFL编辑](https://docs.bfl.ai/flux_3/flux3_image_layout) | 已读；images和match_input示例；不保证输出像素不变 |
| [BFL价格](https://docs.bfl.ai/quick_start/pricing) | 已读；按分辨率计费，cost返回；提取页面单价为空，人民币单价未验证 |
| [Gemini模型](https://aistudio.google.com/docs/models/gemini-3.8-flash) | 已读；指定型号支持图片与结构化输出，账户权限未知 |
| [Gemini结构化输出](https://aistudio.google.com/docs/structured-output) | 已读；JSON Schema/Pydantic路线；实现仍需锁定SDK契约 |
| [PG RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) | 已读；owner默认可绕过，FORCE以及超级用户/BYPASSRLS边界 |
| [aio-pika confirm](https://docs.aio-pika.com/rabbitmq-tutorial/7-publisher-confirms.html) | 已读；确认不替代应用outbox和幂等 |
| [Amazon G1881](https://sellercentral.amazon.com/gp/help/external/G1881?language=en_US) | 直读只有JS/login壳；搜索索引有条文，未完整核实，不标verified |
| [Ozon media](https://docs.ozon.ru/global/en/products/requirements/media/) | 请求重定向读取失败，不标verified |
| [Temu](https://seller.kuajingmaihuo.com/) | 检索未获可核适用类目完整规则，未登录或抓取私人后台 |

BFL当前真实接口与旧适配器差异已转入主计划R11和P4-01。Gemini指定型号已找到官方支持，不擅自换成其他模型。实际密钥、配额、地区和费用尚未验证；离线开发不依赖它们。

Amazon检索索引能见部分规则，但直读G1881未取得正文；Ozon请求重定向读取失败；Temu适用类目正文未取得。种子保持unverified，不能把这些检索结果改写为三个平台全部verified。

## 6. 可执行的复用顺序

1. 先取L06/L13的图像与测试思路，并建立新canonical/坐标契约；以源hash留痕。
2. 取L14/L17/L18/L19的复合FK、outbox、短事务、租约和调用意图模式，独立实现Python+PG。
3. V2.1 SQL和角色文件按修正表迁移，连接池重写；不要照搬原默认GRANT。
4. 取畅想L28/L29/L32的交互/保存恢复/真实指针测试思路，构建本项目简单图片视口。
5. BFL适配器按官方协议重写，Gemini改成严格视觉事实输出；未授权时仅跑假传输契约。
6. 仅在后续范围明确扩大时参考全无限画布、WorkflowArchitect和其他图像平台；不引入ComfyUI/GPU/Agent。

## 7. 当前访问与环境限制

目标GitHub仓库当前网页与连接器均404，不能验证远端文件、分支或推送权限；本机只读Git仍可用。旧remote中存在嵌入式访问令牌，报告和包内未包含令牌值，建议用户轮换后改用凭据管理器。没有因此修改旧仓库remote或做推送。

当前Python 3.14.6，pytest/Pillow/aiohttp存在、asyncpg缺失；PATH未检出Docker/npm。只说明当前命令环境，不等于软件绝未安装。实际工程通过P0 doctor锁定并建立独立环境，不修改Comfy内置Python或日常服务。

调查完成不是实现完成。本报告记录候选和可核实边界，最终采用版本、真实集成、商用发布及付费调用需按主计划门槛分别验收。
