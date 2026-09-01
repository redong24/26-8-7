#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自查: tb5 说"所有会话都像噪声"。在把责任推给采集端之前,
必须先排除【我的测试平台本身有问题】。

方法: 完全绕开 PhaseNet, 用最经典的 rPPG 方法直接从原始帧提取脉搏:
  - 通道均值法(绿通道) : 最简单的基线
  - CHROM 法           : 经典抗运动方法
若这些【传统方法】能从同一批帧里看到清晰的心搏峰, 说明帧是好的,
问题在模型/预处理; 若传统方法也看不到, 说明帧本身就没有脉搏信息。

同时检查帧的基本合理性: 是不是人脸ROI? 亮度是否正常? 通道顺序?
"""
import os
import glob
import numpy as np
import scipy.signal as scipy_signal
import tb_core as TB

SESS = "4b989b29"
FS_NOMINAL = 30.0

files = sorted(glob.glob(os.path.join(TB.CAPTURE_DIR, "*%s*.npz" % SESS)))
print("样本数 = %d" % len(files))

d = np.load(files[0])
fr = d["frames"]           # (160,3,72,72) uint8
ts = d["timestamps"]
print("\n[0] 帧基本信息")
print("  shape=%s dtype=%s" % (fr.shape, fr.dtype))
print("  像素范围 %d ~ %d, 均值 %.1f" % (fr.min(), fr.max(), fr.mean()))
for c in range(3):
    print("     通道%d 均值=%.1f 标准差=%.1f" % (c, fr[:, c].mean(), fr[:, c].std()))
print("  时间跨度 %.2fs, 等效 fps %.2f" % (ts[-1] - ts[0], (len(ts) - 1) / (ts[-1] - ts[0])))
print("  帧间像素差(相邻帧MAE) = %.2f  <- 太小=画面几乎静止/冻结"
      % np.mean(np.abs(fr[1:].astype(float) - fr[:-1].astype(float))))

# 导出一张图供人眼确认是不是人脸
try:
    import imageio.v2 as imageio
    img = fr[0].transpose(1, 2, 0)[:, :, ::-1]   # CHW->HWC, BGR->RGB
    imageio.imwrite("/home/lsz/webapp/hr_analysis/sample_face.png", img.astype(np.uint8))
    print("  已导出首帧 -> sample_face.png (可人眼确认是否为人脸ROI)")
except Exception as e:
    print("  导出图片失败: %s" % e)


def bandpass_hr(x, fs, LL=45, UL=150):
    b, a = scipy_signal.butter(4, [LL / 60, UL / 60], btype='bandpass', fs=fs)
    return scipy_signal.filtfilt(b, a, np.double(x))


def peak_bpm(x, fs, LL=45, UL=150):
    """返回(最强峰bpm, 尖锐度=最强峰能量/中位能量)"""
    x = np.asarray(x, float)
    if x.std() < 1e-9:
        return np.nan, 0.0
    x = (x - x.mean()) / x.std()
    try:
        f = bandpass_hr(x, fs, LL, UL)
    except Exception:
        return np.nan, 0.0
    N = int((60 * fs) / 0.1)
    F, P = scipy_signal.periodogram(f, nfft=N, fs=fs, window='hann')
    bpm = F * 60
    m = (bpm >= LL) & (bpm <= UL)
    if m.sum() == 0:
        return np.nan, 0.0
    b, p = bpm[m], P[m]
    i = int(np.argmax(p))
    return float(b[i]), float(p[i] / np.median(p))


def chrom(frames):
    """CHROM: X=3R-2G, Y=1.5R+G-1.5B, S=X-alpha*Y。frames (T,3,H,W) BGR"""
    f = frames.astype(np.float64)
    B = f[:, 0].mean(axis=(1, 2))
    G = f[:, 1].mean(axis=(1, 2))
    R = f[:, 2].mean(axis=(1, 2))
    Rn = R / (R.mean() + 1e-9)
    Gn = G / (G.mean() + 1e-9)
    Bn = B / (B.mean() + 1e-9)
    X = 3 * Rn - 2 * Gn
    Y = 1.5 * Rn + Gn - 1.5 * Bn
    a = X.std() / (Y.std() + 1e-9)
    return X - a * Y


print("\n" + "=" * 84)
print("传统 rPPG 方法 (完全不用 PhaseNet) 能否从同一批帧看到心搏?")
print("=" * 84)
res = {"绿通道": [], "CHROM": [], "PhaseNet": []}
sharp = {"绿通道": [], "CHROM": [], "PhaseNet": []}

spec_cache = np.load("/home/lsz/webapp/hr_analysis/tb_spec_%s.npy" % SESS,
                     allow_pickle=True)

for i, fp in enumerate(files):
    dd = np.load(fp)
    fr = dd["frames"]
    tt = dd["timestamps"]
    fs = (len(tt) - 1) / (tt[-1] - tt[0]) if tt[-1] > tt[0] else FS_NOMINAL
    g = fr[:, 1].mean(axis=(1, 2))
    b1, s1 = peak_bpm(g, fs)
    res["绿通道"].append(b1)
    sharp["绿通道"].append(s1)
    c = chrom(fr)
    b2, s2 = peak_bpm(c, fs)
    res["CHROM"].append(b2)
    sharp["CHROM"].append(s2)
    sp = spec_cache[i]
    if sp is not None:
        pk = sp["peaks"]
        e = np.array([x[1] for x in pk])
        res["PhaseNet"].append(max(pk, key=lambda x: x[1])[0])
        sharp["PhaseNet"].append(e.max() / np.median(e))
    else:
        res["PhaseNet"].append(np.nan)
        sharp["PhaseNet"].append(0.0)

print("%-12s %8s %10s %10s %12s %12s" %
      ("方法", "n", "均值bpm", "中位bpm", "相邻跳变", "尖锐度中位"))
print("-" * 84)
for k in ("绿通道", "CHROM", "PhaseNet"):
    v = np.array(res[k], float)
    v = v[np.isfinite(v)]
    jump = np.median(np.abs(np.diff(v))) if v.size > 2 else np.nan
    print("%-12s %8d %10.2f %10.2f %12.1f %12.2f"
          % (k, v.size, v.mean(), np.median(v), jump, np.median(sharp[k])))

print("""
判读:
  * 若"绿通道/CHROM"的相邻跳变明显小于 PhaseNet, 且尖锐度更高,
    => 帧是好的, 是【模型或其预处理】的问题。
  * 若三者都乱跳(≈20bpm), => 帧本身没有可用脉搏 => 采集端问题。
  * 注意: 捕获帧是 72x72 的人脸ROI, 传统方法在小ROI上也应能工作。""")

# 帧率的影响
print("\n" + "=" * 84)
print("各样本真实帧率分布 (低帧率会直接摧毁脉搏信息)")
print("=" * 84)
fps_all = []
for fp in files:
    tt = np.load(fp)["timestamps"]
    if tt[-1] > tt[0]:
        fps_all.append((len(tt) - 1) / (tt[-1] - tt[0]))
fps_all = np.array(fps_all)
print("  fps: 均值%.2f 中位%.2f 最小%.2f 最大%.2f" %
      (fps_all.mean(), np.median(fps_all), fps_all.min(), fps_all.max()))
print("  <5fps 占比 = %.1f%%   (奈奎斯特: 5fps只能测到150bpm以下, 但抖动更致命)"
      % (100.0 * (fps_all < 5).mean()))
print("  <10fps 占比 = %.1f%%" % (100.0 * (fps_all < 10).mean()))
