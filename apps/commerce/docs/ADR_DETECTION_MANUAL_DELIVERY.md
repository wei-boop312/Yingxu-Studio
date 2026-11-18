# 检测不可用时的受限降级交付

## 依据与状态边界

主计划§7.2规定交付最终图后结算原报价，检测降级人工复核仍算已交付；§7.3规定检测失败写manual_review且不得重发生图。工程契约§5要求未知意图不得新submit、人工结案须有审计。原实现只处理返回了无效事实的降级；检测网络结果未知会永久停在uncertain，缺少保留计费未知、交付既有成品的受限入口。

迁移0030新增明确的运营交付决定。它只针对已知生成成功、canonical最终PNG已归档、内部积分仍frozen的任务，审核原检测意图与最终SHA后，从uncertain转入本地validating_rules，或恢复已有本地报告的finalizing。不是重新确认模型成功，也不是平台合规人工批准；provider attempt保持原状态，费用记录不改成零。未知生成、无最终图、已结算/退款终态均不能从这个入口恢复。

这是按主计划补齐的具体降级分支，不改变收费单位、原报价、模型、网关或付费授权；原始规范文件不改写。生成结果未知仍保持冻结。任何真实模型验收和发布仍独立受限。

## 私有操作

使用非owner、非superuser/bypassrls的ops_admin LOGIN，在私有ADMIN_DATABASE_URL中提供连接信息：

```text
python scripts/detection_delivery.py inspect --task <uuid>
python scripts/detection_delivery.py export --task <uuid> --output .local/review-final.png
python scripts/detection_delivery.py approve --task <uuid> --attempt <detect-uuid> --final-sha256 <reviewed-final-sha> --operation-id <uuid> --reason <delivery-decision> --evidence-digest <reviewed-evidence-sha>
```

inspect不发模型请求，返回阶段、最终版本/SHA/尺寸、检测意图和已有决定；不返回密钥、签名URL或对象存储凭据。reason/evidence表示允许交付待人工复核成品的运营证据，不是已核验供应商账单，也不是确认图片合规。不要在理由里填凭据。

0031的export需要私有S3环境变量，只读取该未知检测任务的不可变最终版本。CLI只允许写入本工程已忽略的.local/work既有目录，目标必须是新PNG；有版本ID时读取并核对指定版本，未版本化桶沿用不可变对象键；两者都必须核对SHA、PNG/RGB和尺寸后，以独占创建写入私有文件。输出仅含SHA/尺寸/paid_calls=0/delivery_approved=false，不输出bucket、key、凭据或签名URL。查看导出的图片并记录审核依据后再approve；导出本身不批准、不解冻或扣积分。未知生成/无最终图/已完成任务不能导出，API/Worker没有该私有函数执行权。审阅文件不要进入Git或公开证据。

批准与决定、任务/dispatch、恢复outbox、审计和事件在同一PG语句事务里完成。金额及钱包在批准时不变；Worker随后重查不可变最终图SHA和像素保护，再保存报告并沿用原settle_task结算一次。原预算不重开，不执行generate/detect。若无法交付，可继续使用既有受限fail对账流程，不伪造供应商退款。

相同operation及所有参数精确重放仅返回原决定，不再次恢复任务或写消息。若批准后的本地步骤被恢复隔离/重新进入uncertain，需要新的operation、理由和证据，仍绑定同检测意图与最终SHA；原决定不可修改，新增审计记录区分恢复审阅。有匹配的已保存报告时只恢复finalizing。上下文显示original_operation_id和latest_review。

## 数据库与Worker约束

ops.detection_manual_deliveries具有用户/产品/任务/意图/最终版本复合FK，单任务/意图唯一，FORCE RLS，审阅记录不可更新/删除。Worker/API不能直接读写表；ops_admin只能受限审批及读取。既有函数OID/ACL保持，claim保留人工队列和租约/fencing门槛，只对有匹配决定的detect意图开放本地validate/finalize恢复；未知generate永不豁免。

Worker在每一阶段仍验证冻结供应商端点。降级分支读取PG生成的固定不完整Facts，跳过检测请求及响应费用记录，复核实际S3图像/像素并运行原规则引擎；确定性硬失败仍可non_compliant，其余人工，不能compliant。record_report核对Facts与原决定完全一致，不把未知/失败检测意图写成succeeded。正常无审批报告新增检测回执前置条件；原WorkerDb正例fixture补真实回执应用，不靠只有intent冒充检测完成。

报告JSON和API提供detection_degraded。API布尔值由租户隔离的数据库审批记录派生，不依据模型输出中的uncertainties文字；模型无法通过返回保留字冒充运营审批。界面明确提示检测没有取得可用结果；普通用户不看到运营证据或私有标识。重算规则保留降级来源。

## 验证范围

本地111单元、46离线契约、Ruff及Linux/Windows各93源码检查、生产构建通过；HTTP mock生产浏览器验证即便模型文字带审批标记，也只有数据库派生的API字段为true时显示交付告知。没有本地PG实跑。

17项新增PG/Worker/S3候选覆盖原SHA交付/未知金额保留、错误作用域、模型伪造审批文字、租户布尔权限、未知生成/终态拒绝、显式检测拒绝、权限与不可变、并发单审计、outbox失败整体回滚、伪造Facts/通过拒绝、S3报告中断、结算前重启、意图后发网前灰区、受控隔离后的新审阅、结案竞争及迟到回执。另有WorkerDb缺检测回执不能保存报告负例；补入P2-04/P2-06映射。受控隔离分支不冒充完整冷恢复，供应商HTTP始终MockTransport；新迁移/原生/独立恢复需当次CI后才能称通过。

当前候选补充：eb87069的迁移已实跑，142原生中141通过，失败仅为并发测试仍匹配旧格式去重键，已于fc603c2同步准确预期。0031另有3项导出原生测试；受控浏览器增加第7项，审批前经真实CLI导出并核对PNG SHA，交付后核对报告告知和一次结算。冷恢复增加已审阅但未结算的检测未知任务，要求原审批重放不能越过隔离，新操作复审后仅本地交付；这些浏览器/独立冷恢复均须新CI实跑，不以前一基线推断。

最终整轮证据：db7b8b1 / CI43482913577成功，111单元/46离线契约/145原生、4+7真实浏览器、完整Compose、独立冷恢复/重启与P5-02通过。覆盖上述候选的迁移、实际私有PNG审阅、两处告知和恢复后新审批；已归档原JSON与来源SHA。供应商仍Mock/Fake，不代表真实模型或账单已核实。
