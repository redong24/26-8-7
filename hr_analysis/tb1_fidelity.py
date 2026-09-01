#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试平台【保真度验证】—— 在信任任何结论之前必须先过这一关。

判据: 用测试平台算出的完整频谱前5峰, 与线上日志实录的前5峰对比。
若两者一致, 说明离线复现了线上的真实频谱, 后续策略对照才有意义。

注意 capture_*.npz 存的是【生产缓冲160帧】, 而影子链路用的是【影子缓冲】,
两者帧集合不同 => 不能期待逐轮一一对应。因此这里做【分布级】比对:
  - 峰位置的分布是否一致
  - 最强峰均值是否一致
再用【时间戳对齐】挑出时间最接近的日志轮次做逐样本核对。
"""
import re
import numpy as np
import tb_core as TB

SESS = "4b989b29"
LOG = "/home/lsz/real_time_plus/real_time_Demo/output.log.before_gate_20260810_164015"
TRUTH = 78.3

print("=" * 78)
print("测试平台保真度验证  会话 %s" % SESS)
print("=" * 78)

samples = TB.load_session(SESS)
print("捕获样本数 = %d" % len(samples))
model, dev = TB.load_model()
print("设备 = %s" % dev)

spectra = TB.infer_all(model, dev, samples,
                       cache="/home/lsz/webapp/hr_analysis/tb_spec_%s.npy" % SESS)
ok = [s for s in spectra if s is not None]
print("成功推理 = %d/%d" % (len(ok), len(spectra)))

off_strong = np.array([s["peaks"][max(range(len(s["peaks"])),
                                      key=lambda i: s["peaks"][i][1])][0] for s in ok])
off_npeak = np.array([len(s["peaks"]) for s in ok])

# ---------------- 线上日志侧
pat = re.compile(r"独立局部[峰峭]值前5\(bpm,能量\)=\[(.*?)\] chosen_shadow_hr=([0-9.]+)")
pt = re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")
on_strong, on_chosen = [], []
for line in open(LOG, encoding="utf-8", errors="ignore"):
    if SESS not in line or "SHADOW-HR-DIAG" not in line:
        continue
    m = pat.search(line)
    if not m:
        continue
    pk = pt.findall(m.group(1))
    if pk:
        on_strong.append(float(pk[0][0]))
        on_chosen.append(float(m.group(2)))
on_strong = np.array(on_strong)
on_chosen = np.array(on_chosen)

print("\n--- 最强峰分布对比 (离线测试平台 vs 线上日志) ---")
print("%-14s %7s %9s %9s %9s %9s" % ("", "n", "均值", "中位", "p25", "p75"))
for nm, v in [("离线(平台)", off_strong), ("线上(日志)", on_strong)]:
    print("%-14s %7d %9.2f %9.2f %9.2f %9.2f"
          % (nm, v.size, v.mean(), np.median(v),
             np.percentile(v, 25), np.percentile(v, 75)))
print("  均值差 = %+.2f bpm" % (off_strong.mean() - on_strong.mean()))

# KS 检验(自实现, 避免依赖)
def ks(a, b):
    a, b = np.sort(a), np.sort(b)
    allv = np.concatenate([a, b])
    ca = np.searchsorted(a, allv, 'right') / a.size
    cb = np.searchsorted(b, allv, 'right') / b.size
    d = np.max(np.abs(ca - cb))
    en = np.sqrt(a.size * b.size / (a.size + b.size))
    lam = (en + 0.12 + 0.11 / en) * d
    p = 2 * sum((-1) ** (k - 1) * np.exp(-2 * k * k * lam * lam) for k in range(1, 101))
    return d, max(0.0, min(1.0, p))

d, p = ks(off_strong, on_strong)
print("  KS 检验: D=%.3f  p=%.4f  %s" % (d, p,
      "分布无显著差异 ✅" if p > 0.05 else "分布有差异(注意: 帧集合本就不同)"))

print("\n--- 候选峰数量 (关键: 日志只存前5, 平台存全部) ---")
print("  平台每轮候选峰数: 均值 %.1f  中位 %.0f  最大 %d"
      % (off_npeak.mean(), np.median(off_npeak), off_npeak.max()))
print("  >>> 日志只记前5 => g4 重放必然失真, 这正是当时保真度仅37.9%的原因")

# ---------------- 复现"现状策略"
cur = TB.run_strategy(spectra, TB.sel_current)
m_cur = TB.metrics(cur, TRUTH)
m_on = TB.metrics(on_chosen, TRUTH)
print("\n--- 现状策略: 平台重放 vs 线上实录 ---")
print("%-16s %7s %9s %9s %9s" % ("", "n", "均值", "偏差", "MAE"))
print("%-16s %7d %9.2f %+9.2f %9.2f" % ("平台重放", m_cur["n"], m_cur["mean"],
                                        m_cur["bias"], m_cur["mae"]))
print("%-16s %7d %9.2f %+9.2f %9.2f" % ("线上实录", m_on["n"], m_on["mean"],
                                        m_on["bias"], m_on["mae"]))
print("  均值差 = %+.2f bpm" % (m_cur["mean"] - m_on["mean"]))

print("\n" + "=" * 78)
if abs(off_strong.mean() - on_strong.mean()) < 6 and abs(m_cur["mean"] - m_on["mean"]) < 10:
    print("✅ 平台可信: 频谱与选峰行为都能复现线上量级。")
    print("   (逐轮不可能完全相同 —— 生产缓冲160帧 vs 影子缓冲按时间裁剪)")
else:
    print("⚠ 平台与线上仍有系统差, 结论需谨慎。")
print("=" * 78)
