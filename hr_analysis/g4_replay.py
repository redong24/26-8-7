#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用线上日志里记录的【真实候选峰(bpm,能量)】重放选峰策略。

第0步必须先做【保真度验证】: 用完全相同的规则重放, 看能否复现日志里的
chosen_shadow_hr。只有复现率足够高, 后面的反事实才可信。

规则(严格照抄已锁定的 compute_hr_with_tracking, 只读):
    top_energy = max(e)
    strong = [(b,e) for b,e in candidates if e >= top_energy*0.15]
    chosen = min(strong, key=lambda x: abs(x[0]-prev_est))
    new_est = 0.25*chosen + 0.75*prev_est

已知局限: 日志只记录前5个峰, 真实候选可能更多。若复现率高, 说明前5够用。
"""
import re
import numpy as np

LOG = "/home/lsz/real_time_plus/real_time_Demo/output.log.before_gate_20260810_164015"
SESS = "4b989b29"
TRUTH = 78.3
THRESH = 0.15
ALPHA = 0.25
INIT_EST = 75.0

pat = re.compile(
    r"独立局部[峰峭]值前5\(bpm,能量\)=\[(.*?)\] "
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
pat_tuple = re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")

cand, chosen_log, est_log = [], [], []
with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if SESS not in line or "SHADOW-HR-DIAG" not in line:
            continue
        m = pat.search(line)
        if not m:
            continue
        pk = [(float(a), float(b)) for a, b in pat_tuple.findall(m.group(1))]
        if pk:
            cand.append(pk)
            chosen_log.append(float(m.group(2)))
            est_log.append(float(m.group(3)))

chosen_log = np.array(chosen_log)
est_log = np.array(est_log)
n = len(cand)
print("样本数 n = %d" % n)


def strong_set(peaks):
    top = max(e for _, e in peaks)
    s = [(b, e) for b, e in peaks if e >= top * THRESH]
    return s if s else list(peaks)


# ---------------------------------------------------- 第0步: 保真度验证
est = INIT_EST
rep = np.empty(n)
for i, pk in enumerate(cand):
    s = strong_set(pk)
    c, _ = min(s, key=lambda x: abs(x[0] - est))
    rep[i] = c
    est = ALPHA * c + (1 - ALPHA) * est

match = np.abs(rep - chosen_log) < 0.05
print("\n" + "=" * 70)
print("[第0步] 重放保真度验证")
print("=" * 70)
print("  完全复现日志 chosen 的比例 = %.1f%% (%d/%d)"
      % (100.0 * match.mean(), match.sum(), n))
print("  重放均值 %.2f  vs  日志均值 %.2f" % (rep.mean(), chosen_log.mean()))
if match.mean() < 0.9:
    print("  ⚠ 复现率偏低: 日志只存前5峰, 真实候选更多。反事实仅供参考。")
else:
    print("  ✅ 复现率高, 下面的反事实可信。")


def run(pick, label):
    """pick(strong_peaks, est) -> bpm"""
    est = INIT_EST
    out = np.empty(n)
    for i, pk in enumerate(cand):
        s = strong_set(pk)
        c = pick(s, est)
        out[i] = c
        est = ALPHA * c + (1 - ALPHA) * est
    err = np.abs(out - TRUTH)
    return {"label": label, "mean": out.mean(), "bias": out.mean() - TRUTH,
            "mae": err.mean(), "within5": 100.0 * (err <= 5).mean(),
            "within10": 100.0 * (err <= 10).mean(), "series": out}


res = []
# A. 现状: 最接近 est
res.append(run(lambda s, e: min(s, key=lambda x: abs(x[0] - e))[0],
               "A 现状(最接近est)"))
# B. 永远选能量最强
res.append(run(lambda s, e: max(s, key=lambda x: x[1])[0],
               "B 能量最强峰"))
# C. 能量最强, 但阈值更严(只在很强的峰里按接近est选)
res.append(run(lambda s, e: min([p for p in s if p[1] >= max(q[1] for q in s) * 0.5]
                                or s, key=lambda x: abs(x[0] - e))[0],
               "C 严阈值0.5+接近est"))
# D. 能量与接近度联合打分(能量归一 / (1+|Δ|/10))
res.append(run(lambda s, e: max(s, key=lambda x: (x[1] / max(q[1] for q in s))
                                / (1.0 + abs(x[0] - e) / 10.0))[0],
               "D 能量×接近度联合"))
# E. 候选中位数(抗离群)
res.append(run(lambda s, e: float(np.median([p[0] for p in s])),
               "E 候选中位数"))
# F. 能量加权平均
res.append(run(lambda s, e: float(np.average([p[0] for p in s],
                                             weights=[p[1] for p in s])),
               "F 能量加权平均"))

print("\n" + "=" * 78)
print("反事实对照 (真值 %.1f bpm, n=%d)" % (TRUTH, n))
print("=" * 78)
print("%-24s %8s %8s %8s %8s %8s" % ("策略", "均值", "偏差", "MAE", "±5bpm", "±10bpm"))
print("-" * 78)
for r in sorted(res, key=lambda x: x["mae"]):
    print("%-24s %8.2f %+8.2f %8.2f %7.1f%% %7.1f%%"
          % (r["label"], r["mean"], r["bias"], r["mae"],
             r["within5"], r["within10"]))

# 逐样本配对: 最优策略 vs 现状
base = [r for r in res if r["label"].startswith("A")][0]
best = min(res, key=lambda x: x["mae"])
if best is not base:
    eb = np.abs(base["series"] - TRUTH)
    eg = np.abs(best["series"] - TRUTH)
    dd = eg - eb
    t = dd.mean() / (dd.std(ddof=1) / np.sqrt(n))
    print("\n逐样本配对: 【%s】 vs 【%s】" % (best["label"], base["label"]))
    print("  平均误差差 = %+.3f bpm,  t = %+.2f" % (dd.mean(), t))
    print("  更准次数: %d (%.1f%%) | 更差: %d (%.1f%%)"
          % ((eg < eb).sum(), 100.0 * (eg < eb).mean(),
             (eg > eb).sum(), 100.0 * (eg > eb).mean()))
    print("  >>> %s" % ("显著更好" if t < -2 else
                        ("显著更差" if t > 2 else "无显著差异")))
