#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检验 rPPG 的经典杀手: ROI 抖动淹没脉搏。

物理量级:
  脉搏引起的皮肤亮度变化 ≈ 0.5~1% (在均值110上约 0.5~1 个灰阶)
  而 tb6 实测【相邻帧像素MAE = 10.12 个灰阶】
若这10个灰阶主要来自人脸框抖动/重采样错位, 则脉搏被埋在 10~20倍
的干扰之下, 任何模型都提不出来 —— 这与"所有会话都像噪声"完全吻合。

区分方法: 把每帧的空间均值(全局亮度)与逐像素变化分开。
  - 若逐像素差远大于空间均值差 => 是【空间错位/抖动】
  - 若两者相当                => 是整体光照变化
另外检验: 相邻帧做平移配准后, 残差能否显著下降(=证明确实是位移)。
"""
import os
import glob
import numpy as np
import tb_core as TB

SESS = "4b989b29"
files = sorted(glob.glob(os.path.join(TB.CAPTURE_DIR, "*%s*.npz" % SESS)))[:40]
print("样本数 = %d" % len(files))

pix_mae, glob_diff, pulse_amp, ratio = [], [], [], []
shift_gain = []

for fp in files:
    fr = np.load(fp)["frames"].astype(np.float64)   # (T,3,H,W)
    T = fr.shape[0]
    # 逐像素相邻帧差
    pm = np.mean(np.abs(fr[1:] - fr[:-1]))
    # 空间均值的相邻帧差(整体亮度变化)
    gm = np.mean(np.abs(fr[1:].mean(axis=(2, 3)) - fr[:-1].mean(axis=(2, 3))))
    # 绿通道空间均值序列的"脉搏级"波动(去趋势后的std)
    g = fr[:, 1].mean(axis=(1, 2))
    gd = g - np.convolve(g, np.ones(9) / 9, mode='same')
    pix_mae.append(pm)
    glob_diff.append(gm)
    pulse_amp.append(gd.std())
    ratio.append(pm / max(gm, 1e-9))

    # 平移配准增益: 对相邻帧做 ±3 像素整数平移搜索, 看残差能降多少
    a = fr[0, 1]
    b = fr[1, 1]
    base = np.mean(np.abs(b - a))
    best = base
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            bb = np.roll(np.roll(b, dy, axis=0), dx, axis=1)
            v = np.mean(np.abs(bb[3:-3, 3:-3] - a[3:-3, 3:-3]))
            best = min(best, v)
    shift_gain.append((base - best) / max(base, 1e-9))

pix_mae = np.array(pix_mae)
glob_diff = np.array(glob_diff)
pulse_amp = np.array(pulse_amp)
ratio = np.array(ratio)
shift_gain = np.array(shift_gain)

print("\n" + "=" * 78)
print("干扰 vs 脉搏 的量级对比")
print("=" * 78)
print("  相邻帧【逐像素】MAE      = %.2f 灰阶" % pix_mae.mean())
print("  相邻帧【空间均值】差     = %.3f 灰阶" % glob_diff.mean())
print("  绿通道去趋势波动(≈脉搏)  = %.3f 灰阶" % pulse_amp.mean())
print("  逐像素差 / 空间均值差    = %.1f 倍" % ratio.mean())
print("""
  解读:
   * 逐像素差 >> 空间均值差  => 帧间存在【空间错位】(人脸框抖动/重采样)
   * 脉搏幅度只有 %.2f 灰阶, 而逐像素干扰有 %.1f 灰阶
     => 信噪比约 %.3f (1:%.0f)""" % (pulse_amp.mean(), pix_mae.mean(),
                                     pulse_amp.mean() / pix_mae.mean(),
                                     pix_mae.mean() / max(pulse_amp.mean(), 1e-9)))

print("\n  平移配准可消除的残差比例 = %.1f%%" % (100 * shift_gain.mean()))
if shift_gain.mean() > 0.15:
    print("  >>> 显著: 相邻帧确实存在整体位移 => 人脸框抖动是主要干扰源")
else:
    print("  >>> 位移不显著, 干扰主要来自其他(压缩噪声/光照/重采样插值)")

# 帧率与干扰的关系
print("\n" + "=" * 78)
print("低帧率如何放大干扰")
print("=" * 78)
fps_l, pm_l = [], []
for fp in files:
    d = np.load(fp)
    ts = d["timestamps"]
    if ts[-1] <= ts[0]:
        continue
    fr = d["frames"].astype(np.float64)
    fps_l.append((len(ts) - 1) / (ts[-1] - ts[0]))
    pm_l.append(np.mean(np.abs(fr[1:] - fr[:-1])))
fps_l = np.array(fps_l)
pm_l = np.array(pm_l)
for lo, hi in [(0, 3), (3, 6), (6, 10), (10, 99)]:
    m = (fps_l >= lo) & (fps_l < hi)
    if m.sum() >= 3:
        print("  fps[%2g,%2g): n=%2d  相邻帧像素MAE=%.2f" % (lo, hi, m.sum(), pm_l[m].mean()))
print("""
  帧率越低, 相邻帧间隔越长, 人脸位移越大 => 像素差越大。
  这解释了为什么低帧率窗口的频谱最像噪声。""")
