#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【无重放·直接测量】机制确认: 追踪器是不是在"照抄自己"?

g6 已证明:
  - 能量阈值0.15 放行的候选跨度中位数 88.5 bpm ≈ 整个搜索带[50,140]的宽度90
  - 高/低 est 两组信号只差 2.8, 输出却差 31.2

推论: 当候选几乎铺满整个频带时,
      chosen = min(strong, key=|bpm - prev_est|)  ≈  prev_est
      即"选出来的心率"约等于"上一次的估计", 能量信息几乎不参与决策。
      再经 est = 0.25*chosen + 0.75*est 反馈, 形成闭环自证。

本脚本直接量化这个闭环, 不做任何重放、不改代码。
"""
import re
import numpy as np

LOG = "/home/lsz/real_time_plus/real_time_Demo/output.log.before_gate_20260810_164015"
SESS = "4b989b29"
TRUTH = 78.3
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
est_after = np.array(est_l)
n = len(cand)
strongest = np.array([p[0][0] for p in cand])
# 本轮决策依据 = 上一轮 after_pick; 由 est=0.25c+0.75e 反解更精确:
est_before = (est_after - 0.25 * chosen) / 0.75

print("=" * 80)
print("机制确认: 追踪器闭环自证  n=%d  真值%.1f" % (n, TRUTH))
print("=" * 80)

# ---- 1. chosen 与谁更相关
def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1])

print("\n[1] chosen 到底由什么决定?")
print("    corr(chosen, est_before) = %+.3f   <- 上一次的自己" % corr(chosen, est_before))
print("    corr(chosen, strongest)  = %+.3f   <- 真正的能量最强峰" % corr(chosen, strongest))
print("    |chosen - est_before| 中位数 = %.2f bpm" % np.median(np.abs(chosen - est_before)))
print("    |chosen - strongest|  中位数 = %.2f bpm" % np.median(np.abs(chosen - strongest)))

# ---- 2. chosen 的能量排名
ranks = []
for pk, c in zip(cand, chosen):
    order = sorted(range(len(pk)), key=lambda i: -pk[i][1])   # 按能量降序
    r = None
    for rank, i in enumerate(order, 1):
        if abs(pk[i][0] - c) < 0.05:
            r = rank
            break
    ranks.append(r if r else 0)
ranks = np.array(ranks)
valid = ranks > 0
print("\n[2] 被选中的峰, 其【能量排名】分布 (1=能量最强)")
for r in range(1, 6):
    print("    第%d名: %5.1f%%" % (r, 100.0 * (ranks == r).mean()))
print("    不在前5(日志未记全): %.1f%%" % (100.0 * (~valid).mean()))
print("    >>> 只有 %.1f%% 的时候选了能量最强峰, 能量基本被忽略"
      % (100.0 * (ranks == 1).mean()))

# ---- 3. 候选跨度 vs 带宽
spans, sizes = [], []
for pk in cand:
    top = max(e for _, e in pk)
    s = [b for b, e in pk if e >= top * THRESH]
    sizes.append(len(s))
    spans.append(max(s) - min(s) if len(s) > 1 else 0.0)
spans = np.array(spans)
print("\n[3] 能量阈值形同虚设的程度")
print("    搜索带宽 = 90.0 bpm ([50,140])")
print("    候选跨度中位数 = %.1f bpm  (占带宽 %.0f%%)"
      % (np.median(spans), 100.0 * np.median(spans) / 90.0))
print("    跨度>60bpm 的轮次占比 = %.1f%%" % (100.0 * (spans > 60).mean()))
print("    >>> 候选几乎铺满整个频带 => min|bpm-est| 基本总能找到贴着 est 的峰")

# ---- 4. 闭环增益
print("\n[4] 闭环自证的量化")
follow = np.abs(chosen - est_before) < np.abs(chosen - strongest)
print("    chosen 更贴近 est_before 而非最强峰的比例 = %.1f%%" % (100.0 * follow.mean()))
drift = est_after - est_before
print("    单轮 est 变化量: 均值 %+.3f, |变化| 中位数 %.3f bpm"
      % (drift.mean(), np.median(np.abs(drift))))
print("    >>> est 每轮几乎不动(惯性极大), 但没有任何机制把它拉回真值")

# ---- 5. 真值附近有没有峰可选? (信号其实是好的)
near = []
for pk in cand:
    near.append(any(abs(b - TRUTH) <= 5 for b, _ in pk))
near = np.array(near)
print("\n[5] 关键: 真值附近到底有没有候选峰?")
print("    前5峰中存在 |bpm-%.1f|<=5 的轮次 = %.1f%%" % (TRUTH, 100.0 * near.mean()))
sel_near = np.abs(chosen - TRUTH) <= 5
print("    其中真的选中了它的 = %.1f%%"
      % (100.0 * (sel_near & near).sum() / max(near.sum(), 1)))
print("    >>> 信号里【有】正确答案, 但追踪器在 %.1f%% 的情况下没选它"
      % (100.0 * (near & ~sel_near).sum() / max(near.sum(), 1)))

print("\n" + "=" * 80)
print("结论")
print("=" * 80)
print("""  能量阈值0.15太松 -> 候选铺满整个频带 -> min|bpm-prev_est| 退化为
  "选一个最像上次的" -> est 反馈自己 -> 闭环自证。
  真值附近 %.0f%% 的轮次都有正确的峰, 但系统选不中它。
  由于真值78.3 在带[50,140]中偏下(上方空间是下方的2.18倍),
  这个自由漂移的闭环在统计上更容易停在高位 => 系统性偏高。""" % (100.0 * near.mean()))
