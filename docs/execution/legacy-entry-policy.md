# 原详情页执行入口

本轮统一任务所有权沿用电商平台的 app.submit_quoted_task、ops.claim_task、app.record_intent、fencing token 和原始回执。

旧详情面板不再通过浏览器 journal 直接 POST /prompt。编译、工作流导出、读取已有历史和结果导出保留。即使程序触发按钮事件，也不能产生新提交；按钮的禁用只是提示，处理函数本身同样不提交。

M4 将 Studio 图号映射到受控任务后再开放统一执行。当前不以禁用旧路径冒充 ComfyUI 连接已实现，也不声称端到端恰好一次。

已有服务端并发、旧租约拒绝和未知意图不重领由 tests/integration/test_task_api.py、test_worker_db.py 等真实 PostgreSQL 用例验证；目标 ComfyUI 的双会话并发仍需 M4 验收。
