#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解释"平台+4.9 vs 线上+17.0"这 12bpm 的差距 —— 关键变量是【更新密度】。

线上: 8450轮 / ~2000秒 ≈ 4.2 轮/秒。相邻轮的10秒窗口只滑动0.24秒,
      频谱几乎完全相同 => 追踪器在【几乎同一个频谱】上反复迭代。
平台: 120个样本, 间隔8秒, 窗口几乎不重叠 => 相邻样本近似独立。

若"闭环自证"成立, 则在同一频谱上反复迭代会让 est 单调滑向某个自锁点,
而不是收敛到真值。反之若追踪器是健康的, 反复迭代应当稳定在能量最强峰附近。

实验: 把每个捕获样本的频谱重复 R 次喂给追踪器 (R=1,2,5,10,20,50,70),
     观察偏差如何随 R 变化。R≈70 时总轮数≈8400, 与线上量级一致。

这是【决定性实验】: 它用同一批帧、同一个模型, 只改变更新密度。
"""
import numpy as np
import tb_core as TB

SESS = "4b989b29"
TRUTH = 78.3

spectra = list(np.load("/home/lsz/webapp/hr_analysis/tb_spec_%s.npy" % SESS,
                       allow_pickle=True))
ok = [s for s in spectra if s is not None]
print("可用频谱 = %d" % len(ok))


def run_repeat(spectra, R, pick=TB.sel_current, alpha=0.25, init=75.0):
    """每个频谱连续喂 R 轮, 模拟线上高密度更新。"""
    est = init
    out = []
    for sp in spectra:
        if sp is None:
            continue
        for _ in range(R):
            c = pick(sp["peaks"], est)
            if c is None:
                continue
            out.append(c)
            est = alpha * c + (1 - alpha) * est
    return np.array(out, dtype=float)


print("\n" + "=" * 76)
print("更新密度实验: 同一批帧、同一模型, 只改变每个频谱的迭代次数 R")
print("=" * 76)
print("%6s %9s %10s %10s %9s %9s %9s" %
      ("R", "总轮数", "输出均值", "偏差", "MAE", "±5bpm", "est末值"))
print("-" * 76)
for R in (1, 2, 5, 10, 20, 50, 70, 100):
    v = run_repeat(ok, R)
    m = TB.metrics(v, TRUTH)
    # 末态 est
    est = 75.0
    for sp in ok:
        for _ in range(R):
            c = TB.sel_current(sp["peaks"], est)
            if c is not None:
                est = 0.25 * c + 0.75 * est
    print("%6d %9d %10.2f %+10.2f %9.2f %8.1f%% %9.2f"
          % (R, m["n"], m["mean"], m["bias"], m["mae"], m["w5"], est))

print("""
解读: 若偏差随 R 单调上升并逼近线上的 +17, 则证明
      "系统性偏高"是【更新密度 × 闭环反馈】的产物,
      而不是模型、不是重采样、不是帧率。""")

# ---------------------------------------------------------------- 自锁点分析
print("\n" + "=" * 76)
print("单频谱自锁点: 在【一个固定频谱】上无限迭代, est 会停在哪?")
print("=" * 76)
lock, strongest, near_truth = [], [], []
for sp in ok:
    pk = sp["peaks"]
    est = 75.0
    for _ in range(400):          # 迭代至收敛
        c = TB.sel_current(pk, est)
        if c is None:
            break
        est = 0.25 * c + 0.75 * est
    lock.append(est)
    strongest.append(max(pk, key=lambda x: x[1])[0])
    near_truth.append(min(pk, key=lambda x: abs(x[0] - TRUTH))[0])
lock = np.array(lock)
strongest = np.array(strongest)
near_truth = np.array(near_truth)
print("  自锁点均值   = %.2f  (偏差 %+.2f)" % (lock.mean(), lock.mean() - TRUTH))
print("  能量最强峰   = %.2f  (偏差 %+.2f)" % (strongest.mean(), strongest.mean() - TRUTH))
print("  最接近真值峰 = %.2f  (偏差 %+.2f)" % (near_truth.mean(), near_truth.mean() - TRUTH))
print("  自锁点落在能量最强峰上的比例 = %.1f%%"
      % (100.0 * (np.abs(lock - strongest) < 1.0).mean()))
print("  自锁点高于真值的比例         = %.1f%%" % (100.0 * (lock > TRUTH).mean()))
print("""
  >>> 若自锁点普遍不等于能量最强峰, 说明追踪器收敛到的是
      "起点决定的任意峰", 而非"信号最支持的峰" —— 这就是闭环自证。""")

# ---------------------------------------------------------------- 起点依赖
print("\n" + "=" * 76)
print("起点依赖检验: 同一频谱, 不同初始 est, 会收敛到不同答案吗?")
print("=" * 76)
inits = [55, 65, 75, 85, 95, 105, 120, 135]
n_diff = 0
rows = []
for sp in ok[:60]:
    pk = sp["peaks"]
    ends = []
    for i0 in inits:
        est = float(i0)
        for _ in range(400):
            c = TB.sel_current(pk, est)
            if c is None:
                break
            est = 0.25 * c + 0.75 * est
        ends.append(est)
    rows.append(ends)
    if max(ends) - min(ends) > 5:
        n_diff += 1
rows = np.array(rows)
print("  测试频谱数 = %d" % len(rows))
print("  不同起点导致结果相差 >5bpm 的频谱占比 = %.1f%%" % (100.0 * n_diff / len(rows)))
print("  各起点的最终均值:")
for i, i0 in enumerate(inits):
    print("     init=%3d -> 收敛均值 %.2f (偏差 %+.2f)"
          % (i0, rows[:, i].mean(), rows[:, i].mean() - TRUTH))
print("""
  >>> 若"起点越高、收敛越高", 则追踪器根本不是在测量心率,
      而是在【记忆自己的初值】。""")
