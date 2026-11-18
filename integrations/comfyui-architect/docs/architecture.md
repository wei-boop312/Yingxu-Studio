# 架构说明

## 2.1 草稿优先与 Manager 修复闭环

2.1 将两个状态明确分开：

- `creatable`：存在经过审核的具体模板，可以生成工作流或草稿。
- `execution_ready`：模板中的节点和模型均可用，并通过执行前验证。

缺少依赖时，编译器保留目标节点类型、布局、连线意图和参数，输出
`draft=true` 的工作流，同时写入 `dependency_report`。前端使用 Manager 的节点映射
寻找候选包，并提示用户选择安装、启用、修复、更新或替换。用户完成操作并重启后，
插件刷新 `/object_info` 并使用原任务重新编译。草稿不会被直接排队执行。

## 数据流

```text
中文需求
  → RequirementParser
  → TaskSpec
  → CapabilityRegistry
  → TemplateRepository / WorkflowPlanner
  → WorkflowCompiler
  → WorkflowValidator
  → 前端预览
  → 增量插入当前画布
```

## 模块职责

- `schemas.py`：不可变 dataclass 数据模型、范围约束和字典转换。
- `parser.py`：本地关键词与正则解析器；不调用语言模型。
- `registry.py`：读取 ComfyUI `NODE_CLASS_MAPPINGS`，调用真实 `INPUT_TYPES`、
  `RETURN_TYPES` 等定义并缓存；单个坏节点不会使扫描失败。
- `templates.py`：读取抽象模板，并只选择 `verified=true` 且与当前注册表兼容
  的具体模板。
- `planner.py`：按抽象阶段计算已满足和缺失能力。能力或具体模板缺失时，
  `executable=false`。
- `compiler.py`：对具体模板执行确定性编译，生成 UI workflow 与 API prompt；
  分配稳定 ID、坐标、连线、中文分组和生成器元数据。
- `validator.py`：检查节点存在性、必填输入、引用、端口类型、输出可达性、
  占位符和疑似远程节点。
- `service.py`：编排解析、规划、编译和校验，使用 ComfyUI 的安全模型列表接口
  检查模板声明的模型。
- `routes.py`：注册 ComfyUI 后端路由，限制请求大小并隔离异常。
- `web/js/workflow_architect.js`：中文面板、API 调用、状态展示、导出和事务式插入。

## 失败安全

规划和插入分离。前端只有在 `compiled != null` 且 `validation.valid=true` 时才启用
插入。插入使用 `beforeChange/afterChange` 包围单次变更；节点创建或连线失败时，
本次新增节点和分组会回滚，已有画布内容不变。

## 稳定性

同一任务规格、能力快照和具体模板使用模板顺序分配连续 ID，不使用随机数或时间戳。
因此生成结构可重复比较。运行时缓存只在显式刷新或插件初始化时更新。

## 安全边界

- 不执行自然语言、模板中的代码或命令。
- 后端不接受任意文件路径。
- 模板只允许有限占位符，剩余占位符会导致校验失败。
- 节点类必须来自当前注册表。
- 名称疑似 HTTP、API、Cloud、Remote、Upload 或 WebSocket 的节点默认拒绝。
- 模型扫描只允许预定义的 ComfyUI 模型分类，不遍历任意目录。
