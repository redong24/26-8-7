import numpy as np, warnings
warnings.filterwarnings("ignore")
from scipy import signal as ss
FS=30.0
def synth(hr,seed,T,card=0.35,harm=0.45):
    t=np.arange(T)/FS
    rng=np.random.default_rng(seed)
    s=card*(np.sin(2*np.pi*(hr/60)*t)+harm*np.sin(2*np.pi*(2*hr/60)*t+0.7))
    s+=1.5*np.sin(2*np.pi*(15/60)*t+1.0)
    w=rng.standard_normal(T); b,a=ss.butter(2,0.08); s+=2.5*ss.filtfilt(b,a,w)
    w2=rng.standard_normal(T); b2,a2=ss.butter(2,[0.06,0.5],btype='band'); s+=1.8*ss.filtfilt(b2,a2,w2)
    s+=1.2*rng.standard_normal(T)
    return (s-s.mean())/s.std()

def spec(sig,LL,UL):
    b,a=ss.butter(6,[LL/60,UL/60],btype='bandpass',fs=FS)
    f=ss.filtfilt(b,a,np.double(sig)); N=int((60*FS)/0.1)
    F,P=ss.periodogram(x=f,nfft=N,fs=FS,window='hann')
    bpm=F*60;m=(bpm>=LL)&(bpm<=UL); return bpm[m],P[m]

print("窗口长度对精度的影响 (弱心搏, 纯最强峰选法, 无追踪):")
print(f"{'窗口秒':>6} {'分辨率bpm':>10} | " + " ".join(f"HR{h:>3}" for h in [62,70,78,85,95]) + "   平均MAE  平均偏差")
for wsec in [6,8,10,12,15,20]:
    T=int(wsec*FS)+1; maes=[];bias=[]
    row=[]
    for hr in [62,70,78,85,95]:
        vals=[]
        for k in range(150):
            bpm_r,p_r=spec(synth(hr,hash((hr,k,wsec))%10**6,T),58.,150.)
            vals.append(bpm_r[np.argmax(p_r)])
        v=np.array(vals); maes.append(np.abs(v-hr).mean()); bias.append(v.mean()-hr)
        row.append(f"{np.abs(v-hr).mean():5.1f}")
    print(f"{wsec:6d} {60/wsec:10.1f} | " + " ".join(row) + f"   {np.mean(maes):7.2f} {np.mean(bias):+9.2f}")

print("\n\nSNR门控(谱峰显著性不足时冻结输出)效果 [6秒窗]:")
T=181
def snr_pick(sig,LL=58.,UL=150.):
    bpm_r,p_r=spec(sig,LL,UL)
    pk,_=ss.find_peaks(p_r,distance=3)
    if len(pk)==0: return None,0
    i=pk[np.argmax(p_r[pk])]
    peak_bpm=bpm_r[i]
    # 峰显著性: 峰能量 / 带内中位能量
    sig_ratio=p_r[i]/max(np.median(p_r),1e-15)
    return peak_bpm,sig_ratio
for gate in [0,20,50,100,200,400]:
    maes=[];bias=[];cov=[]
    for hr in [62,70,78,85,95]:
        vals=[];n=0
        for k in range(200):
            v,r=snr_pick(synth(hr,hash((hr,k,3))%10**6,T))
            if v is not None and r>=gate: vals.append(v)
            n+=1
        if len(vals)<5: continue
        v=np.array(vals);maes.append(np.abs(v-hr).mean());bias.append(v.mean()-hr);cov.append(len(vals)/n)
    print(f"  门控阈值={gate:4d}: MAE={np.mean(maes):6.2f} 偏差={np.mean(bias):+6.2f} 输出覆盖率={100*np.mean(cov):5.1f}%")
