"""Render the recorded metrics without rerunning or filtering their samples."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--command", default="pnpm --dir apps/web exec playwright test performance.spec.ts")
p.add_argument("--log", default="performance-production-20261113.log")
args = p.parse_args()
e = json.loads((ROOT / "docs/evidence/performance-production.json").read_text())
c = e["canvas"]
a = e["api"]
env = e["environment"]
f = c["frame_interval"]


def heap(values, name):
    return next(x["value"] for x in values if x["name"] == name)


def row(name, s, target):
    return f"| {name}（{s['count']}样本） | {s['p50_ms']:.1f}ms | {s['p95_ms']:.1f}ms | {s['p99_ms']:.1f}ms | {s['max_ms']:.1f}ms | {target} |"


text = f"""# 本机生产构建性能基准

记录命令：`{args.command}`，性能项结果{"通过" if e["passed"] else "未通过"}。起止时间{e["started_at"]}至{e["finished_at"]}，应用基础commit `{e["base_commit"]}`。dist文件/hash、全部原始样本和设备记录见[evidence/performance-production.json](evidence/performance-production.json)，运行日志为{args.log}。

## 环境与负载

{env["cpu"]}，{env["logical_cpu_count"]}逻辑CPU、{env["total_memory_bytes"] / 1024**3:.1f}GiB RAM，Windows {env["release"]}，Chrome {env["browser"]} headless，1920×1200、DPR {env["device_pixel_ratio"]}。本机PG/RabbitMQ/S3、API及静态生产预览均已启动。API与画布阶段分开测量。

通过真实API上传2000×2000合成PNG，普通界面鼠标在缩放/平移后的原图创建12个ROI。50个绘制对象为双画布2个Image、24个Rect和24个Text，不计Stage/Layer/Group，也不是50个独立保护区域（业务最多20区域）。Konva仅用于只读计数/坐标/事件观测，没有通过store注入区域。

连续操作{c["duration_ms"] / 1000:.3f}秒，{c["event_count"]}个输入，最大输入间隔{c["max_input_gap_ms"]:.1f}ms。实际矩形最大位移{c["observed_drag"]["Rect"]["max_displacement"]:.1f} canonical像素、画布组位移{c["observed_drag"]["Group"]["max_displacement"]:.1f}显示像素；缩放范围{c["observed_scale"]["min"]:.4f}–{c["observed_scale"]["max"]:.4f}。验证源坐标未改变，目标宽高不变、坐标为整数。

## 结果

| 指标 | p50 | p95 | p99 | 最大 | 目标 |
|---|---:|---:|---:|---:|---|
{row("RAF帧间隔", f, "p95<33ms")}
{row("Layer场景绘制CPU", c["draw_cpu"], "单独诊断")}
{row("非模型API", a["latency"], "p95<500ms")}

API为同一隔离账号、20并发、500次已登录混合GET（产品列表/详情、任务、余额、规则、当前用户），包含HTTP代理、认证、PG查询及读完响应体。状态分布：`{json.dumps(a["status_counts"], ensure_ascii=False)}`。非200、5xx、超时和网络失败均留在分位数总体并使验收失败；不排除高延迟尾部。文件传输和模型等待另行衡量。

CDP JSHeapUsedSize从{heap(c["memory_before"], "JSHeapUsedSize") / 1024**2:.2f}MiB变为{heap(c["memory_after"], "JSHeapUsedSize") / 1024**2:.2f}MiB，JSHeapTotalSize从{heap(c["memory_before"], "JSHeapTotalSize") / 1024**2:.2f}MiB变为{heap(c["memory_after"], "JSHeapTotalSize") / 1024**2:.2f}MiB；DOM Nodes从{heap(c["memory_before"], "Nodes")}至{heap(c["memory_after"], "Nodes")}。探针保留原始数组且没有强制GC，单次堆差额不能判定是否泄漏。长任务记录：`{json.dumps(c["long_tasks"], ensure_ascii=False)}`。

## 解释边界与复跑

RAF是回调间隔/主线程停顿；beforeDraw至draw只计Konva场景绘制CPU，不代表完整GPU呈现、物理显示延迟或浏览器进程RSS。本机预热、单账号只读负载不能外推公网、冷启动、多租户写入或其他设备容量。合成图不是商品质量样本。

启动API/publisher/worker；构建web后停止开发web，改用`python scripts/run_local.py preview`。单项命令`pnpm --dir apps/web exec playwright test performance.spec.ts`，完整常规浏览器命令`pnpm --dir apps/web e2e`。测试核对哈希静态资源及无Vite开发客户端，自动创建本机离线账号。复跑更新同名证据，完成后用本脚本按实际命令刷新摘要。完整OFFLINE_READY仍需其他P0–P3门槛。
"""
(ROOT / "docs/PERFORMANCE.md").write_text(text)
