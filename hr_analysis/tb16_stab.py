"""直接验证: 对已采集的72x72序列做"逐帧平移配准"(等价于消除框抖动),
看rPPG信号质量与心率稳定性是否改善。这是不依赖原始大图的可靠判据。"""
import numpy as np, glob, os, time, torch, tb_core as TB
model,dev=TB.load_model()
def sel_cent(pk,est=75.0,frac=0.5):
    top=max(e for _,e in pk); s=[(b,e) for b,e in pk if e>=top*frac] or [max(pk,key=lambda x:x[1])]
    w=np.array([e for _,e in s]); b=np.array([bb for bb,_ in s]); return float((b*w).sum()/w.sum())

def align_seq(fr, rng=3):
    """把序列每帧对齐到第一帧(整数像素平移), 模拟'稳定ROI'的效果"""
    g=fr.astype(np.float32).mean(axis=1)
    ref=g[0]; out=[fr[0]]
    H,W=ref.shape
    c=(slice(rng,H-rng),slice(rng,W-rng))
    for i in range(1,fr.shape[0]):
        best=None;bd=(0,0)
        for dy in range(-rng,rng+1):
            for dx in range(-rng,rng+1):
                r=np.abs(ref[c]-np.roll(np.roll(g[i],dy,0),dx,1)[c]).mean()
                if best is None or r<best: best=r;bd=(dy,dx)
        out.append(np.roll(np.roll(fr[i],bd[0],1),bd[1],2))
    return np.stack(out)

now=time.time()
files=sorted([f for f in glob.glob(os.path.join(TB.CAPTURE_DIR,"*.npz")) if now-os.path.getmtime(f)<1800])
if len(files)<8:
    files=sorted(glob.glob(os.path.join(TB.CAPTURE_DIR,"*.npz")))[-20:]
files=files[:14]
print("="*86)
print("ROI稳定化(逐帧平移配准)对心率估计的影响   窗口数=%d"%len(files))
print("="*86)
raw_hr=[]; ali_hr=[]; raw_snr=[]; ali_snr=[]
def snr_of(pts,LL=45,UL=110):
    sp=TB.spectrum(pts,LL=LL,UL=UL)
    if not sp or not sp["peaks"]: return None,None
    pk=sp["peaks"]; e=np.array([x[1] for x in pk])
    top=e.max(); rest=e.sum()-top
    return sel_cent(pk), float(top/(rest+1e-12))
for fp in files:
    d=np.load(fp); fr=d["frames"]; ts=d["timestamps"]
    for lbl,F in (("raw",fr),("ali",align_seq(fr))):
        rs,_=TB.resample_window(F,ts)
        if rs is None: continue
        x=torch.from_numpy(rs.astype(np.float32)).permute(1,0,2,3).unsqueeze(0).to(dev)
        with torch.no_grad(): o,_=model(x)
        hr,sn=snr_of(o[0].detach().cpu().numpy())
        if hr is None: continue
        (raw_hr if lbl=="raw" else ali_hr).append(hr)
        (raw_snr if lbl=="raw" else ali_snr).append(sn)
r=np.array(raw_hr); a=np.array(ali_hr)
rs_=np.array(raw_snr); as_=np.array(ali_snr)
print("%-26s %8s %8s %10s"%("","均值","std","主峰能量占比"))
print("%-26s %8.2f %8.2f %10.3f"%("原始(框抖动)",r.mean(),r.std(),rs_.mean()))
print("%-26s %8.2f %8.2f %10.3f"%("平移配准后(稳定ROI)",a.mean(),a.std(),as_.mean()))
print()
print("  心率输出std:   %.2f -> %.2f  (%s)"%(r.std(),a.std(),"改善" if a.std()<r.std() else "未改善"))
print("  主峰能量占比:  %.3f -> %.3f  (%s)  <-- 越高说明频谱越干净"%(
    rs_.mean(),as_.mean(),"改善" if as_.mean()>rs_.mean() else "未改善"))
from scipy import stats
if r.size>3:
    t,p=stats.ttest_rel(as_[:min(len(as_),len(rs_))],rs_[:min(len(as_),len(rs_))])
    print("  主峰占比配对t检验: t=%.2f p=%.4f %s"%(t,p,"显著" if p<0.05 else "不显著"))
