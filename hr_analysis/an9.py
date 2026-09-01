import numpy as np, warnings
warnings.filterwarnings("ignore")
from scipy import signal as ss
FS=30.0; T=181; t=np.arange(T)/FS

def synth(hr, seed, harm=0.5, resp_amp=1.5, drift=2.0, noise=1.6):
    rng=np.random.default_rng(seed)
    s=np.sin(2*np.pi*(hr/60)*t)+harm*np.sin(2*np.pi*(2*hr/60)*t+0.7)
    s+=resp_amp*np.sin(2*np.pi*(15/60)*t+1.0)
    w=rng.standard_normal(T); b,a=ss.butter(2,0.08); s+=drift*ss.filtfilt(b,a,w)
    s+=noise*rng.standard_normal(T)
    return (s-s.mean())/s.std()

def peaks_of(sig, LL=15., UL=200.):
    b,a=ss.butter(6,[LL/60,UL/60],btype='bandpass',fs=FS)
    f=ss.filtfilt(b,a,np.double(sig))
    N=int((60*FS)/0.1)
    F,P=ss.periodogram(x=f,nfft=N,fs=FS,window='hann')
    bpm=F*60; m=(bpm>=LL)&(bpm<=UL); bpm_r,p_r=bpm[m],P[m]
    pk,_=ss.find_peaks(p_r,distance=3)
    if len(pk)==0: pk=np.array([int(np.argmax(p_r))])
    idx=pk[np.argsort(p_r[pk])[::-1]][:5]
    return [(float(bpm_r[i]),float(p_r[i])) for i in idx]

def cur(peaks,est,LL=58.,UL=180.,th=0.15,alpha=0.3):
    c=[(b,e) for b,e in peaks if LL<=b<=UL]
    if not c: return None,est
    top=max(e for _,e in c)
    s=[(b,e) for b,e in c if e>=top*th] or c
    ch,_=min(s,key=lambda x:abs(x[0]-est))
    return ch,alpha*ch+(1-alpha)*est

def fixed(peaks,est,LL=58.,UL=150.,th=0.45,alpha=0.25,w=0.55):
    c=[(b,e) for b,e in peaks if LL<=b<=UL]
    if not c: return None,est
    top=max(e for _,e in c)
    s=[(b,e) for b,e in c if e>=top*th] or c
    best=None;bs=-1e18
    for b,e in s:
        sc=(1-w)*(e/top)+w*np.exp(-abs(b-est)/12.0)
        for b2,e2 in peaks:
            if abs(b2-b/2)<=0.10*(b/2) and e2>=0.5*e and b2>=45: sc/=1.6; break
        if sc>bs: bs=sc;best=b
    est=float(np.clip(alpha*best+(1-alpha)*est,55,150))
    return best,est

print("真实心率恒定场景 (每档120轮, 报告 MAE 与 平均偏差):\n")
print(f"{'真实HR':>7} | {'当前生产 MAE':>12} {'偏差':>8} | {'修复版 MAE':>11} {'偏差':>8}")
print("-"*58)
tot={'cur':[], 'fix':[]}
for hr in [55,62,70,78,85,95,105,120]:
    o1=[];o2=[];e1=75.;e2=75.
    for k in range(120):
        pk=peaks_of(synth(hr,hash((hr,k))%10**6))
        v1,e1=cur(pk,e1); v2,e2=fixed(pk,e2)
        if v1: o1.append(v1)
        if v2: o2.append(v2)
    o1=np.array(o1);o2=np.array(o2)
    m1=np.abs(o1-hr).mean(); m2=np.abs(o2-hr).mean()
    tot['cur'].append(m1); tot['fix'].append(m2)
    print(f"{hr:7d} | {m1:12.2f} {o1.mean()-hr:+8.2f} | {m2:11.2f} {o2.mean()-hr:+8.2f}")
print("-"*58)
print(f"{'总平均':>7} | {np.mean(tot['cur']):12.2f} {'':8} | {np.mean(tot['fix']):11.2f}")
