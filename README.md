# 映序 Studio · Yingxu Studio

映序 AI 视觉生产工作台：围绕真实商品资料，将事实确认、套图策划、制作、验收、交付与案例回流连接起来。

**当前阶段：源码整合基线。两套原有工程已归入同一仓库；统一应用、共享业务数据和 ComfyUI 执行连接尚未实现，不能视为已完成合并或通过生产稳定性验收。**

## 仓库内容

| 位置 | 内容 | 当前状态 |
| --- | --- | --- |
| [apps/commerce](apps/commerce/README.md) | 跨境电商 AI 视觉生产平台，含前端、API、Worker、迁移、测试与依赖锁定 | 保留原有工程边界 |
| [integrations/comfyui-architect](integrations/comfyui-architect/README.md) | ComfyUI Workflow Architect 2.15.2 | 保留原有扩展工程 |
| [docs/sop](docs/sop/README.md) | 电商视觉生产统一规范 V1.3 原文 | 业务设计依据 |
| [docs/PRODUCT_DIRECTION.md](docs/PRODUCT_DIRECTION.md) | 产品定位、SOP 闭环与形态建议 | 待实现的设计方向 |
| [docs/INTEGRATION_PLAN.md](docs/INTEGRATION_PLAN.md) | 模块分工、阶段和验收边界 | 后续实施计划 |
| [docs/REMEDIATION_PLAN.md](docs/REMEDIATION_PLAN.md) | 五类问题解决方案、测试要求和 15 项发现的阶段对应 | 方案已记录，代码待实施 |
| [docs/audit](docs/audit/README.md) | 15 项审计发现及修复验收要求 | 尚未修复、尚未销项 |
| [docs/provenance](docs/provenance/README.md) | 上游版本、逐文件哈希与导入范围 | 可追溯基线 |

## 如何开始

先阅读[当前状态](docs/STATUS.md)与[启动说明](docs/GETTING_STARTED.md)。本次只归集源码和材料，未安装依赖、运行数据库迁移、启动服务或调用付费模型；根目录没有统一启动入口。

电商平台必须从 `apps/commerce` 目录按其原有 README 配置与运行。ComfyUI 架构师沿用原有扩展安装方式。当前仓库不能直接作为一个完整 ComfyUI 插件安装，也未完成 ComfyUI Desktop 适配验证。

## 业务闭环

商品资料与任务 → 事实及 SKU 确认 → 套图策划 → 逐图制作 → 对照验收 → 独立文件交付 → 案例回流。

商品身份与真实性先于风格；生成成功不等于验收通过；提示词不等于已制作文件。缺少某个槽位的证据时，只阻塞受影响部分。

许可证与上游归属见 [LICENSE_NOTES.md](LICENSE_NOTES.md)。原仓库的历史运行日志、截图及本机凭证不随此基线导入，具体范围见来源说明。
