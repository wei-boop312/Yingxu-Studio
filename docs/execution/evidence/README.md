# 本轮证据索引

所有报告以自身 scope、时间、提交和工作区标记为准。合成样例不是实际商品/模型质量证据；历史基线和当前结果分别保留。

| 文件 | 说明 |
| --- | --- |
| baseline/ | 清洁导入提交的实际运行基线，保留缺依赖/失败 |
| environment-inventory.json | 本轮隔离依赖、服务及实际 Desktop 只读诊断 |
| architect-current.json | 364 Python + Node 模拟交互；工作区修复后，不是宿主执行 |
| studio-native-current.json | 9 个真实 PG/S3 接线用例 |
| studio-native-incremental.json | 扩展到 11 个用例，0 跳过，含增量交付及图号归档/恢复 |
| browser/ | 合成商品的真实浏览器截图和下载样例；只有对应浏览器通过报告才可计入关口 |

原始服务日志和账户配置留在被忽略的 .local/work，不能随公开证据打包。最终提交复验及关口汇总仍在进行。
