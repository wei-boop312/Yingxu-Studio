# 跨境电商AI视觉生产平台 — 项目计划书

**版本**: V3.0 (整合版)
**日期**: 2026-11-12
**编制**: WorkBuddy AI (测试与质量保障方)
**待执行**: GPT Work (资源检索补充) → Codex (开发实现)
**状态**: 设计基线冻结 (V6)，等待工程实现

---

## 0. 文档导航

| 章节 | 内容 | 读者 |
|------|------|------|
| §1 | 项目概述与目标 | 全员 |
| §2 | 本地已有资源盘点 | GPT Work / Codex |
| §3 | 待检索补充资源清单 | GPT Work |
| §4 | 技术架构设计 | Codex |
| §5 | 数据库设计 (完整Schema) | Codex |
| §6 | 后端模块设计 | Codex |
| §7 | 前端模块设计 | Codex |
| §8 | 安全与权限设计 | Codex |
| §9 | 幂等与状态机设计 | Codex |
| §10 | 平台规则引擎设计 | Codex |
| §11 | 像素保护与图像处理 | Codex |
| §12 | 测试计划 | Codex / WorkBuddy AI |
| §13 | 开发任务分解 | Codex |
| §14 | 部署与运维 | Codex / 运维 |
| §15 | 风险与未决项 | 全员 |
| 附录A | V6整改完整清单 | 审计参考 |
| 附录B | Infinite Workbench 可复用资源对照 | GPT Work / Codex |

---

## 1. 项目概述与目标

### 1.1 产品定位

为跨境电商卖家提供一站式 AI 视觉内容生产平台，覆盖以下核心能力：

1. **AI 图片生成** — 调用 BFL FLUX 3 生成产品场景图、白底主图、生活场景图
2. **AI 合规检测** — 调用 Gemini 3.8 Flash 检测图片中的文字/标志/水印，判断是否符合各电商平台规则
3. **像素级保护** — 将用户指定的保护区域（品牌Logo、产品细节）从原图精确回贴到AI生成结果上
4. **多平台规则引擎** — Amazon / Temu / Ozon 三平台图片规则自动校验
5. **积分预算管理** — 按次计费的积分系统，支持冻结→扣费→退款的原子操作

### 1.2 项目来源

本项目由两个分支整合而成：

| 分支 | 技术栈 | 状态 | 贡献 |
|------|--------|------|------|
| **V2.1 设计验证分支** | Python / PostgreSQL | V6整改完成，156项测试通过，设计基线冻结 | 完整的Schema、RLS策略、适配器设计、规则引擎、幂等状态机 |
| **Infinite Workbench** | TypeScript / React / PostgreSQL | 本地实现进行中，2951项测试通过，1132文件 | 双层画布架构、图像提供者契约、预算/幂等表设计、T01-T20测试策略 |

### 1.3 协作分工

| 角色 | 负责方 | 职责 |
|------|--------|------|
| **资源检索** | GPT Work | 检索本地和GitHub上的可用代码/库/工具，补充技术选型 |
| **开发实现** | Codex | 根据本计划书编写全部代码，提交到GitHub |
| **测试与质量** | WorkBuddy AI | 拉取代码，编写/执行测试用例，出具测试报告，反馈修复建议 |
| **产品决策** | 用户 | 需求确认、预算授权、上线决策 |

### 1.4 MVP 范围 (首期目标)

**核心场景**：
1. 用户注册登录 → 创建产品 → 上传产品图
2. 配置外观参数 → 提交AI生成任务 → BFL FLUX 3生成图片
3. AI检测结果 → 平台规则校验 → 输出合规/不合规判定
4. 像素回贴保护区域 → 输出最终图片 → 下载
5. 积分扣费 → 查看积分流水

**不含首期**：
- 团队协作 / 多租户组织
- ComfyUI 工作流编辑器
- Agent 自主循环
- 离线编辑 / 端到端加密
- 商业运营 / 公开注册

---

## 2. 本地已有资源盘点

### 2.1 V2.1 设计验证分支 (Python)

**位置**: `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\`

**文件清单 (15个代码文件 + 3个文档)**:

```
v2.1-project/
├── migrations/
│   ├── 001_schema_baseline.sql         # 10张表 + RLS + 视图 + 索引 + V6唯一索引
│   ├── 002_roles_permissions.sql        # 4个角色 + 列级GRANT + V6 readonly收紧
│   └── 001_down_rollback.sql            # 回滚脚本
├── src/
│   ├── database/db_pool.py              # asyncpg连接池 + JWT→GUC信任边界 + 5类边界场景
│   ├── models/bfl_adapter.py            # BFL FLUX 3 异步轮询适配器
│   ├── models/gemini_adapter.py         # Gemini 3.8 Flash 检测适配器
│   ├── pixel_protection/pixel_paste.py  # 像素回贴 + EXIF Orientation修正
│   └── platform_rules/rules.json        # Amazon/Temu/Ozon三平台规则 (V6修正版)
├── config/models.json                   # 模型集中配置
├── tests/                               # 156项测试，全部通过
│   ├── test_schema_rls.py               # 57项 (Schema/RLS/GRANT验证)
│   ├── test_platform_rules.py           # 44项 (平台规则验证)
│   ├── test_model_adapters.py           # 16项 (BFL/Gemini适配器验证)
│   └── test_pixel_protection.py         # 28项 (EXIF/像素回贴验证)
├── IDEMPOTENCY_DESIGN.md                # 幂等状态机完整设计文档
├── V6_REMEDIATION_REPORT.md             # V6整改报告 (7项任务全部关闭)
└── README.md
```

**可直接复用的设计资产**:
- 数据库Schema (10张表 + 2个安全视图 + 10条RLS策略 + 7个索引)
- 角色权限体系 (4角色 + 列级GRANT + NOBYPASSRLS)
- BFL FLUX 3 适配器 (异步提交 + 轮询 + 超时标记uncertain)
- Gemini 3.8 Flash 适配器 (图片检测 + 结构化输出 + 置信度评分)
- 平台规则引擎 (Amazon MAIN/SWCH/PT01-99 + Temu + Ozon)
- 像素回贴模块 (EXIF Orientation修正 + 区域遮罩 + 回贴)
- 幂等状态机 (任务状态机 + 积分状态机 + UNIQUE约束)
- 连接池信任边界 (JWT→GUC→RLS 安全链)

### 2.2 Infinite Workbench (TypeScript)

**位置**: `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\`

**仓库**: `https://github.com/wei-boop312/Infinite-workbench.git`

**规模**: 1132文件，127个提交，2951项测试通过

**可参考的核心设计**:

| 文件 | 内容 | 参考价值 |
|------|------|---------|
| `03_TECHNICAL/DATABASE_SCHEMA.md` | 28张表的完整逻辑Schema | 表设计范式、复合外键、RLS策略模板 |
| `implementation/db/migrations/001_workspace.sql` | 实际可执行的PG迁移 | RLS + security_invoker实现写法 |
| `implementation/apps/api/src/imageProviderContract.ts` | 图像提供者统一契约 | 适配器接口设计参考 |
| `implementation/apps/api/src/imageProviders.ts` | OpenAI/Google适配器实现 | API调用/错误归一/超时处理参考 |
| `02_ARCHITECTURE/ARCHITECTURE.md` | 模块化单体API + 独立Worker架构 | 部署架构参考 |
| `04_DEVELOPMENT/TEST_PLAN.md` | T01-T20测试用例矩阵 | 测试策略和证据记录标准 |
| `04_DEVELOPMENT/PROJECT_RULES.md` | 12项基线规则 + 变更交付规范 | 开发流程参考 |
| `05_PROJECT_MANAGEMENT/DECISIONS.md` | D01-D12决策 + C01-C15冲突处理 | 决策追溯 |

**架构关键决策 (可继承)**:
- 模块化单体API + 独立Worker，不强行微服务化
- PostgreSQL 作为唯一真相源，MQ仅传递消息
- 对象存储保存文件字节，DB保存元数据和引用
- 前端不直连DB/MQ/GPU，不持有供应商密钥
- SSE推送 + 轮询回退
- 先契约再实现，先Schema再迁移

