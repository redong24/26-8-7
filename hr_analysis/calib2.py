import numpy as np
m=np.array([90,107,106,88,85,101],float); d=np.array([99,99,97,97,97,98],float)
C=95.0  # 新搜索带中心
print("=== 判别性检验: 模型是真准, 还是仍在回归带中心? ===")
print("带中心 C            = %.1f" % C)
print("真值均值 T          = %.2f" % d.mean())
print("|T - C|             = %.2f bpm  <-- 两个假设的可分辨距离" % abs(d.mean()-C))
print("模型输出std         = %.2f bpm  <-- 单次测量噪声" % m.std(ddof=1))
print()
print("要区分'准'与'回归带中心', 真值须远离带中心。")
print("按 |T-C| > 2*sigma/sqrt(n) 粗算, n=6 需 |T-C| > %.1f bpm" % (2*m.std(ddof=1)/np.sqrt(6)))
print("本次 |T-C|=%.2f  ->  【无法判别】" % abs(d.mean()-C))
print()
print("=== 误差结构分解 ===")
e=m-d
print("Bias  = %+.2f bpm  (偏高问题: 已消除)" % e.mean())
print("Std   =  %.2f bpm  (抖动: 现在是误差主成分)" % e.std(ddof=1))
print("MAE中由bias贡献 = %.0f%% , 由抖动贡献 = %.0f%%"
      % (100*abs(e.mean())/np.abs(e).mean(), 100*(1-abs(e.mean())/np.abs(e).mean())))
print()
print("=== 抖动 vs 频率分辨率 ===")
res=60/10.0
print("10s窗分辨率 = %.1f bpm/bin" % res)
print("逐点误差(bin) :", ", ".join("%+.1f"%(x/res) for x in e))
print("-> 误差达1.5~2个bin, 超出分辨率极限, 说明是【选峰不稳】而非分辨率不足")
print()
print("=== 若加时域平滑(对6点滑动中值/均值的事后模拟) ===")
for k in [3,5]:
    sm=np.convolve(m,np.ones(k)/k,mode='valid'); dd=d[k-1:]
    ee=sm-dd
    print("  滑窗均值k=%d: MAE=%.2f (原8.33)  std=%.2f" % (k,np.abs(ee).mean(),ee.std(ddof=1) if len(ee)>1 else 0))
med=np.array([np.median(m[max(0,i-2):i+1]) for i in range(len(m))])
print("  滑动中值k=3: MAE=%.2f" % np.abs(med-d).mean())
