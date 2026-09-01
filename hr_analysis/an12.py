import numpy as np, warnings
warnings.filterwarnings("ignore")
from scipy import signal as ss
FS=30.0;T=181;t=np.arange(T)/FS

def spec(sig,LL,UL):
    b,a=ss.butter(6,[LL/60,UL/60],btype='bandpass',fs=FS)
    f=ss.filtfilt(b,a,np.double(sig));N=int((60*FS)/0.1)
    F,P=ss.periodogram(x=f,nfft=N,fs=FS,window='hann')
    bpm=F*60;m=(bpm>=LL)&(bpm<=UL);return bpm[m],P[m]

# 纯噪声(完全无心搏): 输出应随机, 看它落在哪
print("=== 决定性检验: 输入纯噪声(无任何心跳成分) ===")
for LL,UL in [(58,180),(58,150),(58,140),(40,180)]:
    vals=[]
    for k in range(400):
        rng=np.random.default_rng(k)
        w=rng.standard_normal(T);b,a=ss.butter(2,0.08)
        s=2.5*ss.filtfilt(b,a,w)+1.2*rng.standard_normal(T)
        s=(s-s.mean())/s.std()
        bpm_r,p_r=spec(s,LL,UL)
        vals.append(bpm_r[np.argmax(p_r)])
    v=np.array(vals)
    print(f"  搜索带[{LL},{UL}] 带中心={ (LL+UL)/2:5.1f} -> 噪声输出均值={v.mean():6.2f} 中位数={np.median(v):6.2f}")
print("  ^^ 无信号时输出必然回归'带中心'。这就是低SNR下心率被系统性拉高的根本原因。")

print("\n=== 真实日志的谱峰显著性水平(校准门控阈值) ===")
import re,ast
pat=re.compile(r"独立局部峭值前5\(bpm,能量\)=(\[.*?\])")
ratios=[]
with open("/home/lsz/real_time_plus/real_time_Demo/output.log",errors='ignore') as fh:
    for line in fh:
        if "SHADOW-HR-DIAG" not in line: continue
        m=pat.search(line)
        if not m: continue
        try: pk=ast.literal_eval(m.group(1))
        except: continue
        pk=[(b,e) for b,e in pk if 58<=b<=180]
        if len(pk)>=2:
            es=sorted([e for _,e in pk],reverse=True)
            ratios.append(es[0]/max(es[1],1e-12))
r=np.array(ratios)
print(f"  最强峰/次强峰 能量比: n={len(r)} 中位数={np.median(r):.2f} p25={np.percentile(r,25):.2f} p75={np.percentile(r,75):.2f}")
print(f"  比值<1.5(即无明确主峰,基本靠猜)的占比: {100*(r<1.5).mean():.1f}%")
print(f"  比值<2.0 的占比: {100*(r<2.0).mean():.1f}%")