### 2.3 旧画布项目 ai-commerce-canvas

**位置**: `C:\Users\MECHREVO\Documents\Codex\2026-09-26\xian-z\work\ai-commerce-canvas\`

**HEAD**: `19499e7`，128项测试通过

**参考价值**: TypeScript + React + Node 电商画布原型，phase3 任务成本内核设计文档中的幂等+成本冻结思路与V2.1积分系统是同一脉络。

---

## 3. 待检索补充资源清单 (GPT Work 执行项)

> **指令**: GPT Work 需检索本地文件系统和GitHub，为以下技术选型找到具体可用的代码/库/工具/示例，并补充到对应章节。

### 3.1 后端框架与库

| 检索项 | 要求 | 搜索方向 |
|--------|------|---------|
| Python异步Web框架 | FastAPI / Starlette / Sanic 对比 | GitHub: `fastapi` stars>50k, 生产可用验证 |
| 异步PG驱动 | asyncpg vs psycopg3 async vs SQLAlchemy 2.0 async | 性能基准、连接池管理、GUC支持 |
| 任务队列 | Celery vs Dramatiq vs ARQ vs Taskiq | Python原生异步队列、至少一次投递、死信队列 |
| 对象存储SDK | minio-py vs boto3 vs aiohttp直接签名上传 | S3兼容、分片上传、预签名URL |
| JWT认证 | PyJWT vs python-jose vs authlib | RS256签名、刷新令牌、黑名单 |

### 3.2 前端框架与库

| 检索项 | 要求 | 搜索方向 |
|--------|------|---------|
| React画布 | Konva/react-konva vs React Flow vs Fabric.js | 图片编辑、图层、选区、50对象基准 |
| UI组件库 | Ant Design vs MUI vs shadcn/ui | 中文友好、表格/表单/上传组件完整 |
| 状态管理 | Zustand vs Jotai vs Redux Toolkit | 异步状态、SSE事件流处理 |
| 图片处理 | browser-image-compression vs react-image-crop | 前端压缩、裁剪、EXIF读取 |

### 3.3 基础设施

| 检索项 | 要求 | 搜索方向 |
|--------|------|---------|
| PostgreSQL迁移工具 | Alembic vs yoyo-migrations vs sqitch | 版本化、回滚、CI集成 |
| 容器编排 | Docker Compose单机 vs K8s | 首期单机部署，保留K8s扩展路径 |
| 日志收集 | structlog vs loguru vs 标准logging | 结构化日志、JSON输出、脱敏 |
| 监控 | Prometheus + Grafana vs OpenTelemetry | API延迟、队列深度、模型调用统计 |
| CI/CD | GitHub Actions vs GitLab CI | 自动测试、镜像构建、迁移执行 |

### 3.4 AI/图像处理

| 检索项 | 要求 | 搜索方向 |
|--------|------|---------|
| EXIF处理 | Pillow exif vs exifread vs piexif | Orientation标签读写、批量处理 |
| 图片相似度 | imagehash vs OpenCV perceptual hash | 生成结果与原图对比、去重 |
| OCR备选 | Tesseract vs PaddleOCR vs 仅Gemini | 文字检测准确率、中文支持、成本对比 |
| 图片元数据 | Pillow metadata vs exiftool | DPI、色彩空间、尺寸校验 |

### 3.5 GitHub开源参考

| 检索方向 | 关键词 | 目的 |
|----------|--------|------|
| 跨境电商图片工具 | `amazon listing image tool` / `ecommerce image generator` | 产品功能参考 |
| AI图片生成平台 | `ai image generation platform` / `flux api wrapper` | 架构和API设计参考 |
| PostgreSQL RLS实战 | `postgres rls multi-tenant` / `row level security python` | RLS最佳实践 |
| 积分系统 | `credit system python` / `virtual currency api` | 冻结/扣费/退款原子操作参考 |
| 幂等设计 | `idempotent api design python` / `task idempotency` | 幂等键、状态机参考 |

---

## 4. 技术架构设计

### 4.1 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      浏览器 (React SPA)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ 产品管理  │ │ 任务提交  │ │ 结果查看  │ │ 积分/流水      │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS / JWT
┌──────────────────────┴──────────────────────────────────────┐
│                    API 服务 (FastAPI)                        │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌────────┐  │
│  │ 认证  │ │产品  │ │任务  │ │积分  │ │规则  │ │SSE推送 │  │
│  │JWT   │ │CRUD  │ │状态机│ │账务  │ │校验  │ │事件流  │  │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └────────┘  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           连接池 (asyncpg + JWT→GUC→RLS)              │   │
│  └──────────────────────────────────────────────────────┘   │
└───────┬──────────────────────────┬───────────────────┬──────┘
        │                          │                   │
┌───────┴──────┐    ┌──────────────┴──┐    ┌──────────┴──────┐
│  PostgreSQL  │    │  RabbitMQ       │    │  MinIO (S3兼容) │
│  10张表+RLS  │    │  任务队列        │    │  图片存储        │
│  2个安全视图 │    │  死信队列        │    │  预签名URL      │
└──────────────┘    └───────┬─────────┘    └─────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────────┐
│                    Worker 服务 (Python异步)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ 任务消费  │ │ BFL适配器 │ │Gemini适配│ │ 像素回贴      │   │
│  │ 状态轮询  │ │ FLUX 3   │ │ 3.8 Flash│ │ EXIF修正     │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  积分原子操作 (冻结→扣费/退款, UNIQUE约束)             │   │
│  └──────────────────────────────────────────────────────┘   │
└───────┬──────────────────────────┬───────────────────┬──────┘
        │                          │                   │
┌───────┴──────┐    ┌──────────────┴──┐    ┌──────────┴──────┐
│  BFL API     │    │  Gemini API     │    │  平台规则引擎    │
│  (图片生成)  │    │  (AI检测)       │    │  (Amazon/Temu/  │
│  异步轮询    │    │  同步调用        │    │   Ozon)         │
└──────────────┘    └─────────────────┘    └─────────────────┘
```

### 4.2 模块职责

| 模块 | 职责 | 不做 |
|------|------|------|
| **API服务** | 认证、产品CRUD、任务提交、积分查询、SSE推送 | 不直接调用AI模型、不处理图片像素 |
| **Worker** | 消费任务队列、调用BFL/Gemini API、像素回贴、积分扣费 | 不处理HTTP请求、不直接暴露给前端 |
| **PostgreSQL** | 数据真相、RLS隔离、幂等约束、积分流水 | 不存储图片字节 |
| **MinIO** | 图片文件存储、预签名上传/下载 | 不存储业务数据 |
| **RabbitMQ** | 任务消息传递、至少一次投递 | 不持久化业务状态 |
| **前端** | 用户交互、表单提交、SSE接收、图片展示 | 不直连DB/MQ、不持有API Key |

### 4.3 数据流 — 图片生成完整流程

