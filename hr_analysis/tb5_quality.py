#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据质量筛查: 哪些会话真的含有脉搏信号?

tb4 的警示: 会话 4b989b29 的最强峰均值 92.97, 而【纯白噪声】经同样处理
是 92.26 —— 几乎不可区分。若整批帧都是噪声主导, 那么用它评估任何
选峰策略都毫无意义(这也解释了为什么 tb3 里所有策略都停在 +14: 
它们都只是在噪声里挑数, 结果自然都≈带中心)。

判据(不依赖仪器真值, 因此不受时间对齐问题影响):
  1. 谱尖锐度 = 最强峰能量 / 该轮所有峰能量中位数
  2. 时间一致性 = 相邻样本最强峰的差异 (真心率变化缓慢, 噪声则乱跳)
  3. 与纯噪声基线的分布距离
时间一致性是最有力的: 噪声不可能产生时间上连贯的心率序列。
"""
import os
import re
import glob
import numpy as np
from collections import defaultdict
import tb_core as TB

CACHE = "/home/lsz/webapp/hr_analysis/tb_q_%s.npy"

# 挑样本数较多的会话
CANDIDATES = ["7909a2c5", "c5a766cd", "ffa6d09d", "209812c9",
              "4b989b29", "63d389c2", "1a0b863f", "01844bd3"]

# ---------- 噪声基线
rng = np.random.default_rng(7)
noise_top, noise_sharp = [], []
for _ in range(600):
    sp = TB.spectrum(rng.standard_normal(301))
    if sp is None:
        continue
    pk = sp["peaks"]
    e = np.array([x[1] for x in pk])
    noise_top.append(max(pk, key=lambda x: x[1])[0])
    noise_sharp.append(e.max() / np.median(e))
noise_top = np.array(noise_top)
noise_sharp = np.array(noise_sharp)
# 噪声的"时间一致性": 相邻独立噪声样本之差
noise_jump = np.abs(np.diff(noise_top))
print("噪声基线: 最强峰均值=%.2f  尖锐度中位=%.2f  相邻跳变中位=%.1f bpm"
      % (noise_top.mean(), np.median(noise_sharp), np.median(noise_jump)))

model, dev = TB.load_model()

print("\n" + "=" * 96)
print("各会话数据质量 (不依赖仪器真值)")
print("=" * 96)
print("%-10s %6s %10s %9s %11s %11s %s" %
      ("会话", "n", "最强峰均值", "尖锐度", "相邻跳变", "vs噪声跳变", "判定"))
print("-" * 96)

good = []
for sess in CANDIDATES:
    cache = CACHE % sess
    if os.path.exists(cache):
        spectra = list(np.load(cache, allow_pickle=True))
    else:
        samples = TB.load_session(sess)
        if len(samples) < 20:
            continue
        spectra = TB.infer_all(model, dev, samples)
        np.save(cache, np.array(spectra, dtype=object))
    ok = [s for s in spectra if s is not None]
    if len(ok) < 20:
        continue
    top = np.array([max(s["peaks"], key=lambda x: x[1])[0] for s in ok])
    sharp = np.array([np.max([x[1] for x in s["peaks"]])
                      / np.median([x[1] for x in s["peaks"]]) for s in ok])
    jump = np.abs(np.diff(top))
    mj = np.median(jump)
    # 判定: 相邻跳变显著小于噪声 => 时间连贯 => 有真信号
    verdict = "有信号 ✅" if mj < np.median(noise_jump) * 0.6 else (
        "弱/无信号 ❌" if mj > np.median(noise_jump) * 0.85 else "边缘 ⚠")
    print("%-10s %6d %10.2f %9.2f %11.1f %11s %s"
          % (sess, len(ok), top.mean(), np.median(sharp), mj,
             "%.2f倍" % (mj / np.median(noise_jump)), verdict))
    if "✅" in verdict:
        good.append(sess)

print("\n" + "=" * 96)
print("结论")
print("=" * 96)
if good:
    print("  含真实脉搏信号的会话: %s" % ", ".join(good))
    print("  >>> 后续策略评估【只能】在这些会话上做。")
else:
    print("  ⚠ 没有任何会话通过时间连贯性检验。")
    print("  >>> 这意味着 frame_capture_diag 里的帧普遍不含可用脉搏信号,")
    print("      问题的根子在【采集端】(帧率塌陷/ROI/光照), 而不在算法。")
