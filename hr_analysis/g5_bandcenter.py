#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【无重放·直接测量】追踪器状态 est 是否被搜索带中心吸引?

天然实验: 2026-08-10 提交 61828bc 把影子链路搜索带
    [58,180] (中心 119.0)  ->  [50,140] (中心 95.0)
两段日志分别记录了改动前后的 shadow_track_est_after_pick。

预测(若存在"带中心吸引"):
    改动前 est 应聚集在 119 附近, 改动后聚集在 95 附近,
    且 est 均值的移动量 ≈ 带中心的移动量 (-24)。

⚠ 混杂因素: 同一提交还把窗口 6s->10s。本检验无法分离两者,
   必须如实说明, 不能单独归因于带中心。

本脚本纯读日志, 不改代码、不重启服务。
"""
import re
import os
import numpy as np

BASE = "/home/lsz/real_time_plus/real_time_Demo"
CFG = [
    ("改动前 band[58,180] win6s", "output.log.before_bandfix_20260810_131452", 119.0),
    ("改动后 band[50,140] win10s", "output.log.before_gate_20260810_164015", 95.0),
    ("门控后 band[50,140] win10s", "output.log", 95.0),
]

pat = re.compile(
    r"\[SHADOW-HR-DIAG\] session=([0-9a-f]{8})\S*.*?"
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
pat_peak0 = re.compile(r"前5\(bpm,能量\)=\[\(([0-9.]+),")

print("=" * 88)
print("追踪器状态 est 与搜索带中心的关系  (直接读日志, 无重放)")
print("=" * 88)
print("%-30s %7s %9s %9s %9s %9s" %
      ("配置", "n", "est均值", "带中心", "est-中心", "最强峰均值"))
print("-" * 88)

store = {}
for label, fn, center in CFG:
    path = os.path.join(BASE, fn)
    if not os.path.exists(path):
        print("%-30s  文件不存在, 跳过" % label)
        continue
    est_l, ch_l, pk_l = [], [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "SHADOW-HR-DIAG" not in line:
                continue
            m = pat.search(line)
            if not m:
                continue
            ch_l.append(float(m.group(2)))
            est_l.append(float(m.group(3)))
            mp = pat_peak0.search(line)
            pk_l.append(float(mp.group(1)) if mp else np.nan)
    if not est_l:
        print("%-30s  无样本" % label)
        continue
    est = np.array(est_l)
    ch = np.array(ch_l)
    pk = np.array(pk_l)
    store[label] = (est, ch, pk, center)
    print("%-30s %7d %9.2f %9.1f %+9.2f %9.2f"
          % (label, est.size, est.mean(), center, est.mean() - center,
             np.nanmean(pk)))

print("\n" + "=" * 88)
print("带中心移动 vs est 移动")
print("=" * 88)
keys = list(store.keys())
if len(keys) >= 2:
    a, b = keys[0], keys[1]
    ea, _, pa, ca = store[a]
    eb, _, pb, cb = store[b]
    print("  带中心:  %.1f -> %.1f   (移动 %+.1f)" % (ca, cb, cb - ca))
    print("  est均值: %.2f -> %.2f  (移动 %+.2f)" % (ea.mean(), eb.mean(),
                                                     eb.mean() - ea.mean()))
    print("  最强峰:  %.2f -> %.2f  (移动 %+.2f)" % (np.nanmean(pa), np.nanmean(pb),
                                                     np.nanmean(pb) - np.nanmean(pa)))
    ratio = (eb.mean() - ea.mean()) / (cb - ca) if (cb - ca) != 0 else float('nan')
    print("\n  est移动 / 带中心移动 = %.2f" % ratio)
    print("  (=1.00 表示 est 完全跟随带中心; =0 表示与带中心无关)")

print("\n" + "=" * 88)
print("est 相对带中心的分布形状 (是否'贴着'中心)")
print("=" * 88)
for label, (est, ch, pk, center) in store.items():
    rel = est - center
    print("\n%s   (中心=%.0f, n=%d)" % (label, center, est.size))
    print("   est  分位数 p5/p25/p50/p75/p95 = %.1f / %.1f / %.1f / %.1f / %.1f"
          % tuple(np.percentile(est, [5, 25, 50, 75, 95])))
    print("   est-中心  均值=%+.2f  中位数=%+.2f  |est-中心|均值=%.2f"
          % (rel.mean(), np.median(rel), np.abs(rel).mean()))
    print("   est 落在 中心±15 内的比例 = %.1f%%" % (100.0 * (np.abs(rel) <= 15).mean()))
