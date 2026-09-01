#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
候选修复方案设计与对照 —— 在 R=70 (线上真实更新密度) 下评估。

tb2 已证明: 现状追踪器在高更新密度下退化为"记忆初值", 100%起点依赖。
任何候选方案必须同时满足:
  (A) 在 R=70 下偏差接近 0 (不是靠低密度侥幸)
  (B) 起点无关: init 从 55 到 135 收敛结果一致
  (C) 保留抗噪能力: 不能简单退回 argmax(那样 MAE 反而更差, g9 已测)

对照的策略:
  S0 现状
  S1 纯能量最强峰(无追踪)
  S2 能量优先 + 追踪仅做平局裁决(能量占绝对主导)
  S3 联合打分: 能量权重 w, 接近度权重 (1-w)
  S4 能量加权重心(在最强峰邻域内做谱重心)
  S5 中位数投票(近N轮最强峰的中位数)
  S6 现状 + 收紧能量阈值(0.15 -> 0.5/0.7)
"""
import numpy as np
import tb_core as TB

SESS = "4b989b29"
TRUTH = 78.3
R_ONLINE = 70

spectra = [s for s in np.load(
    "/home/lsz/webapp/hr_analysis/tb_spec_%s.npy" % SESS, allow_pickle=True)
    if s is not None]
print("频谱数 = %d" % len(spectra))


# ------------------------------------------------------------ 策略定义
def s_current(pk, est, th=0.15):
    top = max(e for _, e in pk)
    strong = [(b, e) for b, e in pk if e >= top * th] or list(pk)
    return min(strong, key=lambda x: abs(x[0] - est))[0]


def s_strongest(pk, est):
    return max(pk, key=lambda x: x[1])[0]


def s_tight(th):
    def f(pk, est):
        top = max(e for _, e in pk)
        strong = [(b, e) for b, e in pk if e >= top * th] or [max(pk, key=lambda x: x[1])]
        return min(strong, key=lambda x: abs(x[0] - est))[0]
    return f


def s_joint(w):
    """能量归一 * w + 接近度 * (1-w); 接近度用 exp(-|d|/15)"""
    def f(pk, est):
        top = max(e for _, e in pk)
        if top <= 1e-12:
            return None
        best, bs = None, -1e18
        for b, e in pk:
            sc = w * (e / top) + (1 - w) * np.exp(-abs(b - est) / 15.0)
            if sc > bs:
                bs, best = sc, b
        return best
    return f


def s_centroid(pk, est, frac=0.5):
    """在能量>=frac*top 的峰上做能量加权重心"""
    top = max(e for _, e in pk)
    sel = [(b, e) for b, e in pk if e >= top * frac] or [max(pk, key=lambda x: x[1])]
    w = np.array([e for _, e in sel])
    b = np.array([bb for bb, _ in sel])
    return float((b * w).sum() / w.sum())


# ------------------------------------------------------------ 评估器
def evaluate(pick, R, init=75.0, alpha=0.25):
    est = init
    out = []
    for sp in spectra:
        for _ in range(R):
            c = pick(sp["peaks"], est)
            if c is None:
                continue
            out.append(c)
            est = alpha * c + (1 - alpha) * est
    return np.array(out, dtype=float)


def start_dependence(pick, R=20, alpha=0.25):
    """不同初值下的收敛均值极差 —— 越小越好"""
    means = []
    for i0 in (55, 75, 95, 120, 135):
        v = evaluate(pick, R, init=float(i0), alpha=alpha)
        means.append(v.mean() if v.size else np.nan)
    return float(np.nanmax(means) - np.nanmin(means)), means


STRATS = [
    ("S0 现状(th=0.15,纯接近est)", s_current),
    ("S1 能量最强峰(无追踪)", s_strongest),
    ("S6a 现状+th=0.50", s_tight(0.50)),
    ("S6b 现状+th=0.70", s_tight(0.70)),
    ("S6c 现状+th=0.85", s_tight(0.85)),
    ("S3a 联合 w=0.5", s_joint(0.5)),
    ("S3b 联合 w=0.7", s_joint(0.7)),
    ("S3c 联合 w=0.85", s_joint(0.85)),
    ("S4 能量重心 frac=0.5", lambda pk, est: s_centroid(pk, est, 0.5)),
]

print("\n" + "=" * 100)
print("R = %d (线上真实更新密度) 下的策略对照   真值 %.1f" % (R_ONLINE, TRUTH))
print("=" * 100)
print("%-26s %9s %9s %8s %8s %8s %11s" %
      ("策略", "均值", "偏差", "MAE", "±5bpm", "±10bpm", "起点极差"))
print("-" * 100)
rows = []
for name, fn in STRATS:
    v = evaluate(fn, R_ONLINE)
    m = TB.metrics(v, TRUTH)
    sd, _ = start_dependence(fn)
    rows.append((name, m, sd))
    print("%-26s %9.2f %+9.2f %8.2f %7.1f%% %7.1f%% %11.2f"
          % (name, m["mean"], m["bias"], m["mae"], m["w5"], m["w10"], sd))

print("\n" + "=" * 100)
print("起点无关性明细 (init = 55 / 75 / 95 / 120 / 135 时的输出均值)")
print("=" * 100)
for name, fn in STRATS:
    sd, means = start_dependence(fn)
    print("%-26s %s   极差=%.2f" % (name,
          " ".join("%7.2f" % x for x in means), sd))

print("\n" + "=" * 100)
print("推荐: 偏差小 + MAE小 + 起点极差≈0")
print("=" * 100)
best = sorted(rows, key=lambda r: (abs(r[1]["bias"]) + r[1]["mae"] + r[2]))[:3]
for name, m, sd in best:
    print("  %-26s 偏差%+.2f MAE%.2f ±5bpm%.1f%% 起点极差%.2f"
          % (name, m["bias"], m["mae"], m["w5"], sd))
