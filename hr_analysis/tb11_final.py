#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终方案对照: 搜索带 × 选峰策略, 在线上真实更新密度 R=70 下评估。

诚实前提(必须写进结论):
  tb5/tb9 已证明这批帧的脉搏信噪比约 1:1, 谱型接近噪声。
  因此【任何算法都不可能"测准"】。能做的是:
    (1) 让"无信息时的默认输出"落在生理合理位置(而不是95)
    (2) 消除追踪器的起点依赖/自锁(让结果可复现、不漂移)
    (3) 在信号确实不足时不要假装有读数
  这是"把先验摆正"而非"提高测量精度" —— 必须如实告知用户。

评价用两个真值参考:
  78.3 (被试B心电报告均值; 但捕获时段与报告时段不重叠, 仅作参考)
  72~81 (报告给出的平均心率区间)
"""
import numpy as np
import tb_core as TB

SESS = "4b989b29"
TRUTH = 78.3
R = 70

spectra_cache = {}


def get_spectra(LL, UL):
    """按指定搜索带重算频谱(基于已缓存的模型输出波形)"""
    key = (LL, UL)
    if key in spectra_cache:
        return spectra_cache[key]
    pts_all = np.load("/home/lsz/webapp/hr_analysis/tb_pts_%s.npy" % SESS,
                      allow_pickle=True)
    out = []
    for pts in pts_all:
        if pts is None:
            continue
        sp = TB.spectrum(pts, LL=LL, UL=UL)
        if sp:
            out.append(sp)
    spectra_cache[key] = out
    return out


def sel_current(pk, est, th=0.15):
    top = max(e for _, e in pk)
    strong = [(b, e) for b, e in pk if e >= top * th] or list(pk)
    return min(strong, key=lambda x: abs(x[0] - est))[0]


def sel_centroid(pk, est, frac=0.5):
    top = max(e for _, e in pk)
    sel = [(b, e) for b, e in pk if e >= top * frac] or [max(pk, key=lambda x: x[1])]
    w = np.array([e for _, e in sel])
    b = np.array([bb for bb, _ in sel])
    return float((b * w).sum() / w.sum())


def sel_tight(pk, est, th=0.5):
    top = max(e for _, e in pk)
    strong = [(b, e) for b, e in pk if e >= top * th] or [max(pk, key=lambda x: x[1])]
    return min(strong, key=lambda x: abs(x[0] - est))[0]


def evaluate(spectra, pick, R=R, init=75.0, alpha=0.25):
    est = init
    out = []
    for sp in spectra:
        for _ in range(R):
            c = pick(sp["peaks"], est)
            if c is None:
                continue
            out.append(c)
            est = alpha * c + (1 - alpha) * est
    return np.array(out, float)


def startdep(spectra, pick):
    ms = []
    for i0 in (55, 75, 95, 120):
        v = evaluate(spectra, pick, R=20, init=float(i0))
        ms.append(v.mean() if v.size else np.nan)
    return float(np.nanmax(ms) - np.nanmin(ms))


BANDS = [(50, 140), (50, 120), (45, 110), (50, 110)]
PICKS = [("现状 th=0.15", sel_current),
         ("紧阈值 th=0.50", sel_tight),
         ("能量重心 frac=0.5", sel_centroid)]

print("=" * 104)
print("搜索带 × 选峰策略   (R=%d, 参考真值 %.1f)" % (R, TRUTH))
print("=" * 104)
print("%-12s %-20s %9s %9s %8s %8s %9s %10s" %
      ("搜索带", "选峰", "均值", "偏差", "MAE", "±5bpm", "±10bpm", "起点极差"))
print("-" * 104)
best = []
for LL, UL in BANDS:
    sps = get_spectra(LL, UL)
    if not sps:
        continue
    for nm, fn in PICKS:
        v = evaluate(sps, fn)
        m = TB.metrics(v, TRUTH)
        sd = startdep(sps, fn)
        best.append((abs(m["bias"]) + 0.5 * m["mae"] + sd, LL, UL, nm, m, sd))
        print("%-12s %-20s %9.2f %+9.2f %8.2f %7.1f%% %8.1f%% %10.2f"
              % ("[%d,%d]" % (LL, UL), nm, m["mean"], m["bias"], m["mae"],
                 m["w5"], m["w10"], sd))

best.sort()
print("\n" + "=" * 104)
print("推荐方案")
print("=" * 104)
for sc, LL, UL, nm, m, sd in best[:4]:
    print("  带[%d,%d] + %s" % (LL, UL, nm))
    print("     偏差 %+.2f | MAE %.2f | ±5bpm %.1f%% | ±10bpm %.1f%% | 起点极差 %.2f"
          % (m["bias"], m["mae"], m["w5"], m["w10"], sd))
