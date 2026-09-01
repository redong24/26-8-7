import re,numpy as np
L=open('/home/lsz/real_time_plus/real_time_Demo/output.log',errors='ignore').read()
rows=re.findall(r'shadow_hr=([0-9.]+|nan)\s+shadow_track_est=([0-9.]+)',L)
hr=np.array([float(a) for a,b in rows]); est=np.array([float(b) for a,b in rows])
ok=~np.isnan(hr); hr,est=hr[ok],est[ok]
print("轮次 n=%d  (单一会话, 服务重启后连续运行)"%len(hr))
print()
print("=== est 是否单调爬升后锁死? ===")
print("est: 起=%.1f  末=%.1f  min=%.1f  max=%.1f  均值=%.1f"%(est[0],est[-1],est.min(),est.max(),est.mean()))
k=max(1,len(est)//10)
print("\n分十段看 est 与 hr 的演化:")
print("%-8s %10s %10s %10s"%("段","est均值","hr均值","hr标准差"))
for i in range(10):
    s=slice(i*k,(i+1)*k)
    if len(est[s])==0: continue
    print("%-8s %10.2f %10.2f %10.2f"%("%d/10"%(i+1),est[s].mean(),hr[s].mean(),hr[s].std()))
print()
last=est[-len(est)//3:]
print("后1/3段: est范围[%.1f, %.1f] 极差=%.1f  std=%.2f"%(last.min(),last.max(),last.ptp(),last.std()))
print("-> %s"%("【锁死】est几乎不动"if last.ptp()<15 else "est仍在大范围变化"))
print()
print("=== 输出是否收敛到带中心95? ===")
print("hr 均值=%.2f  中位=%.2f  std=%.2f"%(hr.mean(),np.median(hr),hr.std()))
print("|hr均值 - 95| = %.2f"%abs(hr.mean()-95))
for lo,hi in [(50,70),(70,85),(85,105),(105,125),(125,140)]:
    print("  [%3d,%3d): %5.1f%%"%(lo,hi,100*((hr>=lo)&(hr<hi)).mean()))
print()
print("=== est 与 hr 的耦合(正反馈证据) ===")
print("corr(est[t], hr[t])   = %+.3f"%np.corrcoef(est,hr)[0,1])
print("corr(est[t-1], hr[t]) = %+.3f  <- 上一步est对本次选峰的支配力"%np.corrcoef(est[:-1],hr[1:])[0,1])
