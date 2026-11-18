# 本地知识检索辅助编译 · 第一阶段

本模块把知识存入SQLite，而不是训练进模型权重。当前为无依赖的关键词/中文单字双字检索基线，**未下载或启用Embedding、重排器、本地聊天模型或付费API**，不能标成已完成语义RAG或API大模型协调。

## 已实现

2.13.0增补：在原82条资料上新增24张本地原创提示词卡，共106条。提示词卡仅reference-only，不参与模板编译或证明模型可用；见[prompt-library.md](prompt-library.md)。以下82条是原基线构成，未删除。完整知识集SHA变化后，旧规划必须重新检索。

- 55条来源摘要、17条研究配方、10份原有模板，共82条带出处/证据级别/内容SHA的文档。
- SQLite application_id与schema版本隔离，拒绝覆写外来库；内容版本取完整数据集SHA256，记录不可就地改标，最多16版且不删历史腾空间。每次检索校验记录集与全部正文，删除/篡改失败关闭。
- 先按解析出的任务和stable/low_vram筛选模板；当前节点/模型选项缺失明确列出。source/recipe仅参考，不升级成compiler模板，verified旧字段不当成新模型实际验收。
- POST /workflow-architect/knowledge/assist：默认仅检索。build_draft必须严格true才尝试编译，模型与图片须明确选择，不自动取第一项，不接受任意模板/节点/API prompt/数据库路径/远程planner注入。
- 编译仍走原可信模板与实际registry/validator，返回execution_authorized=false，**从不调用Comfy /prompt**，也不读执行历史或写画布。
- validate_api_choice为未来API规划器的离线输出合同：仅允许当前检索出的兼容模板与真实依据编号。它本身不是调用器、当前许可或任务执行证明。

## 数据库构建

```powershell
python -B tools/build_knowledge.py --output existing-directory/commerce-knowledge.sqlite3
```

目录需预先存在。已存在的非本插件数据库拒绝打开写入；无需FTS扩展、向量库服务或模型下载。HTTP第一阶段使用请求范围的内存SQLite实例，避免运行时对用户Comfy目录新增持久写入；CLI可构建持久知识库。尚无后台学习、用户资料自动入库或跨进程增量同步。

## API输入示例（只访问本机插件，不调用大模型）

```json
{"text":"把图片调整尺寸为192x96","build_draft":false}
```

首次响应selection提供实际模型/图片选项，不默认选第一项。编译须携带首次响应expected并保持原text/options，明确model_overrides/input_overrides，build_draft=true。没有选输入会返回selection-required；旧需求、知识/节点版本变化拒绝。节点或模型缺失只参考/列出缺失，不伪造执行方案。响应包括corpus_revision、document_sha256、registry_generation、分数与依赖状态。版本/摘要是追踪字段，不是授权凭据。商品布局读不到实际所选原图尺寸拒绝猜测。导出原工作流JSON保留知识依据摘要，不排队运行。

## 后续接口及边界

下一步可用评测集比较本地检索与Embedding/重排器的提升，再人工确认下载Qwen3-Embedding-0.6B或其他选定模型；当前GPU优先留给Comfy。API接线另需明确供应商、确切模型、单次和总费用上限、数据发送范围与安全凭证配置。只发必要脱敏节点/文档与需求，不默认外发图像、路径、密钥或完整历史；调用次数有界，无默认重试或安装行为。

大模型只输出结构化方案，不直接生成可执行Python/安装指令；先验证依据与模板，再由插件编译和人工确认。知识中的网页/README/报错是非可信数据，不能覆盖规则；错误和成功案例经人工审核后另存版本，不自动认定画质/商用许可。

当前只支持旧模板覆盖的单任务；多阶段需求、全新模型工作流、Embedding和API推理仍未完成。静态编译通过不代表商品保真、资源足够或GPU效果已验收。SQLite校验不提供整库一致伪造/回滚的外部签名保证，也不是多租户服务的权限隔离。
## 2.4.0工作区状态

新增主面板“本地知识辅助”入口，明确选择后可编译和导出草稿，不自动插图或运行。实际安装与浏览器验收以本轮归档报告为准。HTTP接口每次使用内存索引，归档SQLite由显式CLI建立，当前不是持续学习后台。需求/运行配置变化立即关闭旧草稿，关闭或重新打开面板隔离迟到回调；单次30秒AbortSignal取消、响应1MiB/4096分片严格UTF8。此取消不是恶意非合作fetch或同步CPU强制终止保证。

