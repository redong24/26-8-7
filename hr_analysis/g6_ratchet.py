#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【无重放·直接测量】追踪器是否存在"棘轮效应"(单向自我强化)?

g3 发现的不对称:
    est <= 中位数 :  chosen - strongest =  -8.58
    est >  中位数 :  chosen - strongest = +25.51
向上拉的力度是向下的 3 倍。若这不对称是【追踪器造成】而非【信号本身造成】,
则必须满足: 高est组与低est组的【最强峰分布基本相同】(信号相同),
只是 chosen 不同(选择不同)。这是可以直接测的, 不需要任何重放。

同时测:
  - 候选集规模: 能量阈值0.15 放进来多少个候选(候选越多越容易被噪声牵走)
  - 停留时间: est 处于高位的连续时长(棘轮 = 上去就下不来)
  - 不对称的物理来源: 真值78 在带[50,140]内, 下方仅28bpm空间, 上方62bpm

纯读日志。
"""
import re
import numpy as np

LOG = "/home/lsz/real_time_plus/real_time_Demo/output.log.before_gate_20260810_164015"
SESS = "4b989b29"
TRUTH = 78.3
LL, UL = 50.0, 140.0
THRESH = 0.15

pat = re.compile(
    r"独立局部[峰峭]值前5\(bpm,能量\)=\[(.*?)\] "
    r"chosen_shadow_hr=([0-9.]+) "
    r"shadow_track_est_after_pick=([0-9.]+)")
pt = re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")

cand, chosen_l, est_l = [], [], []
with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if SESS not in line or "SHADOW-HR-DIAG" not in line:
            continue
        m = pat.search(line)
        if not m:
            continue
        pk = [(float(a), float(b)) for a, b in pt.findall(m.group(1))]
        if pk:
            cand.append(pk)
            chosen_l.append(float(m.group(2)))
            est_l.append(float(m.group(3)))

chosen = np.array(chosen_l)
est = np.array(est_l)
strongest = np.array([p[0][0] for p in cand])
n = len(cand)
# est_before: 本轮选峰所依据的状态 = 上一轮 after_pick
est_before = np.concatenate([[75.0], est[:-1]])

print("=" * 80)
print("棘轮效应检验  会话%s  n=%d  真值%.1f  带[%.0f,%.0f]" % (SESS, n, TRUTH, LL, UL))
print("=" * 80)

# ---------------------------------------------- 1. 信号是否相同?
med = np.median(est_before)
lo = est_before <= med
hi = est_before > med
print("\n[1] 关键检验: 高/低 est 两组的【信号(最强峰)】是否相同?")
print("    低est组 (est<=%.1f, n=%d): 最强峰均值 = %.2f" % (med, lo.sum(), strongest[lo].mean()))
print("    高est组 (est> %.1f, n=%d): 最强峰均值 = %.2f" % (med, hi.sum(), strongest[hi].mean()))
print("    信号差异 = %+.2f bpm" % (strongest[hi].mean() - strongest[lo].mean()))
print("    低est组 chosen 均值 = %.2f" % chosen[lo].mean())
print("    高est组 chosen 均值 = %.2f" % chosen[hi].mean())
print("    输出差异 = %+.2f bpm" % (chosen[hi].mean() - chosen[lo].mean()))
sig_d = strongest[hi].mean() - strongest[lo].mean()
out_d = chosen[hi].mean() - chosen[lo].mean()
print("    >>> 输出差异 / 信号差异 = %.1f 倍" % (out_d / sig_d if sig_d else float('nan')))
print("        信号只差 %.1f, 输出却差 %.1f => 差额 %.1f bpm 由【追踪器】制造"
      % (sig_d, out_d, out_d - sig_d))

# ---------------------------------------------- 2. 候选集规模
sizes, spans = [], []
for pk in cand:
    top = max(e for _, e in pk)
    s = [b for b, e in pk if e >= top * THRESH]
    sizes.append(len(s))
    spans.append(max(s) - min(s) if len(s) > 1 else 0.0)
sizes = np.array(sizes)
spans = np.array(spans)
print("\n[2] 能量阈值 %.2f 放行的候选数量 (仅统计日志所存前5峰, 真实更多)" % THRESH)
print("    平均候选数 = %.2f / 5   (>=3个的占比 %.1f%%)"
      % (sizes.mean(), 100.0 * (sizes >= 3).mean()))
print("    候选跨度(最高-最低) 均值 = %.1f bpm, 中位数 = %.1f bpm"
      % (spans.mean(), np.median(spans)))
print("    >>> 候选跨度越宽, 追踪器越能'想选哪个选哪个', est 就越能自由漂移")

# ---------------------------------------------- 3. 不对称空间
print("\n[3] 不对称的结构性来源")
print("    真值 %.1f 在带 [%.0f,%.0f] 内:" % (TRUTH, LL, UL))
print("       下方可漂移空间 = %.1f bpm" % (TRUTH - LL))
print("       上方可漂移空间 = %.1f bpm  (是下方的 %.2f 倍)"
      % (UL - TRUTH, (UL - TRUTH) / (TRUTH - LL)))
print("    实测 est 分布: p5=%.1f  p50=%.1f  p95=%.1f"
      % tuple(np.percentile(est, [5, 50, 95])))
print("    est 低于真值的比例 = %.1f%% | 高于真值 = %.1f%%"
      % (100.0 * (est < TRUTH).mean(), 100.0 * (est > TRUTH).mean()))

# ---------------------------------------------- 4. 停留时间(棘轮)
high = est > TRUTH + 10
runs, cur = [], 0
for v in high:
    if v:
        cur += 1
    elif cur:
        runs.append(cur)
        cur = 0
if cur:
    runs.append(cur)
low = est < TRUTH - 10
runs_l, cur = [], 0
for v in low:
    if v:
        cur += 1
    elif cur:
        runs_l.append(cur)
        cur = 0
if cur:
    runs_l.append(cur)
print("\n[4] 停留时间: est 偏离真值 >10bpm 的连续轮次 (棘轮=上去下不来)")
if runs:
    print("    偏高段: %d 段, 平均 %.1f 轮, 最长 %d 轮, 总占比 %.1f%%"
          % (len(runs), np.mean(runs), max(runs), 100.0 * high.mean()))
if runs_l:
    print("    偏低段: %d 段, 平均 %.1f 轮, 最长 %d 轮, 总占比 %.1f%%"
          % (len(runs_l), np.mean(runs_l), max(runs_l), 100.0 * low.mean()))
else:
    print("    偏低段: 无")
print("    >>> 偏高停留时间是偏低的 %.1f 倍"
      % (np.mean(runs) / np.mean(runs_l) if runs_l and runs else float('inf')))

# ---------------------------------------------- 5. 收敛性
print("\n[5] est 是否收敛到真值? (分10段看轨迹)")
q = n // 10
for i in range(10):
    seg = est[i * q:(i + 1) * q]
    sg = strongest[i * q:(i + 1) * q]
    bar = "#" * int(round((seg.mean() - 50) / 3))
    print("    第%2d段: est=%6.2f  最强峰=%6.2f  %s" % (i + 1, seg.mean(), sg.mean(), bar))
