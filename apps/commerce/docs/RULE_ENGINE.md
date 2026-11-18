# 规则判定实现状态

`domain/rules.py`定义严格规则配置与证据元数据；`domain/compliance.py`只根据固定规则快照、成品Facts和本地文件尺寸计算结果，模型不能给出最终合规结论。

配置支持最小宽高、最大字节、白底、后加文字/Logo、水印、商品长边占图片长边比例。商品自带标记与后加覆盖标记分开；未知来源或低置信度需要人工复核。已验证硬性失败优先于人工复核，未验证规则不能给出通过。缺少完整规则声明、未完成视觉分析、尺寸不一致、视觉不确定性、身份人工检查及确定性抽检都会阻止自动通过。损坏的规则配置转人工，不让Worker因配置验证异常重复执行供应商步骤。

Worker的报告保留逐项原因、规则版本与范围、成品hash及独立像素保护证明。默认没有已验证平台规则，Fake检测不提供完整事实，因此实际离线结果仍为manual_review。单元测试中的verified配置是合成测试策略，不代表任何平台现行政策。

规则证据文档要求HTTPS官方来源、时区日期和归档摘要；这些字段校验不等于已完成来源内容核验。现已新增受限运营发布命令、私有证据归档与摘要比对、只追加的规则重算与报告版本查询。verified发布还必须显式声明已人工核对证据、范围和阈值；程序不会判断归档内容是否充分支持某条平台政策。

验证：29项单元、54项真实PG/MQ/S3回归；另行复跑下载报告完整闭环，确认保留规则未验证、检测未完成原因及像素保护检查。日志见docs/evidence/rule-engine-*.log。


## 运营命令

仅专用非owner的ops_admin LOGIN可使用。通过进程环境配置ADMIN_DATABASE_URL、S3_ENDPOINT、S3_ACCESS_KEY、S3_SECRET_KEY、S3_BUCKET、可选S3_REGION；不要把凭据写入命令行或提交仓库。远程S3必须HTTPS，本机离线服务可使用127.0.0.1的HTTP。先按部署流程升级数据库至0021。

发布未验证种子示例（每次新操作自行生成UUID；重放同一操作保留原UUID、文档、理由）：

```powershell
python scripts/rules.py publish --document rules/seeds/amazon.json --operation-id <UUID> --reason "导入未验证种子"
```

发布verified文档需填官方HTTPS来源、带时区retrieved_at、实际证据文件evidence_sha256，并追加`--evidence-file <归档文件>`及`--attest-evidence-reviewed`。后者代表运营已核对所归档证据、适用范围及阈值，不是自动联网认证。证据1..20MiB，文档≤1MiB，归档进入私有S3，摘要不匹配拒绝发布。已发布版本禁止UPDATE/DELETE，同范围版本唯一；新政策使用新版本。规则发布写审计，普通API角色没有发布或直接改表权限。

```powershell
python scripts/rules.py recheck --task <UUID> --source-check <原报告UUID> --rule-id <已发布规则UUID> --operation-id <UUID> --reason "使用新版规则复核既有成品"
```

多平台为每个原范围各传一个`--rule-id`，不得缺少、重复或换平台/站点/类目/图片类型。命令校验源报告S3摘要、成品S3摘要/大小/PNG尺寸及事实一致性，复用原报告的事实、绑定成品的像素保护证明及稳定抽检标记。S3访问在数据库短事务外。生成新JSON并只追加compliance_checks和审计，不改原任务报告指针、资产像素、供应商attempt、积分或旧复核。相同operation并发/重放返回同一新报告；内容冲突拒绝。

用户可在报告弹窗切换原始/重算版本并下载所选JSON，人工复核按check_id分别展示。对应只读接口为GET /tasks/{task}/compliance/versions、GET /tasks/{task}/compliance?check_id=...、GET /tasks/{task}/report/download?check_id=...及GET /tasks/{task}/compliance/reviews?check_id=...。省略check_id始终返回原任务报告。列表按(created_at,id)游标分页；其他账号知道task/check UUID也不能读取。

## 本阶段验证

30项单元，61项真实PG/RabbitMQ/S3集成通过；Chrome生产构建3个完整用例通过，另复跑2个恢复/报告版本用例进行截图验证。覆盖并发发布/重算、唯一版本、不可改写、私有证据403、坏摘要/范围拒绝、跨用户404、原始人工复核与新报告隔离、0新增supplier intent及积分不变。测试证据中的verified规则仅为隔离合成策略，不代表现行平台政策。见rule-versions-*.log、browser-production-rule-versions*.log和browser-report-versions.png。


## 已完成重检操作的回执重放（0045候选）

规则重检先用受限ops.recheck_operation核对task/source check/规则ID顺序/operation/reason。存在已完成回执时，PG检查原审计和不可变检查记录的报告SHA/私有指针一致，随后关闭事务，再从S3读取原报告并核对SHA；直接返回原检查ID/自动结论，不重新运行当前规则引擎、不重写原对象、无模型提交和积分变动。不同参数重用operation拒绝；原文件缺失或摘要变化拒绝，不能把重新计算的新文件当旧回执。首次并发仍由原record_recheck的事务锁和唯一operation保证一条审计，当前实现升级后重放也不依赖引擎输出保持逐字相同。

新报告provider_profile仍为rule_only_recheck，source_provider_profile从已经校验的原始报告继承；多次重检继续传递原检测来源。历史重检没有该字段时保持未知，不根据当前运行环境冒填offline_fake或真实模型。质量工具分别列report_profiles与provider_profiles；source_lineage_complete仅表示来源标识已记录，不是供应商签名或独立真实性证明。合成统计要求能追溯offline来源；真实数据集的旧未知来源仅列unrecorded、保持provider_verified=false。

0046恢复边界候选：S3版本ID属于存储实例，独立恢复在核对整库/对象SHA之后只重绑该定位符，不能强求它等于原不可变审计里的旧ID。回执读取仍严格匹配原操作、桶、对象键与内容SHA，返回当前定位符和原定位符供运维核对；服务实际读取当前版本再核对原报告SHA。API/Ops账号没有直接改检查记录的权限，版本重绑仅限原有隔离恢复工具的owner流程；不修改旧审计。新增原生受控版本映射负例/权限验证，另独立冷恢复种入真实非空重检报告，恢复后重放原操作、比较原SHA、唯一审计/两报告和余额不变，综合门槛要求该新证据。
