#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 output.log 直接挖掘线上影子链路的【最强峰】分布。

背景: 离线重放 capture_*.npz 得到最强峰均值 83.8, 线上实测 93.9, 差 10bpm。
此前一直归因于"影子链路真实输入从未落盘"。但 SHADOW-HR-DIAG 本来就把
线上每一轮的前5个局部峰(bpm,能量)打进了日志 —— 这就是线上真实频谱本身。

本脚本纯读日志, 不改任何代码、不重启服务。
"""
import re
import sys
import numpy as np

LOG = "/home/lsz/real_time_plus/real_time_Demo/output.log"

pat_peaks = re.compile(
    r"\[SHADOW-HR-DIAG\] session=([0-9a-f]{8})\S* "
    r"独立局部峰值前5\(bpm,能量\)=\[(.*?)\] "
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
# 日志里"峰"字可能是"峭"(OCR/编码差异), 两种都试
pat_peaks_alt = re.compile(
    r"\[SHADOW-HR-DIAG\] session=([0-9a-f]{8})\S* "
    r"独立局部峭值前5\(bpm,能量\)=\[(.*?)\] "
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
pat_tuple = re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")

rows = []
with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if "SHADOW-HR-DIAG" not in line:
            continue
        m = pat_peaks.search(line) or pat_peaks_alt.search(line)
        if not m:
            continue
        sess, body, chosen, est_after = m.groups()
        peaks = [(float(a), float(b)) for a, b in pat_tuple.findall(body)]
        if not peaks:
            continue
        rows.append({
            "sess": sess,
            "peaks": peaks,
            "chosen": float(chosen),
            "est_after": float(est_after),
        })

print("=" * 74)
print("线上 SHADOW-HR-DIAG 日志挖掘")
print("=" * 74)
print("解析到样本数: %d" % len(rows))
if not rows:
    print("未解析到任何样本, 检查正则/日志格式")
    sys.exit(1)

from collections import Counter
cnt = Counter(r["sess"] for r in rows)
print("会话分布:")
for s, c in cnt.most_common():
    print("   %s : %d 轮" % (s, c))

strongest = np.array([r["peaks"][0][0] for r in rows])
chosen = np.array([r["chosen"] for r in rows])

print("\n--- 最强峰(peaks[0]) 统计 ---")
print("  n      = %d" % strongest.size)
print("  mean   = %.2f bpm" % strongest.mean())
print("  median = %.2f bpm" % np.median(strongest))
print("  std    = %.2f" % strongest.std(ddof=1))
print("  范围   = %.1f ~ %.1f" % (strongest.min(), strongest.max()))
print("  >=105bpm 占比 = %.1f%%" % (100.0 * (strongest >= 105).mean()))

print("\n--- 追踪器最终选中的 chosen_shadow_hr 统计 ---")
print("  mean   = %.2f bpm" % chosen.mean())
print("  median = %.2f bpm" % np.median(chosen))
print("  范围   = %.1f ~ %.1f" % (chosen.min(), chosen.max()))

diff = chosen - strongest
print("\n--- chosen - strongest ---")
print("  相同(|d|<0.05) 占比 = %.1f%%" % (100.0 * (np.abs(diff) < 0.05).mean()))
print("  chosen 更低 占比    = %.1f%% (追踪器把结果往下拉)"
      % (100.0 * (diff < -0.05).mean()))
print("  chosen 更高 占比    = %.1f%%" % (100.0 * (diff > 0.05).mean()))
print("  平均修正量          = %+.2f bpm" % diff.mean())

# 分会话统计: 不同会话=不同被试/不同时段
print("\n--- 按会话分别统计 ---")
print("  %-10s %6s %10s %10s %10s" % ("session", "n", "最强峰均值", "chosen均值", "修正量"))
for s, c in cnt.most_common():
    idx = [i for i, r in enumerate(rows) if r["sess"] == s]
    st = strongest[idx]
    ch = chosen[idx]
    print("  %-10s %6d %10.2f %10.2f %+10.2f"
          % (s, len(idx), st.mean(), ch.mean(), (ch - st).mean()))

np.save("/home/lsz/webapp/hr_analysis/online_strongest.npy", strongest)
np.save("/home/lsz/webapp/hr_analysis/online_chosen.npy", chosen)
print("\n已缓存 online_strongest.npy / online_chosen.npy")
