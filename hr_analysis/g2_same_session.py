#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同一会话 4b989b29 的【线上日志】与【离线重放】直接对照。

这是关键: 之前"离线83.8 vs 线上93.9"的对比, 离线用的是 capture_*.npz,
而 capture_*.npz 存的是【生产缓冲区】的160帧(定帧数),
线上影子链路喂的却是【影子缓冲区】(按时间裁剪)重采样出的301帧。

两者是不同的帧集合 —— 这可能就是那10bpm。本脚本先用日志确认线上侧数值,
不改代码、不重启服务。
"""
import re
import numpy as np
from collections import Counter

LOG = "/home/lsz/real_time_plus/real_time_Demo/output.log.before_gate_20260810_164015"
SESS = "4b989b29"

pat = re.compile(
    r"\[SHADOW-HR-DIAG\] session=([0-9a-f]{8})\S* "
    r"独立局部[峰峭]值前5\(bpm,能量\)=\[(.*?)\] "
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
pat_tuple = re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")

# 同时抓 W6-DEBUG 里的 shadow_T / shadow_fs, 用于确认线上确实是301帧
pat_w6 = re.compile(
    r"\[SHADOW-W6-DEBUG\] session=([0-9a-f]{8})\S*.*?"
    r"shadow_fs=([0-9.]+) shadow_T=(\d+) production_hr=([0-9.]+)")

rows, w6 = [], []
with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if SESS not in line:
            continue
        if "SHADOW-HR-DIAG" in line:
            m = pat.search(line)
            if m:
                s, body, ch, ea = m.groups()
                pk = [(float(a), float(b)) for a, b in pat_tuple.findall(body)]
                if pk:
                    rows.append((pk, float(ch), float(ea)))
        elif "SHADOW-W6-DEBUG" in line:
            m = pat_w6.search(line)
            if m:
                w6.append((float(m.group(2)), int(m.group(3)), float(m.group(4))))

print("=" * 74)
print("会话 %s  线上日志实测 (被试B, 仪器真值 78.3 bpm)" % SESS)
print("=" * 74)
print("SHADOW-HR-DIAG 轮数 = %d" % len(rows))
print("SHADOW-W6-DEBUG 轮数 = %d" % len(w6))

if w6:
    fs = np.array([a for a, _, _ in w6])
    T = np.array([b for _, b, _ in w6])
    prod = np.array([c for _, _, c in w6])
    print("\n线上影子链路参数确认:")
    print("  shadow_T   取值分布 = %s" % dict(Counter(T.tolist())))
    print("  shadow_fs  取值分布 = %s" % dict(Counter(np.round(fs, 2).tolist())))
    print("  production_hr 均值 = %.2f  (真值78.3, 偏差 %+.2f)"
          % (prod.mean(), prod.mean() - 78.3))

if not rows:
    print("未解析到 SHADOW-HR-DIAG 样本")
    raise SystemExit(1)

strongest = np.array([r[0][0][0] for r in rows])
chosen = np.array([r[1] for r in rows])
TRUTH = 78.3

print("\n--- 线上: 最强峰 ---")
print("  n=%d  mean=%.2f  median=%.2f  偏差=%+.2f"
      % (strongest.size, strongest.mean(), np.median(strongest),
         strongest.mean() - TRUTH))
print("  >=105bpm 占比 = %.1f%%" % (100.0 * (strongest >= 105).mean()))

print("\n--- 线上: 追踪器选中值(即/max显示的来源) ---")
print("  n=%d  mean=%.2f  median=%.2f  偏差=%+.2f"
      % (chosen.size, chosen.mean(), np.median(chosen), chosen.mean() - TRUTH))
print("  MAE(对真值78.3) = %.2f" % np.mean(np.abs(chosen - TRUTH)))

print("\n" + "=" * 74)
print("对照: 我此前的【离线重放】结果 (同一会话的 capture_*.npz)")
print("=" * 74)
print("  离线最强峰均值 = 83.8  (偏差 +5.5)")
print("  线上最强峰均值 = %.2f  (偏差 %+.2f)" % (strongest.mean(),
                                                strongest.mean() - TRUTH))
print("  >>> 差值 = %.2f bpm" % (strongest.mean() - 83.8))
print("""
若此差值显著, 则证明: 离线重放与线上跑的【不是同一批帧】。
根本原因(待落盘验证): capture_*.npz 存的是生产缓冲(160帧定长),
而影子链路用的是影子缓冲(按时间裁剪) —— 两者在低帧率时严重分歧。
""")
