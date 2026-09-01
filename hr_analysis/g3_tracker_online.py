#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线上日志: 追踪器 vs 最强峰, 严格【逐样本配对】比较。

g2 发现会话 4b989b29:
    最强峰均值 86.79 (+8.49)
    追踪器选中 95.25 (+16.95)
追踪器把结果又推高了 8.5bpm。这与我此前离线得出的"追踪器≈最强峰"结论冲突。

关键区别(自查): 离线重放只有 ~120 个间隔8秒的稀疏样本, 追踪器状态只演化120步;
线上一个会话有 8450 轮连续更新, alpha=0.25 的 EMA 会形成很强的自我强化。
=> 离线根本没有复现出线上的追踪器状态轨迹。

本脚本只读日志。
"""
import re
import numpy as np

LOG = "/home/lsz/real_time_plus/real_time_Demo/output.log.before_gate_20260810_164015"
SESS = "4b989b29"
TRUTH = 78.3

pat = re.compile(
    r"独立局部[峰峭]值前5\(bpm,能量\)=\[(.*?)\] "
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
pat_tuple = re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")

peaks_list, chosen_l, est_after_l = [], [], []
with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if SESS not in line or "SHADOW-HR-DIAG" not in line:
            continue
        m = pat.search(line)
        if not m:
            continue
        pk = [(float(a), float(b)) for a, b in pat_tuple.findall(m.group(1))]
        if not pk:
            continue
        peaks_list.append(pk)
        chosen_l.append(float(m.group(2)))
        est_after_l.append(float(m.group(3)))

chosen = np.array(chosen_l)
est_after = np.array(est_after_l)
strongest = np.array([p[0][0] for p in peaks_list])
n = chosen.size

print("=" * 74)
print("会话 %s  逐样本配对分析  n=%d  真值=%.1f" % (SESS, n, TRUTH))
print("=" * 74)

d = chosen - strongest          # 逐样本配对差(与均值差恒等, 但保留分布)
print("\n[1] 追踪器相对最强峰的逐样本修正量 (chosen - strongest)")
print("    mean=%+.2f  median=%+.2f  std=%.2f" % (d.mean(), np.median(d), d.std(ddof=1)))
print("    往上推 (>+0.05): %.1f%%" % (100.0 * (d > 0.05).mean()))
print("    不变   (|d|<=0.05): %.1f%%" % (100.0 * (np.abs(d) <= 0.05).mean()))
print("    往下拉 (<-0.05): %.1f%%" % (100.0 * (d < -0.05).mean()))

# 配对误差比较
e_str = np.abs(strongest - TRUTH)
e_trk = np.abs(chosen - TRUTH)
win_str = int((e_str < e_trk - 1e-9).sum())
win_trk = int((e_trk < e_str - 1e-9).sum())
tie = n - win_str - win_trk
print("\n[2] 逐样本绝对误差配对 (谁更接近真值)")
print("    最强峰 MAE = %.2f   |  追踪器 MAE = %.2f" % (e_str.mean(), e_trk.mean()))
print("    最强峰更准: %d 次 (%.1f%%)" % (win_str, 100.0 * win_str / n))
print("    追踪器更准: %d 次 (%.1f%%)" % (win_trk, 100.0 * win_trk / n))
print("    平局      : %d 次 (%.1f%%)" % (tie, 100.0 * tie / n))

dd = e_trk - e_str    # >0 表示追踪器更差
sd = dd.std(ddof=1)
t = dd.mean() / (sd / np.sqrt(n)) if sd > 0 else float("nan")
print("\n[3] 配对 t 检验 (H0: 两者误差相同)")
print("    平均误差差 (追踪器 - 最强峰) = %+.3f bpm" % dd.mean())
print("    t = %+.2f   (|t|>2 即显著)" % t)
if t > 2:
    print("    >>> 结论: 追踪器【显著更差】")
elif t < -2:
    print("    >>> 结论: 追踪器【显著更好】")
else:
    print("    >>> 结论: 无显著差异")

print("\n[4] 追踪器状态 est 的轨迹 (自我强化检验)")
print("    est 范围 = %.1f ~ %.1f" % (est_after.min(), est_after.max()))
print("    est 均值 = %.2f  (真值 %.1f, 偏差 %+.2f)"
      % (est_after.mean(), TRUTH, est_after.mean() - TRUTH))
q = n // 4
for i in range(4):
    seg = est_after[i * q:(i + 1) * q]
    print("    第%d四分位: est均值=%.2f  最强峰均值=%.2f"
          % (i + 1, seg.mean(), strongest[i * q:(i + 1) * q].mean()))

# est 高时是否更倾向选高峰
print("\n[5] est 高低分组: 追踪器偏移方向是否随 est 同向")
med = np.median(est_after)
lo, hi = est_after <= med, est_after > med
print("    est<=%.1f (n=%d): chosen-strongest = %+.2f" % (med, lo.sum(), d[lo].mean()))
print("    est> %.1f (n=%d): chosen-strongest = %+.2f" % (med, hi.sum(), d[hi].mean()))

# 反事实: 如果直接用最强峰, /max 会显示什么
print("\n[6] 反事实估计 (若直接用最强峰替代追踪器)")
print("    当前线上显示均值 = %.2f  (偏差 %+.2f, MAE %.2f)"
      % (chosen.mean(), chosen.mean() - TRUTH, e_trk.mean()))
print("    改用最强峰后     = %.2f  (偏差 %+.2f, MAE %.2f)"
      % (strongest.mean(), strongest.mean() - TRUTH, e_str.mean()))
print("    偏高可减少约 %.2f bpm" % (chosen.mean() - strongest.mean()))
