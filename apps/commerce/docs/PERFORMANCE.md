# 本机生产构建性能基准

记录命令：`python scripts/check.py e2e`，性能项结果通过。起止时间2026-11-12T23:23:10.733Z至2026-11-12T23:24:20.595Z，应用基础commit `f4aea8aa7afb7b54024a73e8662a4388dc8642b8`。dist文件/hash、全部原始样本和设备记录见[evidence/performance-production.json](evidence/performance-production.json)，运行日志为check-e2e-current-20261113.log。

## 环境与负载

13th Gen Intel(R) Core(TM) i7-13620H，16逻辑CPU、31.7GiB RAM，Windows 10.0.26200，Chrome 157.0.8103.50 headless，1920×1200、DPR 1。本机PG/RabbitMQ/S3、API及静态生产预览均已启动。API与画布阶段分开测量。

通过真实API上传2000×2000合成PNG，普通界面鼠标在缩放/平移后的原图创建12个ROI。50个绘制对象为双画布2个Image、24个Rect和24个Text，不计Stage/Layer/Group，也不是50个独立保护区域（业务最多20区域）。Konva仅用于只读计数/坐标/事件观测，没有通过store注入区域。

连续操作60.452秒，4528个输入，最大输入间隔143.1ms。实际矩形最大位移123.7 canonical像素、画布组位移25.1显示像素；缩放范围0.1522–0.2577。验证源坐标未改变，目标宽高不变、坐标为整数。

## 结果

| 指标 | p50 | p95 | p99 | 最大 | 目标 |
|---|---:|---:|---:|---:|---|
| RAF帧间隔（9110样本） | 6.1ms | 12.1ms | 18.2ms | 55.1ms | p95<33ms |
| Layer场景绘制CPU（4203样本） | 0.4ms | 1.2ms | 1.8ms | 4.3ms | 单独诊断 |
| 非模型API（500样本） | 129.6ms | 201.5ms | 792.5ms | 1003.8ms | p95<500ms |

API为同一隔离账号、20并发、500次已登录混合GET（产品列表/详情、任务、余额、规则、当前用户），包含HTTP代理、认证、PG查询及读完响应体。状态分布：`{"200": 500}`。非200、5xx、超时和网络失败均留在分位数总体并使验收失败；不排除高延迟尾部。文件传输和模型等待另行衡量。

CDP JSHeapUsedSize从12.73MiB变为38.10MiB，JSHeapTotalSize从48.63MiB变为84.13MiB；DOM Nodes从1204至1212。探针保留原始数组且没有强制GC，单次堆差额不能判定是否泄漏。长任务记录：`[{"start_ms": 190.39999997615814, "duration_ms": 52}, {"start_ms": 56176.09999990463, "duration_ms": 51}]`。

## 解释边界与复跑

RAF是回调间隔/主线程停顿；beforeDraw至draw只计Konva场景绘制CPU，不代表完整GPU呈现、物理显示延迟或浏览器进程RSS。本机预热、单账号只读负载不能外推公网、冷启动、多租户写入或其他设备容量。合成图不是商品质量样本。

启动API/publisher/worker；构建web后停止开发web，改用`python scripts/run_local.py preview`。单项命令`pnpm --dir apps/web exec playwright test performance.spec.ts`，完整常规浏览器命令`pnpm --dir apps/web e2e`。测试核对哈希静态资源及无Vite开发客户端，自动创建本机离线账号。复跑更新同名证据，完成后用本脚本按实际命令刷新摘要。完整OFFLINE_READY仍需其他P0–P3门槛。
