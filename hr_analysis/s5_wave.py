import numpy as np, glob, os, sys, torch
sys.path.insert(0,"/home/lsz/real_time_plus/real_time_Demo")
os.chdir("/home/lsz/real_time_plus/real_time_Demo")
from scipy import signal as ss
from scipy.interpolate import interp1d
from PhaseNetModel import PhaseNet

dev='cuda' if torch.cuda.is_available() else 'cpu'
net=PhaseNet().to(dev)
sd=torch.load('epoch30.pt',map_location=dev)
sd=sd.get('state_dict',sd)
net.load_state_dict({k.replace('module.',''):v for k,v in sd.items()},strict=False)
net.eval()

D="frame_capture_diag"
fs=sorted(glob.glob(os.path.join(D,"capture_4b989b29_*.npz")))
# 只用到帧正常的样本(中位fps>=10)
good=[]
for f in fs:
    ts=np.load(f)['timestamps']
    if 1/np.median(np.diff(ts))>=10: good.append(f)
print("到帧正常样本数 = %d / %d"%(len(good),len(fs)))
print("受试者B的ECG真值区间 = 74~88 bpm (均值78.3)\n")
print("%-6s %8s %8s %8s %8s %8s"%("样本","真实fps","峰1(bpm)","能量","峰2(bpm)","峰1/峰2"))
print("-"*58)
res=[]
for f in good[::6][:12]:
    d=np.load(f); fr=d['frames'].astype(np.float32); ts=d['timestamps']
    x=torch.from_numpy(fr).unsqueeze(0).to(dev)  # 1,T,3,72,72 ?
    if x.shape[2]!=3: x=x.permute(0,1,2,3,4)
    with torch.no_grad():
        out=net(x)
    sig=(out[0] if isinstance(out,(tuple,list)) else out).squeeze().float().cpu().numpy()
    fps_real=1/np.median(np.diff(ts))
    # 在真实时间轴上均匀重采样(用真实fps, 不假装30)
    t=ts-ts[0]
    n=len(sig)
    tt=np.linspace(t[0],t[min(n,len(t))-1],n)
    fsr=(n-1)/(tt[-1]-tt[0])
    s=sig-sig.mean()
    b,a=ss.butter(4,[0.7/(fsr/2),3.0/(fsr/2)],btype='band')
    s=ss.filtfilt(b,a,s)
    fq,px=ss.periodogram(s,fs=fsr,window='hann',nfft=4096)
    bpm=fq*60; m=(bpm>=45)&(bpm<=150)
    bb,pp=bpm[m],px[m]
    pk,_=ss.find_peaks(pp)
    if len(pk)<2: continue
    o=pk[np.argsort(pp[pk])[::-1]]
    p1,p2=bb[o[0]],bb[o[1]]
    ratio=pp[o[0]]/pp[o[1]]
    res.append((p1,ratio,fps_real))
    print("%-6s %8.1f %8.1f %8.3f %8.1f %8.2f"%(os.path.basename(f)[16:19],fps_real,p1,pp[o[0]],p2,ratio))
r=np.array(res)
print("-"*58)
print("峰1均值=%.1f bpm  中位=%.1f  std=%.1f"%(r[:,0].mean(),np.median(r[:,0]),r[:,0].std()))
print("ECG真值均值=78.3 -> 偏差=%+.1f bpm"%(r[:,0].mean()-78.3))
print("峰1/峰2 中位=%.2f  (>2 表示主峰清晰; <1.5 表示无法分辨)"%np.median(r[:,1]))
