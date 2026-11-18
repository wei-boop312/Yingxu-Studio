# 已审阅供应商预算的原子预留候选

预算保存的是USD授权上限，**不是供应商账单，也不是用户积分**。金额以精确十进制处理；已知回执不等于已核验费用，`actual_spending_verified`保持false。当前仅接受`offline_protocol_mock`报价，真实传输、价表换算和一次性付费运行尚未接线；默认Worker仍只有offline Fake。开发验证不调用真实模型。

授权配置升级为schema_version=2，必填用户、产品、标准化资产版本及完整报价快照SHA。快照SHA来自受限管理员的`review-quote`，对应`input_sha256`，包含固定规则/外观/计价/模型信息；与普通API返回的业务`input_hash`不是同一个值。源图SHA、生成参数和目标也逐项匹配。旧v1配置不得自动获得这些新权限。

## 操作入口

通过私有`ADMIN_DATABASE_URL`配置非owner、非超级用户的ops_admin LOGIN；不将连接串、密钥或实际用户审阅报告提交Git。只读审阅受信任离线协议演练已创建的报价：

```text
python scripts/provider_budget.py review-quote --user <uuid> --quote <uuid>
python scripts/provider_smoke.py --config <private-config> --preflight
python scripts/provider_budget.py register --config <private-config> --quote <uuid> --reviewed-sha256 <hash> --operation-id <uuid> --reason <review-reason>
python scripts/provider_budget.py inspect --authorization <uuid>
```

审阅配置摘要必须精确相同；配置本身不能替代数据库角色和操作审阅。注册必须发生在任务创建前，锁定未消费报价，避免发布/消费队列先于预算绑定。完全相同operation重放返回原记录；改上限/快照/身份不能复用该操作或覆盖原记录。该CLI仅登记/检查，不创建任务或执行模型。

## 数据库边界

迁移0027新增`ops.provider_budgets`和`ops.provider_budget_holds`，两表FORCE RLS。普通API和Worker无原始表读取/写入权限；ops_admin只读表并执行受限登记/审阅函数，不能直接改上限。函数固定search_path、NOLOGIN执行所有者、撤销PUBLIC EXECUTE；授权元数据和预留金额不可变。

沿用同一个`app.record_intent`函数OID，在其事务内预留；失败同时回滚调用意图和预留，客户端不能调用未受预算约束的旧辅助函数。报价/用户/快照绑定、UTC有效期、每步最多1次、步骤上限及合计上限由PG再次检查；同预算行锁和唯一约束处理并发。金额不是float，SQL还独立拒绝字符串调用次数、布尔、额外密钥字段或不足的合计额度。

回执落库将预留标为known，金额仍保留最大授权值；未知/失败阻断授权，成功/取消关闭。状态不自动重开、额度不静默扩大或退款。预算拒绝的当前步骤没有新POST，意图/预留回滚；此前步骤可能已经有费用，相关记录仍保留，不能据此宣称整任务未花钱。内部积分释放按已有任务事务处理。

## 备份恢复

恢复先核验原始DB/函数指纹，再阻断所有open预算，包括尚无任务的未消费报价——它们可能在快照之后已发生费用，不能恢复旧授权后重新提交。报告记录撤销数量。独立冷恢复候选保留一个这样的授权，恢复后实际尝试新意图必须被拒，未留下预留且没有供应商调用；探针任务按无意图路径释放内部积分。原P5-02/all门槛增加该安全验证。

## 验证范围

新增7项非owner LOGIN的真实PG/Worker/S3候选：完整协议mock两次调用及两个精确上限、超时不重发、过期前置拒绝/无意图/退积分、并发唯一预留、操作重放/拒绝扩额、错误绑定及权限、绕过Python的SQL类型/额度/秘密字段负例。通用协议测试fixture已分离复用。单元另检验已知预算拒绝不触发传输或错误退款，以及v2身份/快照绑定。完整原生及新增冷恢复验证必须经新CI，不能以离线迁移SQL生成或本地单元冒充通过。

本候选没有证明供应商实际收费不会超出上限，也没有核验真实账单。真实价格来源、费用换算/超额处理、live运行接线及未知后运营审批仍未闭合；P4-03不得标PROVIDER_VERIFIED。


## 未知生成的显式复核候选（0028）

预算不能自动重开。对`offline_protocol_mock`生成响应丢失，只能先通过既有admin accept-receipt登记实际运营审阅的原attempt回执，再做独立预算复核：

```text
python scripts/provider_budget.py review-blocked --authorization <uuid> --receipt <uuid> --reviewed-sha256 <original-scope-sha> --operation-id <uuid> --reason <review-reason> --evidence-digest <accepted-receipt-evidence-sha>
```

函数要求任务仍uncertain/积分frozen，只有一条generate的uncertain预留，无detect预留；回执须属于同用户/任务/attempt，并已有accept_receipt审计及相同证据摘要。授权SHA、快照、金额、调用次数与到期时间不能改变；过期不能延期。只将预算置open，保持原uncertain预留；任务本身仍unknown，尚不能执行。接着经既有admin reconcile resume恢复同attempt的polling，才允许后续首次detect预留。整个流程没有再次generate POST。独立操作可能中断；中断后保持未知预留，不静默认为未收费。

本入口不能恢复已检测未知的预算，不能重开成功/失败/取消或无任务的预算。任何restore_quarantine历史审计存在时拒绝旧授权重开，避免恢复旧快照后重复花费；不能靠修改当前error_code绕过。若要处理恢复后的真实账单或新授权，仍需独立设计，不做自动解锁。

6项新原生候选覆盖：运营审阅→同attempt恢复→最终归档且BFL1/Gemini1；错误SHA/回执/证据拒绝；四并发同operation单审计；过期拒绝且保留冻结；受控restore审计负例；Worker/API无权限以及queued/closed拒绝。受控审计负例只验证SQL分支，不称新冷恢复演练。新候选需当次CI，不用原1606b2d绿灯代替。该基线已经真实通过0027七项预算原生及独立冷恢复撤销验证。

证据摘要表示运营确认的外部资料关联，不自动证明供应商实际账单正确；`actual_spending_verified`仍false。真实账单换算、超额处理、live最小运行及检测未知恢复仍待闭合。
