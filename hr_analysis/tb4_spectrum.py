#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tb3 是个重要的【否定结果】: 所有选峰策略偏差都停在 +14 左右。
=> 偏高不只来自追踪器, 频谱本身就偏高。

但在归因给"模型"之前, 必须先排除两个更平凡的解释:
  (甲) 真值 78.3 是否适用于这120个捕获样本? (时间对齐问题)
  (乙) 带通滤波器 butter(order=6, [50,140]) 是否本身抬高了频谱?

(乙)是可以直接测的: 给【纯白噪声】做同样处理, 看输出分布中心在哪。
    若纯噪声输出中心显著高于带中心, 说明滤波器/周期图链路有系统性抬升。
"""
import os
import glob
import numpy as np
import scipy.signal as scipy_signal
import tb_core as TB

SESS = "4b989b29"
TRUTH = 78.3

# ---------------------------------------------------------- 甲: 时间对齐
print("=" * 76)
print("[甲] 捕获样本的真实时间跨度 vs 仪器测量时段")
print("=" * 76)
files = sorted(glob.glob(os.path.join(TB.CAPTURE_DIR, "*%s*.npz" % SESS)))
t0s = []
for fp in files:
    d = np.load(fp)
    t0s.append((d["timestamps"][0], d["timestamps"][-1]))
t0s = np.array(t0s)
import datetime as dt
fmt = lambda x: dt.datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S")
print("  样本数 = %d" % len(files))
print("  最早帧 = %s" % fmt(t0s[:, 0].min()))
print("  最晚帧 = %s" % fmt(t0s[:, 1].max()))
print("  总跨度 = %.1f 分钟" % ((t0s[:, 1].max() - t0s[:, 0].min()) / 60))
print("  用户提供的心电报告时段: 2026-08-10 14:08 ~ 14:20 (平均心率 72~81)")
print("  >>> 若捕获时段与心电时段不重叠, 78.3 这个真值就【不适用】于这批帧")

# ---------------------------------------------------------- 乙: 纯噪声对照
print("\n" + "=" * 76)
print("[乙] 纯噪声对照: 处理链路本身是否抬高频谱?")
print("=" * 76)
rng = np.random.default_rng(20260810)
T, FS = 301, 30.0
res = {}
for LL, UL in [(50, 140), (58, 180), (45, 120), (40, 110), (50, 110)]:
    strongest, centroid = [], []
    for _ in range(400):
        sig = rng.standard_normal(T)
        sp = TB.spectrum(sig, fs=FS, LL=LL, UL=UL)
        if sp is None:
            continue
        pk = sp["peaks"]
        strongest.append(max(pk, key=lambda x: x[1])[0])
        w = np.array([e for _, e in pk])
        b = np.array([bb for bb, _ in pk])
        centroid.append(float((b * w).sum() / w.sum()))
    strongest = np.array(strongest)
    res[(LL, UL)] = strongest
    print("  带[%3d,%3d] 中心%5.1f | 纯噪声最强峰均值=%6.2f (相对中心 %+5.2f) | 重心均值=%6.2f"
          % (LL, UL, (LL + UL) / 2, strongest.mean(),
             strongest.mean() - (LL + UL) / 2, np.mean(centroid)))
print("""
  解读: 纯噪声【不含任何心跳】, 若输出均值≈带中心, 说明无信号时
        系统就是在"带中心附近瞎猜"。真值78.3 低于带[50,140]中心95,
        因此任何"信号不足的轮次"都会把结果往95拉 => 系统性偏高。""")

# ---------------------------------------------------------- 丙: 信号占比
print("\n" + "=" * 76)
print("[丙] 真实样本里, 有多少轮的频谱是'有真信号'的?")
print("=" * 76)
spectra = [s for s in np.load(
    "/home/lsz/webapp/hr_analysis/tb_spec_%s.npy" % SESS, allow_pickle=True)
    if s is not None]

# 用"最强峰能量 / 次强峰能量"衡量谱的尖锐度(信噪比代理)
ratios, tops, has_true = [], [], []
for sp in spectra:
    pk = sorted(sp["peaks"], key=lambda x: -x[1])
    tops.append(pk[0][0])
    ratios.append(pk[0][1] / pk[1][1] if len(pk) > 1 and pk[1][1] > 0 else np.inf)
    has_true.append(any(abs(b - TRUTH) <= 5 for b, _ in sp["peaks"]))
ratios = np.array(ratios)
tops = np.array(tops)
has_true = np.array(has_true)
print("  最强/次强 能量比: 中位 %.2f  (>2 视为谱尖锐)" % np.median(ratios))
print("  谱尖锐(比值>2) 的轮次占比 = %.1f%%" % (100.0 * (ratios > 2).mean()))
print("  真值±5内存在候选峰的轮次   = %.1f%%" % (100.0 * has_true.mean()))
sharp = ratios > 2
if sharp.sum() > 0:
    print("\n  仅在【谱尖锐】的轮次上看最强峰:")
    print("     n=%d  均值=%.2f  偏差=%+.2f  MAE=%.2f"
          % (sharp.sum(), tops[sharp].mean(), tops[sharp].mean() - TRUTH,
             np.abs(tops[sharp] - TRUTH).mean()))
    print("  在【谱平坦】的轮次上:")
    print("     n=%d  均值=%.2f  偏差=%+.2f  MAE=%.2f"
          % ((~sharp).sum(), tops[~sharp].mean(), tops[~sharp].mean() - TRUTH,
             np.abs(tops[~sharp] - TRUTH).mean()))
    print("""
  >>> 若"谱尖锐"时准、"谱平坦"时偏高, 那么正确的修法是
      【在谱平坦时不输出/保持上次值】, 而不是继续调选峰规则。""")
