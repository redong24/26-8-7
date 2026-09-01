import numpy as np, warnings
warnings.filterwarnings("ignore")
from scipy import signal as ss
FS=30.0
def synth(hr,seed,T,card=0.35,harm=0.45):
    t=np.arange(T)/FS; rng=np.random.default_rng(seed)
    s=card*(np.sin(2*np.pi*(hr/60)*t)+harm*np.sin(2*np.pi*(2*hr/60)*t+0.7))
    s+=1.5*np.sin(2*np.pi*(15/60)*t+1.0)
    w=rng.standard_normal(T);b,a=ss.butter(2,0.08);s+=2.5*ss.filtfilt(b,a,w)
    w2=rng.standard_normal(T);b2,a2=ss.butter(2,[0.06,0.5],btype='band');s+=1.8*ss.filtfilt(b2,a2,w2)
    s+=1.2*rng.standard_normal(T); return (s-s.mean())/s.std()
def spec(sig,LL,UL):
    b,a=ss.butter(6,[LL/60,UL/60],btype='bandpass',fs=FS)
    f=ss.filtfilt(b,a,np.double(sig));N=int((60*FS)/0.1)
    F,P=ss.periodogram(x=f,nfft=N,fs=FS,window='hann')
    bpm=F*60;m=(bpm>=LL)&(bpm<=UL);return bpm[m],P[m]

def OLD(sig,st):
    bpm_r,p_r=spec(sig,58.,180.)
    pk,_=ss.find_peaks(p_r,distance=3)
    if len(pk)==0: pk=np.array([int(np.argmax(p_r))])
    c=[(bpm_r[i],p_r[i]) for i in pk]; top=max(e for _,e in c)
    s=[(b,e) for b,e in c if e>=top*0.15] or c
    ch,_=min(s,key=lambda x:abs(x[0]-st['est']))
    st['est']=0.3*ch+0.7*st['est']; return ch,st

def NEW(sig,st,LL=50.,UL=140.,gate=6.0,alpha=0.25):
    """窄带 + 显著性门控(不显著则冻结上次值) + 谐波惩罚 + 打分选峰"""
    bpm_r,p_r=spec(sig,LL,UL)
    pk,_=ss.find_peaks(p_r,distance=3)
    if len(pk)==0: return st['last'],st
    med=max(np.median(p_r),1e-15)
    cand=[(bpm_r[i],p_r[i]) for i in pk]
    top_b,top_e=max(cand,key=lambda x:x[1])
    if top_e/med < gate:               # 谱峰不显著 -> 本轮不可信, 冻结
        return st['last'],st
    sel=[(b,e) for b,e in cand if e>=top_e*0.45] or [(top_b,top_e)]
    best=None;bs=-1e18
    for b,e in sel:
        sc=0.45*(e/top_e)+0.55*np.exp(-abs(b-st['est'])/12.0)
        for b2,e2 in cand:
            if abs(b2-b/2)<=0.10*(b/2) and e2>=0.5*e and b2>=45: sc/=1.6;break
        if sc>bs: bs=sc;best=b
    st['est']=float(np.clip(alpha*best+(1-alpha)*st['est'],50,140))
    st['last']=best; return best,st

for W,T in [(6,181),(10,301)]:
    print(f"\n{'='*66}\n窗口={W}秒 (弱心搏真实场景, 每档250轮)\n{'='*66}")
    print(f"{'真实HR':>6} | {'当前生产 MAE':>12}{'偏差':>8} | {'建议方案 MAE':>12}{'偏差':>8}{'覆盖率':>8}")
    a1=[];a2=[]
    for hr in [58,65,72,80,88,96]:
        o1=[];o2=[];s1={'est':75.};s2={'est':75.,'last':np.nan};n=0
        for k in range(250):
            sg=synth(hr,hash((hr,k,11))%10**6,T)
            v1,s1=OLD(sg,s1); v2,s2=NEW(sg,s2)
            o1.append(v1)
            if v2==v2: o2.append(v2)
            n+=1
        o1=np.array(o1);o2=np.array(o2)
        m1=np.abs(o1-hr).mean();m2=np.abs(o2-hr).mean() if len(o2) else np.nan
        a1.append(m1);a2.append(m2)
        print(f"{hr:6d} | {m1:12.2f}{o1.mean()-hr:+8.2f} | {m2:12.2f}{o2.mean()-hr:+8.2f}{100*len(o2)/n:7.0f}%")
    print(f"{'平均':>6} | {np.mean(a1):12.2f}{'':8} | {np.nanmean(a2):12.2f}")
