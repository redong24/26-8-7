#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【无重放】改进空间的上下界估计。

g4 的重放保真度只有37.9%(日志只存前5峰), 所以我【不再】用重放给结论。
改用两个不依赖重放的量:

  上界(oracle): 每轮从日志所存候选中挑最接近真值的那个
                -> 信号本身的能力天花板
  下界(能量):   每轮直接取能量最强峰(这是日志里如实记录的, 无需重放)
                -> 完全丢弃追踪的保守方案

现状(chosen)也是日志如实记录的。三者对比即可界定改进空间, 全部无重放。
"""
import re
import numpy as np

BASE = "/home/lsz/real_time_plus/real_time_Demo/"
# (标签, 日志, 会话, 仪器真值)
CASES = [
    ("被试B 4b989b29", "output.log.before_gate_20260810_164015", "4b989b29", 78.3),
]

pat = re.compile(
    r"\[SHADOW-HR-DIAG\] session=([0-9a-f]{8})\S* "
    r"独立局部[峰峭]值前5\(bpm,能量\)=\[(.*?)\] "
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
pt = re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")

for label, fn, sess, truth in CASES:
    cand, chosen_l = [], []
    with open(BASE + fn, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if sess not in line or "SHADOW-HR-DIAG" not in line:
                continue
            m = pat.search(line)
            if not m:
                continue
            pk = [(float(a), float(b)) for a, b in pt.findall(m.group(2))]
            if pk:
                cand.append(pk)
                chosen_l.append(float(m.group(3)))
    chosen = np.array(chosen_l)
    n = len(cand)
    strongest = np.array([p[0][0] for p in cand])
    oracle = np.array([min(p, key=lambda x: abs(x[0] - truth))[0] for p in cand])

    print("=" * 84)
    print("%s   n=%d   仪器真值=%.1f bpm" % (label, n, truth))
    print("=" * 84)
    print("%-34s %8s %8s %8s %8s %8s" %
          ("方案", "均值", "偏差", "MAE", "±5bpm", "±10bpm"))
    print("-" * 84)
    for nm, v in [("现状: 追踪器选峰(日志实录)", chosen),
                  ("下界: 能量最强峰(日志实录)", strongest),
                  ("上界: oracle最接近真值", oracle)]:
        e = np.abs(v - truth)
        print("%-34s %8.2f %+8.2f %8.2f %7.1f%% %7.1f%%"
              % (nm, v.mean(), v.mean() - truth, e.mean(),
                 100.0 * (e <= 5).mean(), 100.0 * (e <= 10).mean()))

    print("\n可解释的改进空间:")
    e_now = np.abs(chosen - truth).mean()
    e_str = np.abs(strongest - truth).mean()
    e_or = np.abs(oracle - truth).mean()
    print("  现状 MAE %.2f -> oracle %.2f, 理论最多可降 %.2f bpm" % (e_now, e_or, e_now - e_or))
    print("  仅换成能量最强峰: MAE %.2f (几乎不变), 但偏差 %+.2f -> %+.2f"
          % (e_str, chosen.mean() - truth, strongest.mean() - truth))
    print("  >>> 关键: 单纯'换选峰规则'只能修好【偏高】, 修不好【离散】。")

    # 中位数/平滑的效果(前端本来就在做窗口平均, 这里估计其收益)
    print("\n若在输出端加时间平滑(前端已有60s窗口, 这里模拟中位数):")
    for w in (30, 60, 120):
        if n > w:
            med_now = np.array([np.median(chosen[max(0, i - w):i + 1]) for i in range(n)])
            med_str = np.array([np.median(strongest[max(0, i - w):i + 1]) for i in range(n)])
            print("  窗口%3d轮: 追踪器 MAE %.2f (偏差%+.2f) | 最强峰 MAE %.2f (偏差%+.2f)"
                  % (w, np.abs(med_now - truth).mean(), med_now.mean() - truth,
                     np.abs(med_str - truth).mean(), med_str.mean() - truth))

    print("\n注: oracle 仅从日志所存【前5峰】中挑选, 真实候选更多,")
    print("    故真实天花板只会比此处更好, 不会更差。")
