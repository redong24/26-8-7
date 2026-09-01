import numpy as np, glob, os
D="/home/lsz/real_time_plus/real_time_Demo/frame_capture_diag"
fs=sorted(glob.glob(os.path.join(D,"capture_4b989b29_*.npz")))
med_dt=[];span=[];idx=[]
for i,f in enumerate(fs):
    ts=np.load(f)['timestamps']; dt=np.diff(ts)
    med_dt.append(np.median(dt)); span.append(ts[-1]-ts[0]); idx.append(i)
med_dt=np.array(med_dt); span=np.array(span)
print("用【中位帧间隔】(抗卡顿离群)评估真实到帧速率, n=%d"%len(fs))
print("中位dt: p10=%.3f p50=%.3f p90=%.3f s"%tuple(np.percentile(med_dt,[10,50,90])))
print("对应fps: p10=%.1f p50=%.1f p90=%.1f"%(1/np.percentile(med_dt,90),1/np.percentile(med_dt,50),1/np.percentile(med_dt,10)))
print()
print("=== 按采集顺序分段(看是否会话前期差、后期好) ===")
for s,e in [(0,30),(30,60),(60,90),(90,120)]:
    seg=med_dt[s:e]
    print("  样本%3d-%3d: 中位fps=%5.1f  窗口跨度中位=%6.1fs"%(s,e,1/np.median(seg),np.median(span[s:e])))
print()
print("=== 160帧缓冲区实际覆盖的真实时长 ===")
print("跨度: p10=%.1fs p50=%.1fs p90=%.1fs  max=%.1fs"%(*np.percentile(span,[10,50,90]),span.max()))
print("注: 这是【模型输入缓冲区】(固定160帧), 非影子缓冲区")
print()
good=(1/med_dt>=10).sum()
print("到帧≥10fps 的样本占比 = %.0f%% (%d/%d)"%(100*good/len(fs),good,len(fs)))
print("到帧< 5fps 的样本占比 = %.0f%%"%(100*(1/med_dt<5).mean()))
