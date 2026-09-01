#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最后一个诊断实验: 若把 ROI 抖动去掉, 脉搏能恢复吗?

tb9: 平移配准可消除30.6%的帧间残差 => 确有位移。
本实验对每个窗口做【逐帧平移配准】(对齐到首帧), 再跑模型, 看频谱是否变尖锐。

同时评估另一条更现实的路线:
  既然低质量窗口的输出必然趋向【搜索带中心】, 那么把带中心对准
  人群静息心率(≈70-75)就能显著降低偏高 —— 这不是"作弊",
  而是让"无信息时的先验"落在正确位置(贝叶斯意义上的合理先验)。
  对照: 带[50,140]中心95  vs  带[45,110]中心77.5  vs  [50,110]中心80
"""
import os
import glob
import numpy as np
import torch
import tb_core as TB

SESS = "4b989b29"
N = 40
files = sorted(glob.glob(os.path.join(TB.CAPTURE_DIR, "*%s*.npz" % SESS)))[:N]
model, dev = TB.load_model()


def infer(fr_u8):
    x = torch.from_numpy(fr_u8.astype(np.float32)).permute(1, 0, 2, 3).unsqueeze(0).to(dev)
    with torch.no_grad():
        out, _ = model(x)
    return out[0].detach().cpu().numpy()


def stabilize(fr):
    """逐帧对齐到首帧(整数平移搜索±4px, 用绿通道)"""
    out = np.empty_like(fr)
    out[0] = fr[0]
    ref = fr[0, 1].astype(np.float64)
    for t in range(1, fr.shape[0]):
        g = fr[t, 1].astype(np.float64)
        best, bd = None, 1e18
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                gg = np.roll(np.roll(g, dy, axis=0), dx, axis=1)
                v = np.mean(np.abs(gg[4:-4, 4:-4] - ref[4:-4, 4:-4]))
                if v < bd:
                    bd, best = v, (dy, dx)
        dy, dx = best
        out[t] = np.roll(np.roll(fr[t], dy, axis=1), dx, axis=2)
    return out


def qual(pts, LL=50, UL=140):
    sp = TB.spectrum(pts, LL=LL, UL=UL)
    if sp is None:
        return None, 0.0
    pk = sp["peaks"]
    e = np.array([p[1] for p in pk])
    return max(pk, key=lambda x: x[1])[0], float(e.max() / np.median(e))


res = {"原始": {"t": [], "s": []}, "配准后": {"t": [], "s": []}}
for i, fp in enumerate(files):
    d = np.load(fp)
    rs, T = TB.resample_window(d["frames"], d["timestamps"])
    if rs is None:
        continue
    t1, s1 = qual(infer(rs))
    if t1:
        res["原始"]["t"].append(t1)
        res["原始"]["s"].append(s1)
    t2, s2 = qual(infer(stabilize(rs)))
    if t2:
        res["配准后"]["t"].append(t2)
        res["配准后"]["s"].append(s2)
    if (i + 1) % 10 == 0:
        print("  %d/%d" % (i + 1, len(files)), flush=True)

print("\n" + "=" * 72)
print("ROI 配准对信号质量的影响")
print("=" * 72)
print("%-10s %6s %11s %11s %11s" % ("", "n", "最强峰均值", "尖锐度中位", "相邻跳变"))
for k in ("原始", "配准后"):
    t = np.array(res[k]["t"], float)
    s = np.array(res[k]["s"], float)
    if t.size < 3:
        continue
    print("%-10s %6d %11.2f %11.2f %11.1f"
          % (k, t.size, t.mean(), np.median(s), np.median(np.abs(np.diff(t)))))

# ------------------------------------------------ 搜索带先验实验
print("\n" + "=" * 72)
print("搜索带选择对'无信息输出'的影响 (纯噪声对照)")
print("=" * 72)
rng = np.random.default_rng(3)
print("%-16s %8s %14s" % ("搜索带", "中心", "纯噪声输出均值"))
for LL, UL in [(50, 140), (50, 120), (45, 110), (50, 110), (48, 105), (45, 100)]:
    v = []
    for _ in range(300):
        sp = TB.spectrum(rng.standard_normal(301), LL=LL, UL=UL)
        if sp:
            v.append(max(sp["peaks"], key=lambda x: x[1])[0])
    print("%-16s %8.1f %14.2f" % ("[%d,%d]" % (LL, UL), (LL + UL) / 2, np.mean(v)))

print("""
  静息心率典型范围 60~100, 中位≈72。
  若把搜索带设为 [45,110](中心77.5), 则:
    - 覆盖了 99% 的静息心率
    - 无信息时的输出落在 ~78 而非 ~95
    - 代价: 无法测到 >110bpm (运动/紧张时会截断)""")

# 真实数据在不同带下的表现
print("\n" + "=" * 72)
print("真实样本在不同搜索带下的最强峰")
print("=" * 72)
pts_cache = []
for fp in files:
    d = np.load(fp)
    rs, _ = TB.resample_window(d["frames"], d["timestamps"])
    if rs is not None:
        pts_cache.append(infer(rs))
for LL, UL in [(50, 140), (50, 120), (45, 110), (50, 110)]:
    v = []
    for pts in pts_cache:
        t, _ = qual(pts, LL, UL)
        if t:
            v.append(t)
    v = np.array(v)
    print("  带[%3d,%3d] 中心%5.1f -> 最强峰均值 %6.2f" % (LL, UL, (LL + UL) / 2, v.mean()))
