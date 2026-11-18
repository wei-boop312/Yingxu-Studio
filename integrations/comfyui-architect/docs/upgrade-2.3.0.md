# 2.3.0安全升级与范围

本版新增电商研究资料库按钮、只读GET资料接口、离线有界批量规划和本机0.27兼容回归。没有增加自动推理队列、模型下载器或商用许可。批量规划CLI不等同批量生图功能。

升级前确认队列为空，将整个现有插件备份到custom_nodes之外，并记录替换文件SHA256。只替换本版运行文件，保留用户example_workflows、配置、其他节点、模型与历史；不删除旧文件腾空间。升级后需要重启Python加载新route，再刷新前端。未保存工作流时不代用户刷新/重启；先由用户保存并重启。

检查：/workflow-architect/health应返回2.3.0；/workflow-architect/research应返回execution_authorized=false及17项不可执行配方；点击资料按钮只读筛选，无graph或queue变化。新UI未通过真实浏览器验收前不能称安装验收完成。

回退须先停服务并保存工作流，再按备份清单恢复被替换文件。不要将备份目录置于custom_nodes导致重复导入，不使用删除整个节点目录或git reset --hard。
