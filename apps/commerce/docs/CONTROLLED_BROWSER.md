# 生产构建故障浏览器验收

默认`pnpm --dir apps/web e2e`执行4个常规用例，包括真实MQ/Worker闭环、断线/会话/报告版本/账号切换、草稿、下载像素校验/手机以及60秒性能项。本阶段另有5个受控故障用例，使用实际API、PG、私有S3、Worker和受限运营CLI。

## 已验证行为

| 场景 | 结果 |
|---|---|
| 排队取消和重复点击/请求 | 只有一次freeze/refund，没有供应商attempt；200可用、0冻结 |
| 明确Fake供应商失败 | 一次refund、无charge；刷新不自动提交新任务，允许用户另建任务 |
| 创建意图后客户端仍显示排队 | 取消HTTP 409并解释已开始；unknown保留184/16，普通生成入口禁用；重复运行Worker不提交新意图 |
| 运营结案unknown | SSE持续订阅，页面无需刷新即显示积分释放；不改称供应商费用已退款 |
| 生成结果已保存但回复丢失 | 接纳既有回执并resume查询/归档，generate仍1次、detect 1次，只charge一次 |
| 创建任务响应丢失 | 真实请求在服务端201提交后中断客户端响应；再次确认同一key返回200/同一task，只freeze/charge一次 |

unknown不再被前端当作订阅终态；真正结束为succeeded/failed/cancelled。当前产品待对账时普通生成入口显示等待，其他产品可独立编辑；不会在unknown上增加普通重试按钮。取消/失败措辞明确指内部积分，结果提示按报告auto_overall显示。

## 执行方式

先构建并运行API与生产preview。受控阶段需要暂停**本项目的**publisher/consumer终端（Ctrl+C），避免正常消费者抢先处理专属测试任务；PG、RabbitMQ、S3和API/preview继续运行。然后在PowerShell执行：

```powershell
$env:CONTROLLED_FAKE_BROWSER='1'
pnpm --dir apps/web exec playwright test -c playwright.faults.config.ts
```

完成后恢复两个本项目进程：

```text
python scripts/run_local.py publisher
python scripts/run_local.py worker
```

该配置与普通e2e目录分开，不以skip冒充故障通过。测试预检生产哈希资源、离线profile及显式控制标记；各用例自动新建独立私有账号，提交后确认仍queued，再用单任务CLI执行故障。不要让并行普通消费者处理同一批受控任务。当前实测后已恢复本地publisher/worker。

## 离线Fake命令

`python scripts/run_fake_task.py --task UUID --mode success|failed|uncertain|late`读取忽略目录.local/runtime.json；可用--profile-dir选择忽略目录内另一个本机profile。仅允许明确offline及loopback数据库/S3。底层Worker同样只启用offline，可直接用`--task UUID --fake-mode MODE`。

- success：原有正常离线生成/检测。
- failed：保存唯一意图后返回明确供应商失败。
- uncertain：保存唯一意图后返回未知结果，无已知供应商ID。
- late：保存PNG/JSON或检测facts后丢失响应，返回unknown；人工核对已有回执后恢复查询，禁止重新generate。

故障模式只在CLI配置，quote/task HTTP契约没有此字段。没有真实模型出网或付费；供应商回执接纳和结案仍走专用ops_admin函数与审计，不直接修改任务或积分表。

## 本阶段证据

37项单元、69项真实PG/Rabbit/S3回归通过。生产Chrome常规4项、受控5项通过；后者JSON保留实际起止、命令、基础commit、浏览器/Node版本、测试文件和生产JS hash、各场景余额与断言摘要。日志为browser-fault-unit-20261113.log、browser-fault-native-full-20261113.log、browser-production-complete-20261113.log、browser-controlled-faults-20261113.log。截图browser-fault-*.png已检查。

最新性能原始样本仍完整保留：帧间隔p95 12.1ms，API p95 359.0ms/p99 583.0ms/最大818.0ms，500次均200；详见PERFORMANCE.md。单项p95目标通过不代表其他门槛完成。完整check all/task覆盖、备份恢复、CI和供应商适配仍待补齐，未标OFFLINE_READY。
