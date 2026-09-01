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

def compute_hr(sig,FS,LL,UL,prev,thr=0.15,alpha=0.25,order=6):
    b,a=ss.butter(order,[LL/60,UL/60],btype='bandpass',fs=FS)
    f=ss.filtfilt(b,a,np.double(sig))
    N=int((60*FS)/0.1)
    F,P=ss.periodogram(x=f,nfft=N,fs=FS,window='hann')
    bpm=F*60; m=(bpm>=LL)&(bpm<=UL); br,pr=bpm[m],P[m]
    pk,_=ss.find_peaks(pr,distance=3)
    if len(pk)==0: pk=np.array([int(np.argmax(pr))])
    cand=[(float(br[i]),float(pr[i])) for i in pk]
    top=max(e for _,e in cand)
    strong=[(b_,e) for b_,e in cand if e>=top*thr] or cand
    ch,_=min(strong,key=lambda x:abs(x[0]-prev))
    return ch, alpha*ch+(1-alpha)*prev, cand, strong

fs=sorted(glob.glob("frame_capture_diag/capture_4b989b29_*.npz"))
good=[f for f in fs if 1/np.median(np.diff(np.load(f)['timestamps']))>=10]
ECG=78.3; W=10.0; T_out=301
est_shadow=75.0
rows=[]
for f in good:
    d=np.load(f); fr=d['frames'].astype(np.float32); ts=d['timestamps']
    te=ts[-1]; msk=ts>=te-W
    if msk.sum()<3: continue
    tw=ts[msk]; fw=fr[msk]
    if tw[-1]-tw[0]<=1e-3: continue
    nts=tw[0]+np.arange(T_out)*(W/(T_out-1)); nts=np.clip(nts,tw[0],tw[-1])
    rs=interp1d(tw,fw,axis=0,kind='linear',assume_sorted=True)(nts)
    rs=np.clip(np.round(rs),0,255).astype(np.uint8)
    x=torch.from_numpy(rs.astype(np.float32)).permute(1,0,2,3).unsqueeze(0).to(dev)
    with torch.no_grad(): out,_=net(x)
    p=out[0].cpu().numpy()
    sg=(p-p.mean())/(p.std() if p.std()>1e-6 else 1)
    hr,est_shadow,cand,strong=compute_hr(sg,30.0,50.0,140.0,est_shadow)
    # 同信号: 纯argmax最强峰
    b,a=ss.butter(6,[50/60,140/60],btype='bandpass',fs=30.0)
    ff=ss.filtfilt(b,a,np.double(sg))
    N=int(60*30/0.1); F,P=ss.periodogram(x=ff,nfft=N,fs=30.0,window='hann')
    bpm=F*60; m=(bpm>=50)&(bpm<=140)
    amax=bpm[m][np.argmax(P[m])]
    best=min(c[0] for c in cand) if cand else np.nan
    hit=min(abs(c[0]-ECG) for c in cand)
    rows.append((hr,amax,est_shadow,hit,len(cand),len(strong)))
r=np.array(rows)
print("="*68)
print("复现线上影子链路 (窗口10s + 带[50,140] + tracker, prev_est初值75)")
print("="*68)
print("n=%d   ECG真值=%.1f"%(len(r),ECG))
print()
print("tracker选峰(线上做法) 均值=%.1f  偏差=%+.1f"%(r[:,0].mean(),r[:,0].mean()-ECG))
print("纯argmax最强峰        均值=%.1f  偏差=%+.1f"%(r[:,1].mean(),r[:,1].mean()-ECG))
print("候选峰中最接近真值    平均误差=%.1f bpm  <=5bpm命中=%.0f%%"%(r[:,3].mean(),100*(r[:,3]<=5).mean()))
print()
print("tracker内部est 轨迹: 起=%.1f 末=%.1f 均值=%.1f 最大=%.1f"%(r[0,2],r[-1,2],r[:,2].mean(),r[:,2].max()))
print("候选峰数 中位=%.0f   通过能量阈值的强候选数 中位=%.0f"%(np.median(r[:,4]),np.median(r[:,5])))
print()
print("est 前15步演化(看是否单向爬升):")
print("  "+" ".join("%.0f"%v for v in r[:15,2]))
print()
print("线上实测(受试者B, 16组ECG对照) 均值=97.0")
print("本次复现 tracker 均值=%.1f  -> %s"%(r[:,0].mean(),"复现成功" if abs(r[:,0].mean()-97.0)<12 else "仍有差距"))
