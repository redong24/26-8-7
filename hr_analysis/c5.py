import numpy as np
S={'1号':(np.array([84,101,94.]),np.array([92,94,94.])),
   '2号':(np.array([108,91,102.]),np.array([71,70,64.])),
   '3号':(np.array([86,82,93.]),np.array([66,68,81.]))}
m=np.concatenate([v[0] for v in S.values()]); d=np.concatenate([v[1] for v in S.values()])
e=m-d
print("="*72); print("总体 (n=%d)"%len(m)); print("="*72)
print("Bias=%+.2f  MAE=%.2f  RMSE=%.2f  误差std=%.2f"%(e.mean(),np.abs(e).mean(),np.sqrt((e**2).mean()),e.std(ddof=1)))
print("偏高占比=%.0f%%   ±5bpm命中=%.0f%%   ±10bpm命中=%.0f%%"%(100*(e>0).mean(),100*(np.abs(e)<=5).mean(),100*(np.abs(e)<=10).mean()))
print()
print("%-6s %8s %8s %9s %8s"%("受试者","仪器均值","模型均值","Bias","|模型-95|"))
print("-"*72)
for k,(mm,dd) in S.items():
    print("%-6s %8.1f %8.1f %+9.1f %8.1f"%(k,dd.mean(),mm.mean(),(mm-dd).mean(),abs(mm.mean()-95)))
print()
print("="*72); print("判别检验: 模型跟随真值 还是 锁在带中心95?"); print("="*72)
tv=np.array([v[1].mean() for v in S.values()]); mv=np.array([v[0].mean() for v in S.values()])
print("仪器均值跨人极差 = %.1f bpm  (93.3 / 68.3 / 71.7)"%tv.ptp())
print("模型均值跨人极差 = %.1f bpm  (%.1f / %.1f / %.1f)"%(mv.ptp(),*mv))
print("跨人真值方差被模型还原的比例 = %.0f%%"%(100*mv.ptp()/tv.ptp()))
print()
r=np.corrcoef(m,d)[0,1]
b,a=np.polyfit(d,m,1)
print("逐点相关 r = %+.3f   回归斜率 = %+.2f  (理想 r=+1, 斜率=+1)"%(r,b))
rr=np.corrcoef(mv,tv)[0,1]
print("按人聚合 r = %+.3f  (n=3)"%rr)
print()
lam=np.mean([(mv[i]-tv[i])/(95.0-tv[i]) for i in range(3) if abs(95.0-tv[i])>3])
print("拟合 m=(1-λ)·真值+λ·95   λ=%.2f -> 输出约%.0f%%由带中心决定"%(lam,100*lam))
