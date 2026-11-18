# 启动说明与边界

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
