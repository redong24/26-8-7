#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
决定性检验: 按【真实帧率】分层, 看信号质量如何变化。

tb6 发现: fps 中位数只有 2.98, 56.7% 的样本 <5fps。
物理约束: 心率 78bpm = 1.3Hz, 采样定理要求 fs > 2.6Hz 才能不混叠。
   fs=3.0Hz  -> 奈奎斯特 1.5Hz = 90bpm, 勉强够但抖动致命
   fs=2.0Hz  -> 奈奎斯特 1.0Hz = 60bpm, 78bpm 直接【混叠】
混叠后真实心率会被折射成【错误频率】, 而重采样到30fps并不能恢复信息
(插值不创造信息), 只是让频谱"看起来"填满了整个[50,140]带。

预测(若帧率是根因):
  高帧率样本 -> 谱尖锐、时间连贯、峰位稳定
  低帧率样本 -> 谱平坦、乱跳、≈噪声
"""
import os
import glob
import numpy as np
import tb_core as TB

SESS_LIST = ["4b989b29", "63d389c2", "209812c9", "c5a766cd", "ffa6d09d", "7909a2c5"]

rows = []
for sess in SESS_LIST:
    cache = "/home/lsz/webapp/hr_analysis/tb_q_%s.npy" % sess
    alt = "/home/lsz/webapp/hr_analysis/tb_spec_%s.npy" % sess
    path = cache if os.path.exists(cache) else (alt if os.path.exists(alt) else None)
    if path is None:
        continue
    spectra = list(np.load(path, allow_pickle=True))
    files = sorted(glob.glob(os.path.join(TB.CAPTURE_DIR, "*%s*.npz" % sess)))
    for fp, sp in zip(files, spectra):
        if sp is None:
            continue
        ts = np.load(fp)["timestamps"]
        if ts[-1] <= ts[0]:
            continue
        # 影子链路窗口是最近10秒内的真实帧
        tw = ts[ts >= ts[-1] - 10.0]
        if tw.size < 2 or tw[-1] <= tw[0]:
            continue
        fps = (tw.size - 1) / (tw[-1] - tw[0])
        pk = sp["peaks"]
        e = np.array([x[1] for x in pk])
        rows.append(dict(sess=sess, fps=fps,
                         top=max(pk, key=lambda x: x[1])[0],
                         sharp=e.max() / np.median(e),
                         npk=len(pk)))

print("总样本 = %d (跨 %d 个会话)" % (len(rows), len(set(r["sess"] for r in rows))))
fps = np.array([r["fps"] for r in rows])
top = np.array([r["top"] for r in rows])
sharp = np.array([r["sharp"] for r in rows])

print("\n窗口内真实帧率: 中位=%.2f  均值=%.2f  p90=%.2f"
      % (np.median(fps), fps.mean(), np.percentile(fps, 90)))
print("心率78bpm=1.3Hz 需要 fs>2.6Hz 才不混叠 -> 低于此即物理不可测")
print("  fps<2.6 占比 = %.1f%%  <5 占比 = %.1f%%  >=10 占比 = %.1f%%"
      % (100 * (fps < 2.6).mean(), 100 * (fps < 5).mean(), 100 * (fps >= 10).mean()))

BINS = [(0, 2), (2, 3), (3, 5), (5, 8), (8, 11), (11, 99)]
print("\n" + "=" * 92)
print("按窗口真实帧率分层")
print("=" * 92)
print("%-12s %7s %11s %11s %11s %12s" %
      ("fps区间", "n", "最强峰均值", "尖锐度中位", "相邻跳变", "峰位标准差"))
print("-" * 92)
for lo, hi in BINS:
    m = (fps >= lo) & (fps < hi)
    if m.sum() < 5:
        continue
    t = top[m]
    jump = np.median(np.abs(np.diff(t))) if t.size > 2 else np.nan
    print("%-12s %7d %11.2f %11.2f %11.1f %12.2f"
          % ("[%g,%g)" % (lo, hi), m.sum(), t.mean(),
             np.median(sharp[m]), jump, t.std()))

# 相关性
def sp_corr(a, b):
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])

print("\n  Spearman corr(fps, 谱尖锐度) = %+.3f  (正=帧率越高谱越尖锐)"
      % sp_corr(fps, sharp))
print("  Spearman corr(fps, 最强峰bpm) = %+.3f" % sp_corr(fps, top))

hi_m = fps >= 10
lo_m = fps < 3
if hi_m.sum() >= 10 and lo_m.sum() >= 10:
    print("\n  高帧率(>=10fps, n=%d): 最强峰均值 %.2f, 尖锐度 %.2f, 峰位std %.2f"
          % (hi_m.sum(), top[hi_m].mean(), np.median(sharp[hi_m]), top[hi_m].std()))
    print("  低帧率(<3fps,  n=%d): 最强峰均值 %.2f, 尖锐度 %.2f, 峰位std %.2f"
          % (lo_m.sum(), top[lo_m].mean(), np.median(sharp[lo_m]), top[lo_m].std()))
    print("  >>> 高帧率组最强峰均值比低帧率组 %+.2f bpm"
          % (top[hi_m].mean() - top[lo_m].mean()))

# 混叠预测: fs 下真实心率78 会被折射到哪
print("\n" + "=" * 92)
print("混叠预测: 真实心率 78bpm(1.3Hz) 在各采样率下会被观测成什么频率?")
print("=" * 92)
f_true = 78 / 60.0
for fs_v in (1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 10.0, 13.8):
    k = round(f_true / fs_v)
    f_alias = abs(f_true - k * fs_v)
    bpm_alias = f_alias * 60
    note = "✅ 无混叠" if fs_v > 2 * f_true else "❌ 混叠 -> 观测为 %.1f bpm" % bpm_alias
    print("  fs=%5.1f Hz (奈奎斯特 %5.1f bpm): %s" % (fs_v, fs_v * 30, note))
print("""
  注意: 混叠后的观测频率通常【低于】真值, 而我们看到的是偏高。
  说明低帧率下不是简单混叠, 而是脉搏成分被彻底摧毁、
  谱型退化为噪声 -> 选峰结果趋向带中心95 -> 相对真值78 表现为偏高。""")
