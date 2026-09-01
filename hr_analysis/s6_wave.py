import numpy as np, glob, os, sys, torch, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0,"/home/lsz/real_time_plus/real_time_Demo")
os.chdir("/home/lsz/real_time_plus/real_time_Demo")
from scipy import signal as ss
from PhaseNetModel import PhaseNet
dev='cuda' if torch.cuda.is_available() else 'cpu'
net=PhaseNet().to(dev)
sd=torch.load('epoch30.pt',map_location=dev); sd=sd.get('state_dict',sd)
net.load_state_dict({k.replace('module.',''):v for k,v in sd.items()},strict=False); net.eval()

fs=sorted(glob.glob("frame_capture_diag/capture_4b989b29_*.npz"))
good=[f for f in fs if 1/np.median(np.diff(np.load(f)['timestamps']))>=10]
ECG=78.3
res=[]
for f in good:
    d=np.load(f); fr=d['frames'].astype(np.float32); ts=d['timestamps']
    with torch.no_grad():
        out=net(torch.from_numpy(fr).unsqueeze(0).to(dev))
    sig=(out[0] if isinstance(out,(tuple,list)) else out).squeeze().float().cpu().numpy()
    n=len(sig); t=ts[:n]-ts[0]
    fsr=(n-1)/(t[-1]-t[0])
    if not (0.1 < 3.0/(fsr/2) < 1.0): continue
    s=ss.detrend(sig)
    b,a=ss.butter(4,[0.7/(fsr/2),3.0/(fsr/2)],btype='band')
    s=ss.filtfilt(b,a,s)
    fq,px=ss.periodogram(s,fs=fsr,window='hann',nfft=8192)
    bpm=fq*60; m=(bpm>=45)&(bpm<=150); bb,pp=bpm[m],px[m]
    pk,_=ss.find_peaks(pp)
    if len(pk)<2: continue
    o=pk[np.argsort(pp[pk])[::-1]]
    res.append((bb[o[0]], pp[o[0]]/pp[o[1]], fsr, np.abs(bb[o[:3]]-ECG).min()))
r=np.array(res)
print("="*66)
print("【决定性结果】PhaseNet 输出频谱主峰 vs ECG真值")
print("="*66)
print("有效样本 n=%d  (到帧>=10fps)"%len(r))
print("ECG真值均值 = %.1f bpm  (区间74~88)"%ECG)
print()
print("主峰均值   = %.1f bpm   偏差 = %+.1f"%(r[:,0].mean(),r[:,0].mean()-ECG))
print("主峰中位   = %.1f bpm"%np.median(r[:,0]))
print("主峰std    = %.1f bpm"%r[:,0].std())
print("主峰落在[70,90]的比例 = %.0f%%"%(100*((r[:,0]>=70)&(r[:,0]<=90)).mean()))
print("主峰误差<=5bpm 比例   = %.0f%%"%(100*(np.abs(r[:,0]-ECG)<=5).mean()))
print()
print("前3峰中存在<=5bpm命中的比例 = %.0f%%   <-- 信号是否存在的关键"%(100*(r[:,3]<=5).mean()))
print("峰1/峰2能量比 中位 = %.2f"%np.median(r[:,1]))
print()
print("分布:")
for lo,hi in [(45,60),(60,70),(70,80),(80,90),(90,110),(110,150)]:
    c=((r[:,0]>=lo)&(r[:,0]<hi)).sum()
    print("  [%3d,%3d): %2d  %s"%(lo,hi,c,"#"*c))
print()
print("对照: 线上影子链路输出均值 97.0 bpm (偏差+18.7)")
print("      本次直算主峰均值 %.1f bpm (偏差%+.1f)"%(r[:,0].mean(),r[:,0].mean()-ECG))
