import numpy as np, glob, os
D="/home/lsz/real_time_plus/real_time_Demo/frame_capture_diag"
fs=sorted(glob.glob(os.path.join(D,"capture_4b989b29_*.npz")))
print("受试者B会话样本数:",len(fs))
d=np.load(fs[60])
print("keys:",list(d.keys()))
fr,ts=d['frames'],d['timestamps']
print("frames shape:",fr.shape,fr.dtype,"min/max:",fr.min(),fr.max())
print("timestamps shape:",ts.shape)
dt=np.diff(ts)
print()
print("=== 帧间隔(时间轴质量, 这直接决定频谱是否可信) ===")
print("dt: mean=%.4fs median=%.4fs std=%.4fs min=%.4f max=%.4f"%(dt.mean(),np.median(dt),dt.std(),dt.min(),dt.max()))
print("等效帧率: mean=%.2f fps  median=%.2f fps"%(1/dt.mean(),1/np.median(dt)))
print("窗口总时长=%.2fs  帧数=%d"%(ts[-1]-ts[0],len(ts)))
print("抖动系数 std/mean = %.1f%%"%(100*dt.std()/dt.mean()))
print("dt分位: p5=%.4f p50=%.4f p95=%.4f p99=%.4f"%tuple(np.percentile(dt,[5,50,95,99])))
big=(dt>2*np.median(dt)).sum()
print("超过2倍中位间隔的'卡顿'帧数 = %d / %d (%.1f%%)"%(big,len(dt),100*big/len(dt)))
