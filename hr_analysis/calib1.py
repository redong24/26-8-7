import numpy as np
m = np.array([90,107,106,88,85,101], float)   # 模型
d = np.array([99, 99, 97,97,97, 98], float)   # 设备(真值)
e = m - d
print("n =", len(m))
print("设备: mean=%.2f  range=[%.0f,%.0f] 极差=%.0f  std=%.2f" % (d.mean(), d.min(), d.max(), d.ptp(), d.std(ddof=1)))
print("模型: mean=%.2f  range=[%.0f,%.0f] 极差=%.0f  std=%.2f" % (m.mean(), m.min(), m.max(), m.ptp(), m.std(ddof=1)))
print()
print("逐点误差:", ", ".join("%+d"%x for x in e))
print("Bias(平均误差) = %+.2f bpm" % e.mean())
print("MAE  = %.2f bpm" % np.abs(e).mean())
print("RMSE = %.2f bpm" % np.sqrt((e**2).mean()))
print("误差std = %.2f bpm" % e.std(ddof=1))
print("MAE<=5 命中率 = %.0f%%" % (100*(np.abs(e)<=5).mean()))
print()
r = np.corrcoef(m, d)[0,1]
print("相关系数 r = %.3f  (设备极差仅%.0fbpm, n=6 -> 无统计意义)" % (r, d.ptp()))
print()
print("=== 关键对照 ===")
print("新搜索带 [50,140] 中心      = %.1f bpm" % ((50+140)/2))
print("本次受试者真值均值          = %.2f bpm" % d.mean())
print("模型输出均值                = %.2f bpm" % m.mean())
print("模型均值 - 带中心           = %+.2f bpm" % (m.mean()-95.0))
