#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨会话验证: g7 的"闭环自证"结论是否只在会话4b989b29成立?

我此前多次把单一样本的巧合当成机制, 这次必须先做泛化检验再下结论。
对每个日志文件的每个会话(>=200轮)分别计算:
    corr(chosen, est_before)  vs  corr(chosen, strongest)
若闭环自证是普遍机制, 前者应普遍接近1, 后者普遍接近0。

纯读日志。
"""
import re
import os
import numpy as np
from collections import defaultdict

BASE = "/home/lsz/real_time_plus/real_time_Demo"
LOGS = [
    ("改前 band[58,180]", "output.log.before_bandfix_20260810_131452"),
    ("改后 band[50,140]", "output.log.before_gate_20260810_164015"),
    ("门控后",            "output.log"),
]

pat = re.compile(
    r"\[SHADOW-HR-DIAG\] session=([0-9a-f]{8})\S* "
    r"独立局部[峰峭]值前5\(bpm,能量\)=\[(.*?)\] "
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
pt = re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")

print("=" * 92)
print("跨会话泛化检验: chosen 是跟随'上次的自己'还是跟随'能量最强峰'?")
print("=" * 92)
print("%-20s %-10s %7s %11s %11s %10s %10s" %
      ("日志", "会话", "n", "corr(est)", "corr(峰)", "|c-est|中位", "|c-峰|中位"))
print("-" * 92)

summary = []
for label, fn in LOGS:
    path = os.path.join(BASE, fn)
    if not os.path.exists(path):
        continue
    data = defaultdict(lambda: {"c": [], "e": [], "s": []})
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "SHADOW-HR-DIAG" not in line:
                continue
            m = pat.search(line)
            if not m:
                continue
            sess, body, c, e = m.groups()
            pk = pt.findall(body)
            if not pk:
                continue
            d = data[sess]
            d["c"].append(float(c))
            d["e"].append(float(e))
            d["s"].append(float(pk[0][0]))
    for sess, d in sorted(data.items(), key=lambda kv: -len(kv[1]["c"])):
        n = len(d["c"])
        if n < 200:
            continue
        c = np.array(d["c"])
        ea = np.array(d["e"])
        s = np.array(d["s"])
        eb = (ea - 0.25 * c) / 0.75          # 反解本轮决策依据
        if c.std() < 1e-9 or eb.std() < 1e-9 or s.std() < 1e-9:
            continue
        r_est = float(np.corrcoef(c, eb)[0, 1])
        r_str = float(np.corrcoef(c, s)[0, 1])
        print("%-20s %-10s %7d %+11.3f %+11.3f %10.2f %10.2f"
              % (label, sess, n, r_est, r_str,
                 np.median(np.abs(c - eb)), np.median(np.abs(c - s))))
        summary.append((r_est, r_str, n))

print("-" * 92)
if summary:
    re_ = np.array([x[0] for x in summary])
    rs_ = np.array([x[1] for x in summary])
    print("会话数 = %d" % len(summary))
    print("corr(chosen, est_before): 均值 %+.3f   范围 %+.3f ~ %+.3f"
          % (re_.mean(), re_.min(), re_.max()))
    print("corr(chosen, strongest) : 均值 %+.3f   范围 %+.3f ~ %+.3f"
          % (rs_.mean(), rs_.min(), rs_.max()))
    print("\n所有会话 corr(est)>0.8 的比例 = %.0f%%" % (100.0 * (re_ > 0.8).mean()))
    print("所有会话 |corr(峰)|<0.3 的比例 = %.0f%%" % (100.0 * (np.abs(rs_) < 0.3).mean()))
    if (re_ > 0.8).all() and (np.abs(rs_) < 0.3).all():
        print("\n>>> 闭环自证在【全部会话】成立, 不是单会话巧合。")
    else:
        print("\n>>> 存在例外会话, 结论需限定范围。")
