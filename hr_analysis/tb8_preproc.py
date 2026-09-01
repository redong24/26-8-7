#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预处理消融实验: 线上喂给模型的张量是否与训练时一致?

发现的可疑点(读 test2.py 与 train_PURE_utils.py 得到):
  test2.py:  face_roi_bgr = frame[...]                      <- OpenCV BGR
             face_tensor_chw = torch.from_numpy(resized).permute(2,0,1).float()
             => 通道顺序 B,G,R, 数值 0~255 原始值
  训练:      frames = torch.tensor(numpy数据.astype(np.float32))
             归一化那行被注释掉 => 也是 0~255 原始值 ✅ 数值范围一致
             但 PURE numpy 数据集通常是 RGB => 通道顺序可能不一致 ❌

若模型训练时是 RGB, 线上喂 BGR, 则 R 与 B 通道互换。
rPPG 严重依赖各通道血液吸收差异, 通道错位会显著削弱脉搏提取。

评价指标(不依赖仪器真值):
  谱尖锐度 = 最强峰能量/中位能量  (越高=脉搏成分越突出)
  时间连贯 = 相邻样本最强峰跳变   (越小=越像真心率)
"""
import os
import glob
import numpy as np
import torch
import tb_core as TB

SESS = "4b989b29"
N_USE = 60          # 用前60个样本, 兼顾速度

files = sorted(glob.glob(os.path.join(TB.CAPTURE_DIR, "*%s*.npz" % SESS)))[:N_USE]
print("样本数 = %d" % len(files))
model, dev = TB.load_model()


def infer(frames_u8):
    """frames_u8: (T,C,H,W) uint8 -> rppg 波形"""
    x = torch.from_numpy(frames_u8.astype(np.float32))
    x = x.permute(1, 0, 2, 3).unsqueeze(0).to(dev)
    with torch.no_grad():
        out, _ = model(x)
    return out[0].detach().cpu().numpy()


VARIANTS = {
    "原样(BGR,0-255)": lambda f: f,
    "通道翻转(RGB)": lambda f: f[:, ::-1, :, :].copy(),
    "仅绿通道复制3份": lambda f: np.repeat(f[:, 1:2], 3, axis=1),
    "逐帧标准化*255": None,       # 特殊处理
    "除以255": None,              # 特殊处理
}

results = {}
for name in VARIANTS:
    results[name] = {"top": [], "sharp": []}

for i, fp in enumerate(files):
    d = np.load(fp)
    fr, ts = d["frames"], d["timestamps"]
    rs, T = TB.resample_window(fr, ts)
    if rs is None:
        continue
    for name, fn in VARIANTS.items():
        if name == "逐帧标准化*255":
            x = rs.astype(np.float32)
            x = (x - x.mean()) / (x.std() + 1e-9) * 255.0
            pts = infer(x)
        elif name == "除以255":
            pts = infer(rs.astype(np.float32) / 255.0)
        else:
            pts = infer(fn(rs))
        sp = TB.spectrum(pts)
        if sp is None:
            continue
        pk = sp["peaks"]
        e = np.array([p[1] for p in pk])
        results[name]["top"].append(max(pk, key=lambda x: x[1])[0])
        results[name]["sharp"].append(e.max() / np.median(e))
    if (i + 1) % 20 == 0:
        print("  %d/%d" % (i + 1, len(files)), flush=True)

# 噪声基线
rng = np.random.default_rng(11)
ns, nt = [], []
for _ in range(400):
    sp = TB.spectrum(rng.standard_normal(301))
    if sp:
        e = np.array([p[1] for p in sp["peaks"]])
        ns.append(e.max() / np.median(e))
        nt.append(max(sp["peaks"], key=lambda x: x[1])[0])
noise_sharp = float(np.median(ns))
noise_jump = float(np.median(np.abs(np.diff(nt))))

print("\n" + "=" * 86)
print("预处理消融  (噪声基线: 尖锐度 %.2f, 相邻跳变 %.1f bpm)" % (noise_sharp, noise_jump))
print("=" * 86)
print("%-22s %7s %11s %11s %12s %10s" %
      ("变体", "n", "最强峰均值", "尖锐度中位", "相邻跳变", "vs噪声"))
print("-" * 86)
rows = []
for name in VARIANTS:
    t = np.array(results[name]["top"], float)
    s = np.array(results[name]["sharp"], float)
    if t.size < 5:
        continue
    jump = np.median(np.abs(np.diff(t)))
    score = (np.median(s) / noise_sharp) * (noise_jump / max(jump, 1e-6))
    rows.append((score, name, t, s, jump))
    print("%-22s %7d %11.2f %11.2f %12.1f %10.2f"
          % (name, t.size, t.mean(), np.median(s), jump, score))

print("\n(vs噪声 = 尖锐度提升 × 连贯性提升, >1.3 才算真的看到脉搏)")
rows.sort(reverse=True)
print("\n最佳变体: %s  (score=%.2f)" % (rows[0][1], rows[0][0]))
if rows[0][0] < 1.3:
    print("\n⚠ 所有变体都接近噪声水平 => 预处理不是主因, 或帧本身无脉搏。")
else:
    print("\n✅ %s 明显优于其他 => 预处理确实存在问题。" % rows[0][1])
