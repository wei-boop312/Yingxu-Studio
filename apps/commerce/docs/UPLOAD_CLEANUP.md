# 过期上传临时对象回收

上传PUT签名有效15分钟，数据库意图也在15分钟后过期。本流程另等24小时宽限，并要求没有有效验证租约，才允许受限Ops管理员回收该意图唯一的`temp/<user UUID>/<intent UUID>`。它不删除最终图、原图归档、报告、规则证据、账单证据或没有意图记录的任意对象；其他私有孤儿对象仍需独立审阅，不能前缀批量删除。

默认只读计划（非owner的ops_admin LOGIN，`OPS_ADMIN_DATABASE_URL`来自私有环境）：

```sh
python scripts/cleanup_uploads.py --user <user-uuid>
```

返回最多100个符合范围的意图ID/状态/过期时间和临时键SHA，不输出对象键或签名URL。逐个执行明确的目标和稳定操作ID：

```sh
python scripts/cleanup_uploads.py --user <user-uuid> --intent <intent-uuid> --operation-id <uuid> --reason <运维原因> --apply
```

需要原有S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET/S3_REGION私有环境变量。当前没有自动调度或生产执行；真实测试只处理GitHub CI自建实例内的合成对象。

PG领取先锁意图，再检查年龄、所属用户、精确键、活动验证/回收租约及归档引用。未完成意图原子改为rejected，清空旧验证租约，防止同时完成；ready意图和原/规范化版本不变。领取审计和状态是同一事务，审计失败时不能开始删除。唯一意图/操作记录防止重复领取，5分钟租约及递增attempt阻止过期执行者写入完成结果；失败必须等待租约后用原operation-id和原reason重试，不扩大目标。

存储先完整枚举精确键的版本和删除标记，再逐版本删除；相同前缀的其他键忽略。一次最多1000版本/10页，缺版本ID、重复版本或坏分页均在删除前停止。最后重新枚举并实际HEAD确认当前对象不存在，才将完成回执与审计同事务保存。版本API不支持或隐藏了仍存在的当前对象时明确失败，不把删除标记当空间已回收。S3删除后PG写入失败可能保留pending记录，原操作重试以对象已不存在安全完成；removed_versions仅是最后一次成功尝试删除的数量，不是跨失败尝试累计空间或字节。S3访问本身可能有平台费用，paid_calls=0仅表示没有模型请求。

该流程不改用户积分、任务/供应商意图或供应商预算。Ops可读回收记录，API/Worker/readonly无执行权限，不开放普通用户删除接口。备份沿用所有业务表的指纹/数据保存，未新增“真实定期回收已部署”或非空回收记录冷恢复的验收声明。
