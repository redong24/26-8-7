import numpy as np, warnings
warnings.filterwarnings("ignore")
from scipy import signal as ss
FS=30.0
def synth(hr,seed,T):
    t=np.arange(T)/FS; rng=np.random.default_rng(seed)
    s=0.35*(np.sin(2*np.pi*(hr/60)*t)+0.45*np.sin(2*np.pi*(2*hr/60)*t+0.7))
    s+=1.5*np.sin(2*np.pi*(15/60)*t+1.0)
    w=rng.standard_normal(T);b,a=ss.butter(2,0.08);s+=2.5*ss.filtfilt(b,a,w)
    w2=rng.standard_normal(T);b2,a2=ss.butter(2,[0.06,0.5],btype='band');s+=1.8*ss.filtfilt(b2,a2,w2)
    s+=1.2*rng.standard_normal(T);return (s-s.mean())/s.std()
def spec(sig,LL,UL):
    b,a=ss.butter(6,[LL/60,UL/60],btype='bandpass',fs=FS)
    f=ss.filtfilt(b,a,np.double(sig));N=int((60*FS)/0.1)
    F,P=ss.periodogram(x=f,nfft=N,fs=FS,window='hann')
    bpm=F*60;m=(bpm>=LL)&(bpm<=UL);return bpm[m],P[m]
def track(sig,st,LL,UL,th=0.15,alpha=0.3):
    """完全保留现有 compute_hr_with_tracking 逻辑, 只改 LL/UL/窗口"""
    bpm_r,p_r=spec(sig,LL,UL)
    pk,_=ss.find_peaks(p_r,distance=3)
    if len(pk)==0: pk=np.array([int(np.argmax(p_r))])
    c=[(bpm_r[i],p_r[i]) for i in pk];top=max(e for _,e in c)
    s=[(b,e) for b,e in c if e>=top*th] or c
    ch,_=min(s,key=lambda x:abs(x[0]-st['est']))
    st['est']=alpha*ch+(1-alpha)*st['est'];return ch,st

HRS=[58,65,72,80,88,96,110]
print("只调整两个参数(窗口长度 / 搜索带上限), 算法逻辑完全不动:\n")
print(f"{'窗口':>5} {'搜索带':>12} | " + "".join(f"{h:>6}" for h in HRS) + f"{'平均MAE':>9}{'平均偏差':>10}")
print("-"*95)
best=None
for W,T in [(6,181),(8,241),(10,301),(12,361),(15,451)]:
    for LL,UL in [(58,180),(58,150),(50,140),(48,130)]:
        maes=[];bias=[];row=""
        for hr in HRS:
            st={'est':75.};o=[]
            for k in range(200):
                v,st=track(synth(hr,hash((hr,k,5))%10**6,T),st,LL,UL)
                o.append(v)
            o=np.array(o);m=np.abs(o-hr).mean()
            maes.append(m);bias.append(o.mean()-hr);row+=f"{m:6.1f}"
        am=np.mean(maes);ab=np.mean(bias)
        tag=""
        if best is None or am<best[0]: best=(am,W,LL,UL);tag="  <<<"
        print(f"{W:4d}s {f'[{LL},{UL}]':>12} | {row}{am:9.2f}{ab:+10.2f}{tag}")
print("-"*95)
print(f"\n最优组合: 窗口={best[1]}秒 搜索带=[{best[2]},{best[3]}] 平均MAE={best[0]:.2f}")
print(f"当前生产(6秒,[58,180])对照 -> 见首行")
