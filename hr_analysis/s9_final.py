import numpy as np, glob, os, sys, torch, warnings, json
warnings.filterwarnings('ignore')
sys.path.insert(0,"/home/lsz/real_time_plus/real_time_Demo")
os.chdir("/home/lsz/real_time_plus/real_time_Demo")
from scipy import signal as ss
from scipy.interpolate import interp1d
from PhaseNetModel import PhaseNet
dev='cuda' if torch.cuda.is_available() else 'cpu'
net=PhaseNet().to(dev); sd=torch.load('epoch30.pt',map_location=dev); sd=sd.get('state_dict',sd)
net.load_state_dict({k.replace('module.',''):v for k,v in sd.items()},strict=False); net.eval()
LL,UL,FS,W,T_out=50.0,140.0,30.0,10.0,301

def spec(sg):
    b,a=ss.butter(6,[LL/60,UL/60],btype='bandpass',fs=FS)
    f=ss.filtfilt(b,a,np.double(sg))
    F,P=ss.periodogram(x=f,nfft=int(60*FS/0.1),fs=FS,window='hann')
    bpm=F*60; m=(bpm>=LL)&(bpm<=UL)
    return bpm[m],P[m]

fs=sorted(glob.glob("frame_capture_diag/capture_4b989b29_*.npz"))
good=[f for f in fs if 1/np.median(np.diff(np.load(f)['timestamps']))>=10]
sigs=[]
for f in good:
    d=np.load(f); fr=d['frames'].astype(np.float32); ts=d['timestamps']
    msk=ts>=ts[-1]-W
    if msk.sum()<3: continue
    tw,fw=ts[msk],fr[msk]
    if tw[-1]-tw[0]<=1e-3: continue
    nts=np.clip(tw[0]+np.arange(T_out)*(W/(T_out-1)),tw[0],tw[-1])
    rs=np.clip(np.round(interp1d(tw,fw,axis=0,kind='linear',assume_sorted=True)(nts)),0,255).astype(np.uint8)
    x=torch.from_numpy(rs.astype(np.float32)).permute(1,0,2,3).unsqueeze(0).to(dev)
    with torch.no_grad(): out,_=net(x)
    p=out[0].cpu().numpy()
    sigs.append((p-p.mean())/(p.std() if p.std()>1e-6 else 1))
print("有效样本 n=%d"%len(sigs))
np.save('/home/lsz/webapp/hr_analysis/sigs_B.npy',np.array(sigs))

ECG=78.3
am=[];tk=[]
est=75.0
for sg in sigs:
    bb,pp=spec(sg)
    am.append(bb[np.argmax(pp)])
    pk,_=ss.find_peaks(pp,distance=3)
    if len(pk)==0: pk=np.array([int(np.argmax(pp))])
    cand=[(float(bb[i]),float(pp[i])) for i in pk]
    top=max(e for _,e in cand)
    strong=[(b_,e) for b_,e in cand if e>=top*0.15] or cand
    ch,_=min(strong,key=lambda x:abs(x[0]-est))
    tk.append(ch); est=0.25*ch+0.75*est
am=np.array(am);tk=np.array(tk)
ea,et=am-ECG,tk-ECG
print("="*70)
print("逐样本对比: 纯argmax  vs  tracker选峰   (真值%.1f)"%ECG)
print("="*70)
print("%-14s %8s %8s %8s %8s %8s"%("方法","均值","Bias","MAE","RMSE","std"))
for n,e,v in [("纯argmax",ea,am),("tracker",et,tk)]:
    print("%-14s %8.1f %+8.2f %8.2f %8.2f %8.2f"%(n,v.mean(),e.mean(),np.abs(e).mean(),np.sqrt((e**2).mean()),v.std()))
print()
print("±5bpm命中率 : argmax %.0f%%   tracker %.0f%%"%(100*(np.abs(ea)<=5).mean(),100*(np.abs(et)<=5).mean()))
print("±10bpm命中率: argmax %.0f%%   tracker %.0f%%"%(100*(np.abs(ea)<=10).mean(),100*(np.abs(et)<=10).mean()))
print()
win=(np.abs(ea)<np.abs(et)).sum()
print("逐样本胜负: argmax更准 %d 次, tracker更准 %d 次, 平手 %d"%(win,(np.abs(et)<np.abs(ea)).sum(),(np.abs(ea)==np.abs(et)).sum()))
print()
from math import sqrt
d=np.abs(et)-np.abs(ea)
t=d.mean()/(d.std(ddof=1)/sqrt(len(d)))
print("配对检验 |误差|差值: 均值=%+.2f bpm  t=%.2f  n=%d"%(d.mean(),t,len(d)))
print("-> %s"%("argmax显著更优(|t|>2)" if t>2 else "差异不显著"))
print()
print("稳健性: argmax误差分位 p10=%.1f p50=%.1f p90=%.1f"%tuple(np.percentile(ea,[10,50,90])))
print("        tracker误差分位 p10=%.1f p50=%.1f p90=%.1f"%tuple(np.percentile(et,[10,50,90])))
bs=[np.abs(np.random.choice(ea,len(ea))).mean() for _ in range(2000)]
print("argmax MAE 95%%CI = [%.2f, %.2f]"%(np.percentile(bs,2.5),np.percentile(bs,97.5)))
bs2=[np.abs(np.random.choice(et,len(et))).mean() for _ in range(2000)]
print("tracker MAE 95%%CI = [%.2f, %.2f]"%(np.percentile(bs2,2.5),np.percentile(bs2,97.5)))
