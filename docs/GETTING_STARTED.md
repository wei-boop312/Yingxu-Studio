# 启动说明与边界

2026-11-19 已新增根级运行入口。以下为本轮用法，后文保留最初导入时说明。

## 当前本地用法

需要 Python 3.13/3.14、Node 24、pnpm 11.25、uv 和 Docker Engine。Windows 可使用 WSL Ubuntu 内的 Docker。从仓库根目录执行：

```powershell
uv sync --locked --project apps/commerce
uv sync --locked --extra test --project integrations/comfyui-architect
pnpm --dir apps/commerce/apps/web install --frozen-lockfile
pnpm --dir apps/commerce/apps/web build
python scripts/studio.py check --report work/offline-check.json
```

根级检查不安装依赖、不启动服务、不调用模型；已有报告禁止覆盖，复验使用新文件名。

首次创建专属开发服务和离线配置：

```powershell
apps/commerce/.venv/Scripts/python.exe scripts/dev_services.py up
apps/commerce/.venv/Scripts/python.exe scripts/dev_services.py migrate-test
apps/commerce/.venv/Scripts/python.exe scripts/dev_services.py bootstrap
```

测试 PG 55432、开发 PG 55433、MQ 5679、私有 S3 18333，均绑定 loopback。配置保存在被忽略的 .local/services；应用私有配置和测试账号保存在 apps/commerce/.local，只供本机操作者读取。后续用 `dev_services.py resume` 恢复所拥有服务；等待进程可防止 WSL 提前关闭。`down` 会清理本项目临时容器及数据，不能作为保留数据的日常停机方法。

```powershell
python scripts/studio.py start api
python scripts/studio.py start publisher
python scripts/studio.py start worker
python scripts/studio.py start preview
```

访问 `http://127.0.0.1:5173/#suites`。新套图页支持资料、策划、人工导入、审核和部分 ZIP 交付；视觉工作台保留已有单图流程。当前启动器只接受离线配置。

`python scripts/studio.py status api` 查询所拥有进程，`stop api` 停止其进程树；其他组件同理。停止前检查 PID 创建身份，不影响用户其他进程或 Comfy Desktop。升级迁移应先停止本项目 API/Worker，再执行 `dev_services.py migrate-test` 和 `migrate-dev`，最后重新启动，避免复用迁移前的数据库查询缓存。

```powershell
apps/commerce/.venv/Scripts/python.exe scripts/dev_services.py test --pattern 'test*.py' --report work/native-current.json
pnpm --dir apps/commerce/apps/web exec playwright install chromium
$env:PLAYWRIGHT_CHANNEL='chromium'
pnpm --dir apps/commerce/apps/web exec playwright test e2e/studio.spec.ts
```

原生测试零用例、跳过或失败均不能通过。浏览器测试创建隔离离线账号，使用合成商品。根级 CI 执行两个工程的检查、双平台静态检查及 Linux PG/MQ/S3、生产浏览器和恢复流程；以对应提交实际结果为准。

本机已诊断 Desktop 1.1.9 / 服务 0.43.0。统一执行和真实模型质量仍待验，不能将可打开页面或图片处理当作模型质量通过。生产迁移、发布和付费调用没有自动启用。

## 初始导入说明（历史）

本次归集保留两个独立工程，没有新增统一启动脚本。请从对应子目录运行原工程命令，避免把原 README 的相对路径误当成仓库根路径。

## 电商平台

进入 `apps/commerce` 后阅读其 `README.md`、`docs/LOCAL_WINDOWS_TRIAL.md`、`docs/COMPOSE.md` 和 `.env.example`。依赖声明、锁文件、迁移、Compose 与测试均在该目录内。

`.env.example` 是占位模板，真实密钥、数据库与存储账号必须放入本机私有配置。原工程默认离线；本次未安装依赖或启动服务，不保证当前机器能直接运行。不得自动复用旧文档中的付费授权。

## ComfyUI 架构师

阅读 `integrations/comfyui-architect/README.md`、`requirements.txt` 和 `pyproject.toml`。安装扩展时使用该子目录内完整工程，按原 README 放入 ComfyUI 的扩展目录；整个 Yingxu-Studio 根目录不是该扩展的入口。

使用新的导入路径不表示已通过 ComfyUI 或 ComfyUI Desktop 实机验收。模型、依赖、节点和访问配置仍需在目标机器核实。

## 原始工程文件的范围

除电商平台 `docs/evidence/` 历史运行证据目录外，两个工程与 SOP 的固定版本文件全部保留。历史运行证据仍可从原仓库固定提交读取。旧文档中指向该目录的历史链接在此基线中可能缺失；不能据旧记录声称新仓库测试已通过。

原 CI 定义保存在 `apps/commerce/.github/workflows`，GitHub 不会把子目录中的该文件当作新仓库工作流执行。本次未新增或启用根目录自动部署与 CI；后续实施时应明确工作目录、依赖与真正的验收范围。
