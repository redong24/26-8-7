import numpy as np
m1=np.array([84,101,94],float); d1=np.array([92,94,94],float)   # 1号
m2=np.array([108],float);       d2=np.array([71],float)          # 2号
print("="*70)
print("门控上线后 ECG 对照 (n=4)")
print("="*70)
print("1号受试者 (静息, 真值92~94):")
for a,b in zip(m1,d1): print("   模型%6.1f  仪器%6.1f  误差%+6.1f"%(a,b,a-b))
e1=m1-d1
print("   Bias=%+.2f  MAE=%.2f  ±5命中=%.0f%%"%(e1.mean(),np.abs(e1).mean(),100*(np.abs(e1)<=5).mean()))
print()
print("2号受试者 (真值71 = 低心率):")
for a,b in zip(m2,d2): print("   模型%6.1f  仪器%6.1f  误差%+6.1f"%(a,b,a-b))
print()
print("="*70)
print("【关键】2号这一组是判别性样本")
print("="*70)
C=95.0
print("搜索带[50,140]中心 C = %.1f"%C)
print("2号真值 = 71.0, 离带中心 %.1f bpm  -> 可判别"%abs(71-C))
print("2号模型输出 = 108.0")
print()
print("  假设'模型准'      预测 ≈ 71   实测108  差 %+.0f"%(108-71))
print("  假设'回归带中心'  预测 ≈ 95   实测108  差 %+.0f"%(108-95))
print("  -> 都不吻合。108甚至【高于】带中心13bpm")
print()
print("="*70)
print("汇总: 全部真人ECG对照数据 (跨3名受试者)")
print("="*70)
allm=np.concatenate([[90,107,106,88,85,101],
                     [88,88,102,111,101,94,104,81,86,77,86,107,104,106,112,105],
                     m1,m2])
alld=np.concatenate([[99,99,97,97,97,98],
                     [79,79,77,75,74,81,82,84,75,76,88,78,74,74,78,79],
                     d1,d2])
e=allm-alld
print("n=%d  Bias=%+.2f  MAE=%.2f  RMSE=%.2f  ±5命中=%.0f%%  ±10命中=%.0f%%"
      %(len(e),e.mean(),np.abs(e).mean(),np.sqrt((e**2).mean()),
        100*(np.abs(e)<=5).mean(),100*(np.abs(e)<=10).mean()))
print("误差为正(偏高)占比 = %.0f%%"%(100*(e>0).mean()))
r=np.corrcoef(allm,alld)[0,1]
print("相关系数 r = %+.3f  (真值范围%.0f~%.0f)"%(r,alld.min(),alld.max()))
b,a=np.polyfit(alld,allm,1)
print("回归 模型 = %.2f×真值 + %.1f   (理想应为 1.00×真值 + 0)"%(b,a))