```
用户上传产品图
    │
    ▼
API: 创建product + asset → MinIO上传 → DB记录
    │
    ▼
用户配置外观参数 → 提交生成任务
    │
    ▼
API: 创建generation_task (status=pending)
    │  └→ 检查积分余额 ≥ 预估费用
    │  └→ 冻结积分 (credit_transactions: type=freeze, UNIQUE约束)
    │  └→ 状态更新: pending → processing
    │  └→ 写入RabbitMQ消息
    │  └→ 返回task_id给前端
    │
    ▼
Worker: 消费消息
    │  └→ 创建provider_attempt (attempt_number=1)
    │  └→ 调用BFL FLUX 3 API (异步提交)
    │       └→ 提交成功: status=submitted, 轮询
    │       └→ 提交失败: status=failed
    │       └→ 超时无响应: status=uncertain (V6-06)
    │  └→ 轮询结果
    │       └→ succeeded: 下载图片 → MinIO存储
    │       └→ failed: 记录错误
    │       └→ 超时: status=uncertain, 需人工核查
    │
    ▼
Worker: AI合规检测 (Gemini 3.8 Flash)
    │  └→ 编码图片为base64
    │  └→ 调用Gemini detect API
    │       └→ confidence ≥ 0.75: 自动判定
    │       └→ confidence < 0.75: manual_review (V6-05)
    │       └→ 检测失败: manual_review (V6-05)
    │       └→ 5%抽检: 即使通过也可能人工复核 (V6-05)
    │
    ▼
Worker: 平台规则校验
    │  └→ 加载rules.json对应平台规则
    │  └→ 校验尺寸/背景/文字/Logo/水印
    │  └→ 输出: compliant / non_compliant / manual_review
    │
    ▼
Worker: 像素回贴
    │  └→ 读取用户指定的保护区域 (PasteRegion[])
    │  └→ 从原图提取保护区域像素
    │  └→ EXIF Orientation修正 (V4修正: 6=ROTATE_270, 8=ROTATE_90)
    │  └→ 回贴到生成图片对应位置
    │  └→ 输出最终图片 → MinIO存储
    │
    ▼
Worker: 完成任务
    │  └→ 状态更新: processing → succeeded
    │  └→ 积分扣费 (credit_transactions: type=charge, UNIQUE约束)
    │  └→ 记录task_cost_record
    │  └→ SSE通知前端
    │
    ▼
前端: 展示结果 → 用户下载
```

### 4.4 故障边界

| 场景 | 行为 | 恢复 |
|------|------|------|
| BFL API超时 | 标记uncertain，不盲重付费 | 人工通过request_id核查 |
| Gemini检测失败 | 升级为manual_review | 人工复核后覆盖结果 |
| Worker崩溃 | 任务留在processing状态 | 超时清理 → 重新入队 |
| 积分冻结后任务失败 | 自动退款 (type=refund) | UNIQUE约束防重复退款 |
| MinIO上传失败 | 任务不标记成功 | 重试归档，不重新生成 |
| SSE断开 | 自动转轮询 | 持久游标恢复 |
| 取消任务 | 尽力请求 | 已提交的API可能已计费 |

---

## 5. 数据库设计 (完整Schema)

### 5.1 表结构总览

```
users ──┬── products ──── appearance_profiles
        │                   │
        │                   ├── assets ── asset_versions
        │                   │                │
        │                   │                └── consistency_checks
        │                   │
        └── generation_tasks ──┬── provider_attempts
                                │
                                ├── task_cost_records
                                │
                                └── credit_transactions

platform_rules (独立配置表)
```

### 5.2 完整表定义

**已有完整SQL文件**: `migrations/001_schema_baseline.sql` (283行)

#### 表1: users (用户账户)

