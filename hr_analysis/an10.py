import numpy as np, warnings
warnings.filterwarnings("ignore")
from scipy import signal as ss
FS=30.0; T=181; t=np.arange(T)/FS

def synth(hr,seed,card=0.35,harm=0.45):
    """弱心搏 + 强噪声/漂移 —— 贴近真实rPPG(日志显示心搏峰常非最强峰)"""
    rng=np.random.default_rng(seed)
    s=card*(np.sin(2*np.pi*(hr/60)*t)+harm*np.sin(2*np.pi*(2*hr/60)*t+0.7))
    s+=1.5*np.sin(2*np.pi*(15/60)*t+1.0)
    w=rng.standard_normal(T); b,a=ss.butter(2,0.08); s+=2.5*ss.filtfilt(b,a,w)
    # 宽带彩色噪声，会在心率带内产生随机伪峰
    w2=rng.standard_normal(T); b2,a2=ss.butter(2,[0.06,0.5],btype='band'); s+=1.8*ss.filtfilt(b2,a2,w2)
    s+=1.2*rng.standard_normal(T)
    return (s-s.mean())/s.std()

def peaks_of(sig,LL=15.,UL=200.):
    b,a=ss.butter(6,[LL/60,UL/60],btype='bandpass',fs=FS)
    f=ss.filtfilt(b,a,np.double(sig)); N=int((60*FS)/0.1)
    F,P=ss.periodogram(x=f,nfft=N,fs=FS,window='hann')
    bpm=F*60;m=(bpm>=LL)&(bpm<=UL);bpm_r,p_r=bpm[m],P[m]
    pk,_=ss.find_peaks(p_r,distance=3)
    if len(pk)==0: pk=np.array([int(np.argmax(p_r))])
    idx=pk[np.argsort(p_r[pk])[::-1]][:5]
    return [(float(bpm_r[i]),float(p_r[i])) for i in idx]

def cur(peaks,est,LL=58.,UL=180.,th=0.15,alpha=0.3):
    c=[(b,e) for b,e in peaks if LL<=b<=UL]
    if not c: return None,est
    top=max(e for _,e in c); s=[(b,e) for b,e in c if e>=top*th] or c
    ch,_=min(s,key=lambda x:abs(x[0]-est)); return ch,alpha*ch+(1-alpha)*est

def fixed(peaks,est,LL=58.,UL=150.,th=0.45,alpha=0.25,w=0.55):
    c=[(b,e) for b,e in peaks if LL<=b<=UL]
    if not c: return None,est
    top=max(e for _,e in c); s=[(b,e) for b,e in c if e>=top*th] or c
    best=None;bs=-1e18
    for b,e in s:
        sc=(1-w)*(e/top)+w*np.exp(-abs(b-est)/12.0)
        for b2,e2 in peaks:
            if abs(b2-b/2)<=0.10*(b/2) and e2>=0.5*e and b2>=45: sc/=1.6;break
        if sc>bs: bs=sc;best=b
    return best,float(np.clip(alpha*best+(1-alpha)*est,55,150))

print("弱心搏(真实rPPG)场景, 每档200轮:\n")
print(f"{'真实HR':>7} | {'当前生产 MAE':>12} {'偏差':>8} | {'修复版 MAE':>11} {'偏差':>8}")
print("-"*58)
c1=[];c2=[]
for hr in [62,70,78,85,95]:
    o1=[];o2=[];e1=75.;e2=75.
    for k in range(200):
        pk=peaks_of(synth(hr,hash((hr,k,7))%10**6))
        v1,e1=cur(pk,e1); v2,e2=fixed(pk,e2)
        if v1:o1.append(v1)
        if v2:o2.append(v2)
    o1,o2=np.array(o1),np.array(o2)
    m1,m2=np.abs(o1-hr).mean(),np.abs(o2-hr).mean(); c1.append(m1);c2.append(m2)
    print(f"{hr:7d} | {m1:12.2f} {o1.mean()-hr:+8.2f} | {m2:11.2f} {o2.mean()-hr:+8.2f}")
print("-"*58)
print(f"{'总平均':>7} | {np.mean(c1):12.2f} {'':8} | {np.mean(c2):11.2f}")
