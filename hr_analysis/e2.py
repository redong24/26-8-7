import re,numpy as np
L=open('/home/lsz/real_time_plus/real_time_Demo/output.log',errors='ignore').read()
pat=re.compile(r'独立局部峭值前5\(bpm,能量\)=\[(.*?)\]\s+chosen_shadow_hr=([0-9.]+)\s+shadow_track_est_after_pick=([0-9.]+)')
rows=[]
for cs,ch,est in pat.findall(L):
    c=re.findall(r'\(([0-9.]+),\s*([0-9.]+)\)',cs)
    if len(c)<2: continue
    cand=[(float(a),float(b)) for a,b in c]
    rows.append((cand,float(ch),float(est)))
print("有候选峰的诊断轮次 n=%d"%len(rows))
top1=[];chosen=[];rank=[];engr=[]
for cand,ch,est in rows:
    srt=sorted(cand,key=lambda x:-x[1])
    top1.append(srt[0][0]); chosen.append(ch)
    d=[abs(c[0]-ch) for c in srt]
    k=int(np.argmin(d)); rank.append(k+1)
    engr.append(srt[k][1]/srt[0][1])
top1=np.array(top1);chosen=np.array(chosen);rank=np.array(rank);engr=np.array(engr)
print()
print("=== 被选中的峰, 在能量排序中排第几? ===")
for i in range(1,6):
    print("  第%d强峰被选中: %5.1f%%"%(i,100*(rank==i).mean()))
print("选中最强峰的比例 = %.1f%%"%(100*(rank==1).mean()))
print("被选峰能量/最强峰能量 中位 = %.2f"%np.median(engr))
print()
print("=== 若改用'能量最强峰'会怎样? ===")
print("最强峰   均值=%.2f 中位=%.2f std=%.2f"%(top1.mean(),np.median(top1),top1.std()))
print("实际选中 均值=%.2f 中位=%.2f std=%.2f"%(chosen.mean(),np.median(chosen),chosen.std()))
print()
print("最强峰分布:")
for lo,hi in [(50,70),(70,85),(85,105),(105,125),(125,140)]:
    print("  [%3d,%3d): %5.1f%%"%(lo,hi,100*((top1>=lo)&(top1<hi)).mean()))
print()
print("三名受试者仪器真值: 93.3 / 68.3 / 71.7  (总均值77.8)")
print("最强峰均值=%.1f 偏差=%+.1f | 实际选中均值=%.1f 偏差=%+.1f"
      %(top1.mean(),top1.mean()-77.8,chosen.mean(),chosen.mean()-77.8))