```sql
CREATE TABLE users (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email             VARCHAR(255) UNIQUE NOT NULL,
    password_hash     TEXT NOT NULL,
    credits           INTEGER NOT NULL DEFAULT 0,
    subscription_tier VARCHAR(20) NOT NULL DEFAULT 'free',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 表2: products (产品)

```sql
CREATE TABLE products (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        VARCHAR(255) NOT NULL,
    category    VARCHAR(100),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 表3: appearance_profiles (外观配置)

```sql
CREATE TABLE appearance_profiles (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id  UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name        VARCHAR(255) NOT NULL,
    config      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 表4: assets (资产)

```sql
CREATE TABLE assets (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id  UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_url    TEXT NOT NULL,
    file_type   VARCHAR(50) NOT NULL,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 表5: asset_versions (资产版本)

```sql
CREATE TABLE asset_versions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_id        UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    file_url        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 表6: consistency_checks (一致性检查)

```sql
CREATE TABLE consistency_checks (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_id    UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    check_type  VARCHAR(100) NOT NULL,
    result      JSONB NOT NULL DEFAULT '{}',
    passed      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 表7: generation_tasks (生成任务) — 核心

```sql
CREATE TABLE generation_tasks (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id       UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_type        VARCHAR(50) NOT NULL,       -- image_generation / ai_detection
    status           VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- pending / processing / succeeded / failed / cancelled
    idempotency_key  VARCHAR(255),
    credits_status   VARCHAR(20) NOT NULL DEFAULT 'none',
    -- none / frozen / charged / refunded (V6积分状态机)
    input_params     JSONB NOT NULL DEFAULT '{}',
    output_result    JSONB,
    error_code       VARCHAR(100),
    error_message    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

-- 幂等去重索引
CREATE UNIQUE INDEX idx_tasks_user_idem
    ON generation_tasks (user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

#### 表8: credit_transactions (积分流水) — 不可篡改

```sql
CREATE TABLE credit_transactions (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id        UUID REFERENCES generation_tasks(id) ON DELETE SET NULL,
    type           VARCHAR(20) NOT NULL,    -- freeze / charge / refund / recharge
    amount         INTEGER NOT NULL,        -- 负数=扣除, 正数=增加
    balance_after  INTEGER NOT NULL,
    description    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- V6-06: 防止同一任务的重复冻结/扣费/退款
CREATE UNIQUE INDEX idx_credit_tx_task_type
    ON credit_transactions (task_id, type)
    WHERE task_id IS NOT NULL;
```

#### 表9: task_cost_records (任务成本)

```sql
CREATE TABLE task_cost_records (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID NOT NULL REFERENCES generation_tasks(id) ON DELETE CASCADE,
    provider        VARCHAR(50) NOT NULL,       -- bfl_flux_3 / gemini_3_8_flash
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    api_cost_cny    DECIMAL(10, 4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 表10: provider_attempts (Provider调用记录)

```sql
CREATE TABLE provider_attempts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id             UUID NOT NULL REFERENCES generation_tasks(id) ON DELETE CASCADE,
    attempt_number      INTEGER NOT NULL,
    provider            VARCHAR(50) NOT NULL,
    provider_request_id VARCHAR(255),
    provider_task_id    VARCHAR(255),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- pending / submitted / polling / succeeded / failed / timeout / uncertain
    request_payload     JSONB DEFAULT '{}',
    response_payload    JSONB,
    error_code          VARCHAR(100),
    error_message       TEXT,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    api_cost_cny        DECIMAL(10, 4),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_provider_attempts_task_num
    ON provider_attempts (task_id, attempt_number);
```

#### 配置表: platform_rules (平台规则)

```sql
CREATE TABLE platform_rules (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform    VARCHAR(50) NOT NULL,       -- amazon / temu / ozon
    rule_code   VARCHAR(50) NOT NULL,       -- AMZ-MAIN / AMZ-SWCH / TEMU-MAIN
    config      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.3 安全视图 (security_invoker=true)

```sql
-- 用户安全视图 (排除password_hash)
CREATE OR REPLACE VIEW user_profile_view AS
SELECT id, email, credits, subscription_tier, created_at, updated_at
FROM users;
ALTER VIEW user_profile_view SET (security_invoker = true);

-- Worker最小权限视图
CREATE OR REPLACE VIEW worker_user_info_view AS
SELECT id, credits, subscription_tier
FROM users;
ALTER VIEW worker_user_info_view SET (security_invoker = true);
```

### 5.4 RLS策略 (10条)

所有表启用 `ROW LEVEL SECURITY`，策略基于 `app.user_id` GUC：

| 表 | 策略 | 逻辑 |
|----|------|------|
| users | `id = current_setting('app.user_id')::UUID` | 用户只能看自己 |
| products | `user_id = current_setting('app.user_id')::UUID` | 用户只能操作自己的产品 |
| appearance_profiles | `product_id IN (SELECT id FROM products WHERE user_id = GUC)` | 通过产品链隔离 |
| assets | `user_id = GUC` | 直接用户隔离 |
| asset_versions | `asset_id IN (SELECT id FROM assets WHERE user_id = GUC)` | 通过资产链隔离 |
| consistency_checks | 同上 | 通过资产链隔离 |
| generation_tasks | `user_id = GUC` | 直接用户隔离 |
| credit_transactions | `user_id = GUC` | 直接用户隔离 |
| task_cost_records | `task_id IN (SELECT id FROM generation_tasks WHERE user_id = GUC)` | 通过任务链隔离 |
| provider_attempts | 同上 | 通过任务链隔离 |

### 5.5 索引清单

| 索引名 | 表 | 字段 | 用途 |
|--------|-----|------|------|
| idx_products_user_id | products | user_id | 产品列表查询 |
| idx_assets_user_id | assets | user_id | 资产列表查询 |
| idx_assets_product_id | assets | product_id | 产品下资产查询 |
| idx_tasks_user_id | generation_tasks | user_id | 任务列表查询 |
| idx_tasks_status | generation_tasks | status | Worker扫描待处理任务 |
| idx_credit_tx_user_id | credit_transactions | user_id | 积分流水查询 |
| idx_provider_attempts_task_id | provider_attempts | task_id | 调用记录查询 |
| idx_tasks_user_idem | generation_tasks | (user_id, idempotency_key) UNIQUE | 幂等去重 |
| idx_credit_tx_task_type | credit_transactions | (task_id, type) UNIQUE | 防重复积分操作 |
| idx_provider_attempts_task_num | provider_attempts | (task_id, attempt_number) UNIQUE | 防重复调用记录 |

### 5.6 迁移策略

```
migrations/
├── 001_schema_baseline.sql      # 创建全部表 + 视图 + RLS + 索引
├── 002_roles_permissions.sql     # 创建角色 + 列级GRANT + 默认权限
├── 001_down_rollback.sql         # 完整回滚 (DROP TABLE CASCADE)
└── 003_seed_platform_rules.sql   # [待创建] 初始化平台规则数据
```

**迁移执行规范**:
1. 每次迁移必须在事务中执行
2. 迁移前自动备份数据库
3. 迁移后运行全部测试验证
4. 回滚脚本必须可用且已测试
5. 生产迁移通过CI/CD执行，不手动psql

---

## 6. 后端模块设计

### 6.1 API服务 (FastAPI)

**已有代码**: `src/database/db_pool.py` (连接池 + 信任边界)

#### API路由设计

```
/api/v1/
├── auth/
│   ├── POST   /register           # 注册
│   ├── POST   /login              # 登录 → JWT
│   ├── POST   /refresh            # 刷新令牌
│   └── GET    /me                 # 当前用户信息 (user_profile_view)
│
├── products/
│   ├── GET    /                   # 产品列表 (RLS隔离)
│   ├── POST   /                   # 创建产品
│   ├── GET    /{id}               # 产品详情
│   ├── PUT    /{id}               # 更新产品
│   ├── DELETE /{id}               # 删除产品
│   └── POST   /{id}/appearances   # 创建外观配置
│
├── assets/
│   ├── POST   /upload             # 申请预签名URL → MinIO上传
│   ├── GET    /{id}               # 资产详情
│   ├── GET    /{id}/download      # 预签名下载URL
│   └── GET    /{id}/versions      # 版本列表
│
├── tasks/
│   ├── POST   /                   # 提交生成任务 (含幂等键)
│   ├── GET    /                   # 任务列表
│   ├── GET    /{id}               # 任务详情
│   ├── POST   /{id}/cancel        # 取消任务
│   └── GET    /{id}/events        # SSE事件流
│
├── credits/
│   ├── GET    /balance            # 当前积分余额
│   ├── GET    /transactions       # 积分流水 (分页)
│   └── POST   /recharge           # 充值 (MVP手动)
│
├── rules/
│   ├── GET    /platforms          # 支持的平台列表
│   └── GET    /{platform}         # 平台规则详情
│
└── health/
    └── GET    /                   # 健康检查
```

#### 连接池信任边界 (已有设计, V6-02)

```
JWT验证 (应用层可信)
    │ user_id 从签名令牌提取
    ▼
════════════ 信任边界跨越点 ════════════
    │
    ▼
GUC设置 (数据库层不可信)
    │ SET LOCAL app.user_id = '<uuid>'
    │ ⚠ GUC是信任提示，不是身份证明
    ▼
RLS策略 (数据库层强制)
    │ USING (id = current_setting('app.user_id')::UUID)
    ▼
security_invoker视图 (数据库层)
    │ 视图权限 = 调用者权限
    ▼
列级GRANT (数据库层)
    │ password_hash 不暴露
```

**事务入口约束 (4条规则)**:
1. user_id 只能从 JWT 提取，不能从请求参数/请求体获取
2. GUC 设置必须在事务内完成，事务结束后自动清理
3. 所有租户查询必须通过 `withTenantTransaction` 执行
4. 禁止在事务外设置 GUC (会导致 GUC 泄漏到连接池)

**连接池边界场景 (5类)**:
1. 连接泄漏 → `try/finally` 确保 `conn.reset()`
2. 事务超时 → `statement_timeout` + 归还前 `reset()`
3. 并发访问 → 每事务独占连接
4. 连接池耗尽 → `min_size=5, max_size=20, command_timeout=30s`
5. 连接中毒 → asyncpg自动检测 + 健康检查 + `reset()`

### 6.2 Worker服务

#### Worker职责

```python
# 伪代码
async def process_task(task_id):
    # 1. 获取任务 (Worker身份连接)
    async with db.withWorkerContext(worker_id, task.user_id) as conn:
        task = await get_task(conn, task_id)
        
        # 2. 积分冻结 (已在API层完成, Worker验证)
        assert task.credits_status == 'frozen'
        
        # 3. 创建provider_attempt
        attempt = await create_attempt(conn, task_id, attempt_number=1)
        
        # 4. 调用BFL FLUX 3
        result = await bfl_adapter.generate(task.input_params)
        
        if result.status == 'succeeded':
            # 5. 下载图片 → MinIO
            image_data = await download(result.image_url)
            stored_url = await minio.upload(image_data)
            
            # 6. AI检测 (Gemini)
            detection = await gemini_adapter.detect(image_data)
            
            # 7. 平台规则校验
            compliance = await check_rules(detection, task.platform)
            
            # 8. 像素回贴
            if task.protection_regions:
                final_image = await paste_pixels(
                    original=task.original_image,
                    generated=image_data,
                    regions=task.protection_regions
                )
                stored_url = await minio.upload(final_image)
            
            # 9. 完成任务 + 扣费 (原子操作)
            await complete_task(conn, task_id, stored_url, compliance)
            # → status=succeeded, credits_status=charged
            # → credit_transactions: type=charge (UNIQUE约束)
            
        elif result.status == 'failed':
            await fail_task(conn, task_id, result.error)
            # → status=failed, credits_status=refunded
            # → credit_transactions: type=refund (UNIQUE约束)
            
        elif result.status == 'uncertain':
            # V6-06: 不盲重付费, 等待人工核查
            await mark_uncertain(conn, task_id, result.provider_request_id)
```

### 6.3 模型适配器

#### BFL FLUX 3 适配器 (已有代码: `src/models/bfl_adapter.py`)

| 属性 | 值 |
|------|-----|
| 端点 | `https://api.bfl.ai/v1/flux-3/generate` |
| 状态查询 | `https://api.bfl.ai/v1/get_task` |
| 认证 | Bearer API Key |
| 模式 | 异步提交 + 轮询 |
| 轮询间隔 | 5秒 |
| 最大轮询 | 60次 (5分钟超时) |
| 幂等header | 不支持 (使用request_id策略) |
| 单次成本 | ¥0.15 |
| 速率限制 | 30次/分钟, 并发5 |
| 超时处理 | 标记 `uncertain` (V6-06) |

#### Gemini 3.8 Flash 适配器 (已有代码: `src/models/gemini_adapter.py`)

| 属性 | 值 |
|------|-----|
| 端点 | `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent` |
| 认证 | API Key (query param) |
| 模式 | 同步调用 |
| 温度 | 0.1 (低温度保证一致性) |
| 最大输出 | 2048 tokens |
| 单次成本 | ¥0.008 |
| 速率限制 | 60次/分钟, 并发10 |
| 图片输入 | 支持 (base64) |
| 结构化输出 | 支持 |

**AI检测决策矩阵 (V6-05)**:

| 检测结果 | 置信度 | 动作 |
|----------|--------|------|
| 检测到违规 | ≥ 0.75 | `non_compliant` |
| 检测到违规 | < 0.75 | `manual_review` |
| 未检测到违规 | — | `log_and_pass` + 5%抽检 |
| 检测失败 | — | `manual_review` |

---

## 7. 前端模块设计

> **注**: 前端尚未实现，以下为设计规范。GPT Work 检索补充具体库选型后，Codex 实现。

### 7.1 页面结构

```
/ (首页/登录)
/register (注册)
/login (登录)
/dashboard (工作台)
├── /products (产品列表)
│   ├── /products/new (新建产品)
│   └── /products/:id (产品详情)
│       ├── 基本信息面板
│       ├── 外观配置面板
│       ├── 资产管理 (图片上传/查看)
│       └── 任务历史
├── /tasks (任务列表)
│   ├── /tasks/new (提交新任务)
│   └── /tasks/:id (任务详情 + SSE实时状态)
├── /credits (积分中心)
│   ├── 余额展示
│   └── 流水记录
└── /settings (设置)
    ├── API Key管理 (BFL/Gemini)
    └── 平台规则查看
```

### 7.2 核心交互 — 任务提交页面

```
┌─────────────────────────────────────────────────────────┐
│  提交生成任务                                            │
├──────────────────┬──────────────────────────────────────┤
│  选择产品         │  ┌────────────────────────────────┐  │
│  [产品A ▼]       │  │     产品原图预览               │  │
│                  │  │  ┌──────────────────────┐      │  │
│  选择外观配置     │  │  │                      │      │  │
│  [外观A ▼]       │  │  │    [产品图片]         │      │  │
│                  │  │  │                      │      │  │
│  生成类型         │  │  └──────────────────────┘      │  │
│  ○ 白底主图      │  └────────────────────────────────┘  │
│  ○ 场景图        │                                       │
│  ○ 生活场景      │  保护区域 (拖拽框选)                   │
│                  │  ┌────────────────────────────────┐  │
│  目标平台         │  │  [区域1: Logo] [区域2: 标签]   │  │
│  ☑ Amazon        │  │  + 添加保护区域                │  │
│  ☐ Temu          │  └────────────────────────────────┘  │
│  ☐ Ozon          │                                       │
│                  │  幂等键 (可选)                         │
│  预估费用         │  [___________________________]       │
│  ¥0.15           │                                       │
│                  │  [     提交任务     ]                  │
└──────────────────┴──────────────────────────────────────┘
```

### 7.3 任务状态实时推送 (SSE)

```javascript
// 伪代码
const eventSource = new EventSource(`/api/v1/tasks/${taskId}/events`);

eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    // data.type: status_change | progress | result | error
    // data.status: pending → processing → succeeded/failed
    
    updateTaskUI(data);
    
    if (data.type === 'result') {
        showResult(data.result_url);
        eventSource.close();
    }
};

// 断开回退轮询
eventSource.onerror = () => {
    eventSource.close();
    pollFallback(`/api/v1/tasks/${taskId}`, 3000); // 3秒轮询
};
```

---

## 8. 安全与权限设计

### 8.1 角色体系 (已有完整SQL: `migrations/002_roles_permissions.sql`)

| 角色 | 用途 | 权限 | 关键限制 |
|------|------|------|---------|
| `app_user` | 前端用户 | 产品/资产/任务/积分 CRUD | users表仅列级GRANT(6列), 无password_hash |
| `app_worker` | 后台Worker | 任务/积分/成本/调用记录 UPDATE | users表仅列级GRANT(3列), 无password_hash |
| `app_readonly` | 只读分析 | 安全视图 + 租户表 SELECT | 撤销users全表SELECT, NOBYPASSRLS |
| `app_admin` | 管理员 | 全表 CRUD | — |

### 8.2 列级GRANT (V6-01)

```sql
-- app_user: user_profile_view 引用的6列
GRANT SELECT (id, email, credits, subscription_tier, created_at, updated_at)
    ON users TO app_user;

-- app_worker: worker_user_info_view 引用的3列
GRANT SELECT (id, credits, subscription_tier)
    ON users TO app_worker;

-- password_hash 在两个角色中均未授予 (双重防御)
```

### 8.3 安全保证链

```
1. JWT签名验证 (应用层)
   └→ user_id 可信 (从签名令牌提取)

2. withTenantTransaction (应用层→数据库层)
   └→ SET LOCAL app.user_id (事务内, 结束后清理)

3. RLS策略 (数据库层强制)
   └→ USING (id = current_setting('app.user_id')::UUID)

4. security_invoker=true视图 (数据库层)
   └→ 视图权限 = 调用者权限 (不是所有者权限)

5. 列级GRANT (数据库层)
   └→ password_hash 不在授权列表中

6. NOBYPASSRLS (数据库层)
   └→ 所有角色无法绕过RLS
```

### 8.4 禁止反模式

```python
# ❌ 禁止: 从请求体获取user_id
user_id = request.body.userId

# ❌ 禁止: 事务外设置GUC
await conn.execute("SET app.user_id = '...'")

# ❌ 禁止: 直接暴露password_hash
SELECT * FROM users  -- 即使RLS限制行, password_hash列也暴露

# ❌ 禁止: Worker以用户身份连接
conn = await pool.acquire(user_credentials)  # 应使用Worker自己的凭据
```

---

## 9. 幂等与状态机设计

### 9.1 任务状态机

```
                    ┌──────────┐
                    │ pending  │ ← 创建任务 (幂等去重)
                    └────┬─────┘
                         │
                    ┌────v─────┐
            ┌───────│processing│ ← 积分冻结 (credits_status: frozen)
            │       └────┬─────┘
            │            │
            │   ┌────────v────────┐
            │   │ provider_attempt │ ← 每次API调用记录
            │   │  (可能多次重试)   │
            │   └────────┬────────┘
            │            │
            │     ┌──────v──────┐
            │     │  succeeded  │ → 积分扣费 (credits_status: charged)
            │     └─────────────┘
            │
            └──→ ┌──────┐
                 │ failed│ → 积分退款 (credits_status: refunded)
                 └──────┘

            ┌──────────┐
            │cancelled │ → 积分退款 (credits_status: refunded, 如已冻结)
            └──────────┘
```

### 9.2 积分状态机

```
none → frozen → charged   (成功)
none → frozen → refunded  (失败/取消)
none                      (未到扣费环节就失败)
```

### 9.3 积分流水 (不可篡改)

| credits_status 变更 | credit_transactions 记录 |
|---------------------|--------------------------|
| none → frozen | type=freeze, amount=<负数>, balance_after=<冻结后余额> |
| frozen → charged | type=charge, amount=<负数>, balance_after=<扣费后余额> |
| frozen → refunded | type=refund, amount=<正数>, balance_after=<退款后余额> |

**V6唯一约束**: `UNIQUE(task_id, type) WHERE task_id IS NOT NULL`
- 同一任务只能冻结一次
- 同一任务只能扣费一次
- 同一任务只能退款一次

### 9.4 Provider状态决策矩阵 (V6-06)

| Provider返回 | DB状态 | 积分处理 | 后续动作 |
|-------------|--------|---------|---------|
| 成功 | succeeded | 扣费 | 继续: 检测→规则→回贴 |
| 明确失败 | failed | 退款 | 通知用户 |
| 超时无响应 | uncertain | 不动 | 人工通过request_id核查 |
| 轮询超时 | uncertain | 不动 | 人工核查 |
| 查询到成功 | succeeded | 扣费 | 继续 |
| 查询到失败 | failed | 退款 | 通知用户 |
| 查询不到 | uncertain | 不动 | 人工核查 |

**核心原则**: uncertain 状态绝不自动重试或盲扣费。等待人工核查确认实际结果后再决定扣费或退款。

### 9.5 幂等去重

```sql
-- 任务级幂等 (用户+幂等键唯一)
CREATE UNIQUE INDEX idx_tasks_user_idem
    ON generation_tasks (user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- 积分操作级幂等 (任务+类型唯一)
CREATE UNIQUE INDEX idx_credit_tx_task_type
    ON credit_transactions (task_id, type)
    WHERE task_id IS NOT NULL;

-- 调用记录级幂等 (任务+尝试次数唯一)
CREATE UNIQUE INDEX idx_provider_attempts_task_num
    ON provider_attempts (task_id, attempt_number);
```

---

## 10. 平台规则引擎设计

### 10.1 规则结构 (已有完整配置: `src/platform_rules/rules.json`)

```json
{
  "platforms": {
    "amazon": {
      "rules": {
        "MAIN": {
          "rule_code": "AMZ-MAIN",
          "description": "Amazon主图规则 (G1881)",
          "text_logo": "allowed",
          "evidence_status": "verified",
          "evidence_source": "G1881",
          "min_image_size": [1000, 1000],
          "max_image_size": [10000, 10000],
          "background": "white",
          "product_coverage": "85%",
          "ai_detection": {
            "enabled": true,
            "confidence_threshold": 0.75,
            "below_threshold_action": "manual_review",
            "detection_failure_action": "manual_review",
            "sampling_inspection": {
              "enabled": true,
              "rate": 0.05
            }
          }
        },
        "SWCH": {
          "text_logo": "unverified",
          "evidence_status": "unverified",
          "pre_production_action": "在Amazon Seller Central后台确认SWCH变体规则"
        }
      }
    },
    "temu": { "rules": { ... } },
    "ozon": { "rules": { ... } }
  }
}
```

### 10.2 V6修正要点

| 规则 | V5状态 | V6修正 | 原因 |
|------|--------|--------|------|
| Amazon SWCH text_logo | `allowed` | `unverified` | G1881原文未明确针对SWCH |
| Amazon PT01-99 text_logo | `conditional` | `conditional_unverified` | 同上 |
| AI检测 below_threshold | `warn` | `manual_review` | 低置信度不应自动放行 |
| AI检测 failure | 未定义 | `manual_review` | 检测失败不应自动通过 |
| AI检测 抽检 | 无 | 5%抽检率 | 即使通过也可能有问题 |

### 10.3 规则校验流程

```python
async def check_compliance(image, platform, rule_code):
    # 1. 加载规则
    rule = load_rule(platform, rule_code)
    
    # 2. 图片基本属性校验
    check_size(image, rule.min_image_size, rule.max_image_size)
    check_background(image, rule.background)
    check_coverage(image, rule.product_coverage)
    
    # 3. AI检测 (如果启用)
    if rule.ai_detection.enabled:
        detection = await gemini_adapter.detect(image)
        
        if detection.confidence >= rule.ai_detection.confidence_threshold:
            if detection.has_violations:
                return ComplianceResult.NON_COMPLIANT
            else:
                # 通过检测, 但有5%抽检
                if random() < rule.ai_detection.sampling_inspection.rate:
                    return ComplianceResult.MANUAL_REVIEW
                return ComplianceResult.COMPLIANT
        else:
            return ComplianceResult.MANUAL_REVIEW  # V6-05
    
    # 4. 文字/Logo规则校验
    check_text_logo(detection, rule.text_logo)
    
    return ComplianceResult.COMPLIANT
```

---

## 11. 像素保护与图像处理

### 11.1 已有代码: `src/pixel_protection/pixel_paste.py`

#### EXIF Orientation修正 (V4整改)

```python
class ExifOrientation(IntEnum):
    NORMAL = 1          # 无需旋转
    FLIP_HORIZONTAL = 2
    ROTATE_180 = 3
    FLIP_VERTICAL = 4
    TRANSPOSE = 5       # 90CW + flip-H
    ROTATE_90_CW = 6    # 顺时针90度
    TRANSVERSE = 7      # 90CCW + flip-H
    ROTATE_90_CCW = 8   # 逆时针90度

def apply_exif_orientation(img: Image) -> Image:
    orientation = get_exif_orientation(img)
    if orientation == ExifOrientation.ROTATE_90_CW:    # 6 → PIL ROTATE_270
        return img.transpose(Image.ROTATE_270)
    elif orientation == ExifOrientation.ROTATE_90_CCW: # 8 → PIL ROTATE_90
        return img.transpose(Image.ROTATE_90)
    # ... 其他情况
```

**V4修正关键**: EXIF Orientation 6 (顺时针90度) 对应 PIL 的 `ROTATE_270`，不是 `ROTATE_90`。这是因为 PIL 的 ROTATE 方向与 EXIF 定义相反。

#### 像素回贴

```python
@dataclass
class PasteRegion:
    x: int
    y: int
    width: int
    height: int

def paste_pixels(base_image, source_image, regions: list[PasteRegion]) -> bytes:
    """
    将source_image中指定区域的像素回贴到base_image对应位置
    用于保护品牌Logo、产品细节等区域不被AI修改
    """
    base = Image.open(io.BytesIO(base_image))
    source = Image.open(io.BytesIO(source_image))
    
    for region in regions:
        # 从原图裁剪保护区域
        crop = source.crop((region.x, region.y, 
                           region.x + region.width, 
                           region.y + region.height))
        # 回贴到生成图片
        base.paste(crop, (region.x, region.y))
    
    output = io.BytesIO()
    base.save(output, format='PNG')
    return output.getvalue()
```

#### 保护区域遮罩

```python
def create_protection_mask(image_size, regions: list[PasteRegion]) -> Image:
    """创建保护区域遮罩图 (红色半透明标记保护区域)"""
    mask = Image.new('RGBA', image_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(mask)
    for region in regions:
        draw.rectangle(
            [region.x, region.y, region.x + region.width, region.y + region.height],
            fill=(255, 0, 0, 128)  # 红色半透明
        )
    return mask
```

---

## 12. 测试计划

### 12.1 已有测试 (156项全部通过)

| 测试文件 | 测试数 | 覆盖范围 |
|----------|--------|---------|
| `test_schema_rls.py` | 57项 | 10张表字段验证、RLS策略、列级GRANT、安全视图、V6唯一约束 |
| `test_platform_rules.py` | 44项 | Amazon MAIN/SWCH/PT规则、Temu规则、Ozon规则、AI检测决策矩阵 |
| `test_model_adapters.py` | 16项 | BFL端点/轮询/超时、Gemini端点/温度/结构化输出 |
| `test_pixel_protection.py` | 28项 | EXIF Orientation 8种值、像素回贴、遮罩创建、V4旋转修正 |

### 12.2 待开发测试 (Codex 实现后 WorkBuddy AI 编写)

| 测试类别 | 编号 | 内容 | 前置 |
|----------|------|------|------|
| **API集成测试** | IT-01 | 注册→登录→JWT验证 | FastAPI搭建 |
| | IT-02 | 产品CRUD + RLS隔离 | 数据库迁移 |
| | IT-03 | 资产上传 → MinIO → 下载 | MinIO部署 |
| | IT-04 | 任务提交 → 幂等去重 | 任务队列 |
| | IT-05 | 积分冻结→扣费→退款 原子操作 | 积分系统 |
| **Worker测试** | WT-01 | 任务消费 → BFL调用 → 结果存储 | BFL API Key |
| | WT-02 | AI检测 → Gemini调用 → 置信度判断 | Gemini API Key |
| | WT-03 | 像素回贴 → EXIF修正 → 输出 | Pillow |
| | WT-04 | uncertain状态处理 (超时不盲扣费) | Mock BFL |
| **安全测试** | ST-01 | 跨用户数据访问 → 404/拒绝 | 多用户环境 |
| | ST-02 | 列级GRANT → password_hash不可访问 | PG权限 |
| | ST-03 | GUC泄漏 → 连接池reset验证 | 并发测试 |
| | ST-04 | JWT伪造/过期 → 拒绝 | 安全扫描 |
| **端到端测试** | E2E-01 | 完整流程: 上传→生成→检测→回贴→下载 | 全栈 |
| | E2E-02 | 平台规则校验: Amazon MAIN → 合规/不合规 | 规则引擎 |
| | E2E-03 | 取消任务 → 积分退款 | 任务取消 |
| **性能测试** | PT-01 | 20并发API → p95<500ms | 压测工具 |
| | PT-02 | 任务队列 → 10并发生成 | BFL限流 |

### 12.3 测试规范

**证据记录**: 每次测试记录 test_id、状态、commit、环境、命令、exit code、时间、request ID、输入输出hash、真实费用、截图/日志位置。

**分层标准**:
- 单元测试: 每个函数/方法的输入输出
- 集成测试: API → DB → MinIO → MQ
- 端到端测试: 用户操作 → 真实结果
- 安全测试: 攻击向量 + 权限边界
- 性能测试: p95指标 + 并发

**禁止**:
- mock不替代真实供应商验收
- 开发模式性能不替代production基准
- 平均值不替代p95
- 选择性剔除失败样本

---

## 13. 开发任务分解

### 13.1 任务清单 (Codex 执行)

#### Phase 1: 基础设施搭建 (Day 1-3)

| Task ID | 任务 | 输入 | 输出 | 依赖 |
|---------|------|------|------|------|
| D1-01 | 项目初始化 | 技术栈确认 | Python项目骨架 + 依赖文件 | 无 |
| D1-02 | Docker Compose环境 | Dockerfile | PG15 + MinIO + RabbitMQ | D1-01 |
| D1-03 | 数据库迁移执行 | `001_schema_baseline.sql` | 10张表 + 视图 + RLS + 索引 | D1-02 |
| D1-04 | 角色权限执行 | `002_roles_permissions.sql` | 4角色 + 列级GRANT | D1-03 |
| D1-05 | 平台规则数据初始化 | `rules.json` | `003_seed_platform_rules.sql` | D1-03 |

#### Phase 2: 后端核心 (Day 4-10)

| Task ID | 任务 | 输入 | 输出 | 依赖 |
|---------|------|------|------|------|
| D2-01 | 连接池模块 | `db_pool.py` (已有) | 集成到FastAPI | D1-03 |
| D2-02 | JWT认证模块 | PyJWT/authlib | 注册/登录/刷新/中间件 | D2-01 |
| D2-03 | 产品CRUD API | FastAPI路由 | 5个端点 + RLS验证 | D2-02 |
| D2-04 | 资产上传/下载 API | MinIO SDK | 预签名URL + 元数据管理 | D2-03 |
| D2-05 | 任务提交 API | 幂等键设计 | POST /tasks + 幂等去重 | D2-04 |
| D2-06 | 积分系统 API | 积分状态机 | 余额 + 流水 + 冻结/扣费/退款 | D2-05 |
| D2-07 | SSE事件推送 | EventSource | 任务状态实时推送 | D2-05 |

#### Phase 3: Worker服务 (Day 8-14)

| Task ID | 任务 | 输入 | 输出 | 依赖 |
|---------|------|------|------|------|
| D3-01 | Worker骨架 | RabbitMQ Consumer | 消费任务消息 | D2-05 |
| D3-02 | BFL适配器集成 | `bfl_adapter.py` (已有) | 真实API调用 + 轮询 | D3-01 |
| D3-03 | Gemini适配器集成 | `gemini_adapter.py` (已有) | 真实AI检测 | D3-02 |
| D3-04 | 平台规则校验 | `rules.json` (已有) | 合规判定逻辑 | D3-03 |
| D3-05 | 像素回贴集成 | `pixel_paste.py` (已有) | 像素保护 + EXIF修正 | D3-04 |
| D3-06 | 积分原子操作 | UNIQUE约束 | 冻结→扣费/退款 | D3-05 |
| D3-07 | uncertain状态处理 | V6-06设计 | 超时标记 + 不盲扣费 | D3-06 |

#### Phase 4: 前端 (Day 10-18)

| Task ID | 任务 | 输入 | 输出 | 依赖 |
|---------|------|------|------|------|
| D4-01 | 前端项目初始化 | React + UI库 | 项目骨架 + 路由 | 无 |
| D4-02 | 登录/注册页面 | JWT API | 表单 + Token管理 | D2-02 |
| D4-03 | 产品管理页面 | 产品API | 列表 + 详情 + CRUD | D2-03 |
| D4-04 | 图片上传组件 | MinIO预签名 | 拖拽上传 + 预览 | D2-04 |
| D4-05 | 任务提交页面 | 任务API | 表单 + 保护区域框选 | D4-04 |
| D4-06 | 任务详情页面 | SSE API | 实时状态 + 结果展示 | D2-07 |
| D4-07 | 积分中心页面 | 积分API | 余额 + 流水列表 | D2-06 |

#### Phase 5: 集成与测试 (Day 15-22)

| Task ID | 任务 | 输入 | 输出 | 依赖 |
|---------|------|------|------|------|
| D5-01 | 端到端联调 | 全栈 | 完整流程可运行 | D3-07 + D4-07 |
| D5-02 | WorkBuddy AI测试 | 测试计划 | 测试报告 + 缺陷列表 | D5-01 |
| D5-03 | Codex修复 | 测试报告 | 修复提交 | D5-02 |
| D5-04 | 性能测试 | 压测工具 | p95指标报告 | D5-03 |
| D5-05 | 安全测试 | 安全扫描 | 渗透测试报告 | D5-03 |

#### Phase 6: 部署上线 (Day 20-25)

| Task ID | 任务 | 输入 | 输出 | 依赖 |
|---------|------|------|------|------|
| D6-01 | 生产环境搭建 | Docker Compose | 生产服务器 | D5-04 |
| D6-02 | CI/CD流水线 | GitHub Actions | 自动测试 + 构建 + 部署 | D5-03 |
| D6-03 | 监控配置 | Prometheus/Grafana | 告警 + 仪表盘 | D6-01 |
| D6-04 | 文档交付 | 完整代码 | API文档 + 运维手册 | D5-03 |

### 13.2 关键路径

```
D1-01 → D1-02 → D1-03 → D2-01 → D2-02 → D2-03 → D2-05 → D3-01 → D3-02 → D3-05 → D3-06 → D5-01
                  │                                                    │
                  └── D2-07 ←───────────────────────────────────────────┘
                                                            │
D4-01 → D4-02 → D4-03 → D4-04 → D4-05 → D4-06 ←────────────┘
```

**关键路径耗时**: 约18-22天 (含联调)

---

## 14. 部署与运维

### 14.1 首期部署方案 (单机Docker Compose)

```yaml
# docker-compose.yml (结构)
services:
  postgres:
    image: postgres:15-alpine
    volumes: [pg_data:/var/lib/postgresql/data]
    environment: [POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD]
    
  minio:
    image: minio/minio
    command: server /data
    volumes: [minio_data:/data]
    ports: ["9000:9000", "9001:9001"]
    
  rabbitmq:
    image: rabbitmq:3-management-alpine
    ports: ["5672:5672", "15672:15672"]
    
  api:
    build: ./api
    depends_on: [postgres, minio, rabbitmq]
    ports: ["8000:8000"]
    env_file: .env
    
  worker:
    build: ./worker
    depends_on: [postgres, minio, rabbitmq]
    env_file: .env
    
  web:
    build: ./web
    depends_on: [api]
    ports: ["3000:3000"]
```

### 14.2 环境变量

```env
# 数据库
DATABASE_URL=postgresql://app:password@postgres:5432/visual_platform
PG_MIN_POOL_SIZE=5
PG_MAX_POOL_SIZE=20
PG_COMMAND_TIMEOUT=30

# MinIO
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_BUCKET=visual-assets

# RabbitMQ
RABBITMQ_URL=amqp://rabbitmq:5672
QUEUE_NAME=generation_tasks

# BFL
BFL_API_KEY=...
BFL_API_ENDPOINT=https://api.bfl.ai/v1/flux-3/generate

# Gemini
GEMINI_API_KEY=...

# JWT
JWT_SECRET=...
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# 积分
DEFAULT_FREE_CREDITS=100
BFL_COST_PER_IMAGE=15  # 积分 (¥0.15 = 15积分, 1积分=¥0.01)
GEMINI_COST_PER_DETECTION=1  # 积分
```

### 14.3 扩展路径

| 阶段 | 扩展 | 触发条件 |
|------|------|---------|
| MVP | 单机Docker Compose | 首期上线 |
| 扩展1 | API + Worker 分离部署 | API延迟 > 500ms |
| 扩展2 | 数据库独立 + 读副本 | 连接池耗尽 |
| 扩展3 | GPU Worker独立 | BFL排队 > 5分钟 |
| 扩展4 | K8s编排 | 日任务量 > 10000 |

---

## 15. 风险与未决项

### 15.1 技术风险

| 风险 | 影响 | 缓解 | 状态 |
|------|------|------|------|
| BFL API变更 | 适配器失效 | 端点/响应格式版本锁定 | 待验证最新API |
| Gemini限额 | 检测瓶颈 | 速率限制 + 队列缓冲 | 需确认配额 |
| PG连接泄漏 | 服务不可用 | reset() + 健康检查 | 已有设计 |
| 图片大文件 | 上传超时 | 分片上传 + 压缩 | 待实现 |
| RLS误配置 | 数据泄漏 | 自动化RLS负例测试 | 已有57项测试 |

### 15.2 产品风险

| 风险 | 影响 | 缓解 | 状态 |
|------|------|------|------|
| Amazon SWCH/PT规则不确定 | 合规误判 | 标注unverified + pre_production_action | V6已处理 |
| AI检测准确率不足 | 误放行/误拦截 | manual_review + 5%抽检 | V6已处理 |
| 积分重复扣费 | 用户损失 | UNIQUE约束 + uncertain状态 | V6已处理 |
| 像素回贴位置偏差 | 品牌Logo错位 | 用户手动框选 + 预览确认 | 待UX验证 |

### 15.3 未决项 (待GPT Work检索补充后关闭)

| 编号 | 问题 | 负责方 | 依赖 |
|------|------|--------|------|
| U-01 | FastAPI vs 其他框架最终选型 | GPT Work | §3.1检索结果 |
| U-02 | 前端画布库最终选型 (Konva vs其他) | GPT Work | §3.2检索结果 |
| U-03 | 任务队列最终选型 (Celery vs ARQ vs其他) | GPT Work | §3.1检索结果 |
| U-04 | 迁移工具最终选型 (Alembic vs其他) | GPT Work | §3.1检索结果 |
| U-05 | BFL FLUX 3 API最新文档验证 | GPT Work | §3.5检索结果 |
| U-06 | Gemini 3.8 Flash API最新文档验证 | GPT Work | §3.5检索结果 |
| U-07 | Amazon G1881最新版本确认 | 用户 | — |
| U-08 | 生产服务器预算和地区 | 用户 | — |
| U-09 | BFL/Gemini API Key获取和预算 | 用户 | — |
| U-10 | 商业上线时间节点 | 用户 | — |

---

## 附录A: V6整改完整清单

| 任务ID | 整改内容 | 证据文件 | 状态 |
|--------|---------|---------|------|
| V6-01 | security_invoker列级GRANT (app_user 6列, app_worker 3列) | `002_roles_permissions.sql` | ✅ |
| V6-02 | JWT/GUC信任边界图解 + 5类连接池边界场景 + Fastify范式 | `db_pool.py` | ✅ |
| V6-03 | app_readonly撤销全表SELECT, password_hash不再暴露 | `002_roles_permissions.sql` | ✅ |
| V6-04 | SWCH→unverified, PT→conditional_unverified + pre_production_action | `rules.json` | ✅ |
| V6-05 | AI检测 warn→manual_review + detection_failure_action + 5%抽检 | `rules.json` | ✅ |
| V6-06 | UNIQUE(task_id,type)约束 + uncertain状态 + 原子操作伪代码 | `001_schema_baseline.sql` + `IDEMPOTENCY_DESIGN.md` | ✅ |
| V6-07 | 35项新测试, 145项(扩展后156项)全部通过 | `test_*.py` | ✅ |

---

## 附录B: Infinite Workbench 可复用资源对照

| V2.1项目模块 | Infinite Workbench对应 | 复用方式 | 说明 |
|-------------|----------------------|---------|------|
| PostgreSQL RLS | `001_workspace.sql` 19个迁移 | 参考 | RLS策略模板、security_invoker写法 |
| 连接池信任边界 | `imageProviderContract.ts` | 参考 | 契约设计模式 |
| BFL/Gemini适配器 | `imageProviders.ts` | 参考 | API调用/错误归一/超时处理 |
| 积分系统 | `budgets` + `budget_reservations` | 对照 | 冻结/扣费/退款模式验证 |
| 幂等设计 | `idempotency_records`表 | 对照 | 四项作用域+key唯一 |
| 任务状态机 | `runs` + `run_steps` + `run_attempts` | 参考 | 状态转换+attempt追踪 |
| 测试策略 | T01-T20测试用例矩阵 | 参考 | 证据记录标准 |
| 开发规则 | 12项基线 + 变更交付规范 | 继承 | 先契约再实现, 先Schema再迁移 |
| 部署架构 | 模块化单体API + 独立Worker | 继承 | 同一架构理念 |
| 故障边界 | unknown状态 + request_id核查 | 对照 | uncertain状态处理一致 |

---

## 附录C: GPT Work 执行指令

> **致 GPT Work**:
>
> 1. 阅读 §3 待检索补充资源清单，逐项检索本地文件系统和GitHub
> 2. 为每个检索项找到1-3个具体可用的代码/库/工具
> 3. 补充到对应章节的技术选型中，标注推荐选项和理由
> 4. 特别关注 §3.5 GitHub开源参考中的项目，找到可直接参考或引入的开源代码
> 5. 检查本地已有资源 (§2) 中是否有遗漏的可复用文件
> 6. 输出补充后的完整技术选型表，关闭 §15.3 中的未决项
>
> **本地关键路径**:
> - V2.1项目: `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-20-21-09\v2.1-project\`
> - Infinite Workbench: `C:\Users\MECHREVO\WorkBuddy AI\2026-11-11-02-59-04\Infinite-workbench\`
> - 旧画布: `C:\Users\MECHREVO\Documents\Codex\2026-09-26\xian-z\work\ai-commerce-canvas\`
> - GitHub仓库: `https://github.com/wei-boop312/Infinite-workbench.git`

---

## 附录D: Codex 执行指令

> **致 Codex**:
>
> 1. 以本计划书为唯一设计依据，V6冻结的设计基线不可更改
> 2. 已有代码文件 (§2.1) 可直接使用，标注来源
> 3. 按 §13 任务分解顺序开发，每个Phase完成后提交GitHub
> 4. 每次提交记录: Task ID、影响范围、测试结果、commit hash
> 5. 安全设计 (§8) 不可妥协: RLS + 列级GRANT + JWT→GUC信任链
> 6. 幂等设计 (§9) 不可妥协: UNIQUE约束 + uncertain状态 + 不盲扣费
> 7. 平台规则 (§10) 的 unverified/manual_review 状态不可降级
> 8. 遇到设计未覆盖的情况，记录为未决项 (§15.3)，不自行决策
> 9. 代码提交到: `https://github.com/wei-boop312/Infinite-workbench.git` (或新仓库)
> 10. WorkBuddy AI 将在每次提交后拉取测试并出具报告

---

**文档结束**

*本计划书整合了 V2.1 设计验证分支 (V6冻结基线) 和 Infinite Workbench (本地实现进行中) 的全部设计资产，作为后续开发执行的唯一依据。*
