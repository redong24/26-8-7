import numpy as np, glob, os, sys, torch, warnings
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

def tracker(pp,bb,est):
    pk,_=ss.find_peaks(pp,distance=3)
    if len(pk)==0: pk=np.array([int(np.argmax(pp))])
    cand=[(float(bb[i]),float(pp[i])) for i in pk]
    top=max(e for _,e in cand)
    strong=[(b_,e) for b_,e in cand if e>=top*0.15] or cand
    ch,_=min(strong,key=lambda x:abs(x[0]-est))
    return ch,0.25*ch+0.75*est

fs=sorted(glob.glob("frame_capture_diag/capture_4b989b29_*.npz"))
rows=[]
for f in fs:
    d=np.load(f); fr=d['frames'].astype(np.float32); ts=d['timestamps']
    fps_med=1/np.median(np.diff(ts))
    msk=ts>=ts[-1]-W
    nreal=int(msk.sum())
    if nreal<3: continue
    tw,fw=ts[msk],fr[msk]
    span=tw[-1]-tw[0]
    if span<=1e-3: continue
    nts=np.clip(tw[0]+np.arange(T_out)*(W/(T_out-1)),tw[0],tw[-1])
    rs=np.clip(np.round(interp1d(tw,fw,axis=0,kind='linear',assume_sorted=True)(nts)),0,255).astype(np.uint8)
    x=torch.from_numpy(rs.astype(np.float32)).permute(1,0,2,3).unsqueeze(0).to(dev)
    with torch.no_grad(): out,_=net(x)
    p=out[0].cpu().numpy()
    sg=(p-p.mean())/(p.std() if p.std()>1e-6 else 1)
    b,a=ss.butter(6,[LL/60,UL/60],btype='bandpass',fs=FS)
    ff=ss.filtfilt(b,a,np.double(sg))
    F,P=ss.periodogram(x=ff,nfft=int(60*FS/0.1),fs=FS,window='hann')
    bpm=F*60; m=(bpm>=LL)&(bpm<=UL)
    rows.append(dict(f=os.path.basename(f),fps=fps_med,nreal=nreal,span=span,bb=bpm[m],pp=P[m]))
print("全部可算样本 n=%d"%len(rows))
np.save('rowsB.npy',np.array([(r['fps'],r['nreal'],r['span']) for r in rows]))

ECG=78.3
def run(sel,label):
    est=75.0; out=[]
    for r in rows:
        if not sel(r): continue
        hr,est=tracker(r['pp'],r['bb'],est)
        out.append(hr)
    o=np.array(out); e=o-ECG
    if len(o)==0: return
    print("%-34s n=%3d  均值=%6.2f  Bias=%+7.2f  MAE=%6.2f  ±10命中=%3.0f%%"
          %(label,len(o),o.mean(),e.mean(),np.abs(e).mean(),100*(np.abs(e)<=10).mean()))
    return o

print("="*84)
print("【关键验证】线上不做质量门控, 会把低帧率窗口一起硬算")
print("="*84)
run(lambda r: r['fps']>=10, "仅高帧率(我之前的离线口径)")
run(lambda r: True,          "全部样本(线上真实口径)")
run(lambda r: r['fps']<10,   "仅低帧率样本")
print()
print("="*84)
print("窗口内真实帧数 / 跨度 的门控效果")
print("="*84)
for k in [30,60,90,120]:
    run(lambda r,k=k: r['nreal']>=k, "门控: 窗口内真实帧>=%d"%k)
print()
for s in [8.0,9.0,9.5]:
    run(lambda r,s=s: r['span']>=s, "门控: 窗口跨度>=%.1fs"%s)
print()
run(lambda r: r['nreal']>=90 and r['span']>=9.0, "门控: 帧>=90 且 跨度>=9.0s")
