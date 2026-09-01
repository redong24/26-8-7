import numpy as np, glob, os, torch, tb_core as TB
SESS="4b989b29"
model,dev=TB.load_model()

def sel_cur(pk,est,th=0.15):
    top=max(e for _,e in pk); s=[(b,e) for b,e in pk if e>=top*th] or list(pk)
    return min(s,key=lambda x:abs(x[0]-est))[0]
def sel_cent(pk,est,frac=0.5):
    top=max(e for _,e in pk); s=[(b,e) for b,e in pk if e>=top*frac] or [max(pk,key=lambda x:x[1])]
    w=np.array([e for _,e in s]); b=np.array([bb for bb,_ in s]); return float((b*w).sum()/w.sum())

cf="/home/lsz/webapp/hr_analysis/tb_pts_%s.npy"%SESS
pts_all=list(np.load(cf,allow_pickle=True)) if os.path.exists(cf) else None
if pts_all is None:
    files=sorted(glob.glob(os.path.join(TB.CAPTURE_DIR,'*%s*.npz'%SESS))); pts_all=[]
    for fp in files:
        d=np.load(fp); rs,_=TB.resample_window(d['frames'],d['timestamps'])
        if rs is None: pts_all.append(None); continue
        x=torch.from_numpy(rs.astype(np.float32)).permute(1,0,2,3).unsqueeze(0).to(dev)
        with torch.no_grad(): o,_=model(x)
        pts_all.append(o[0].detach().cpu().numpy())
    np.save(cf,np.array(pts_all,dtype=object))

TRUTH=78.3
def ev(sps,pick,R=70,init=75.0,a=0.25):
    est=init; out=[]
    for sp in sps:
        for _ in range(R):
            c=pick(sp["peaks"],est)
            if c is None: continue
            out.append(c); est=a*c+(1-a)*est
    return np.array(out,float)

print("="*78); print("A. ALPHA 0.25(测试台) vs 0.30(生产文件) 对结论的影响"); print("="*78)
print("%-26s %8s %8s %8s %8s"%("方案","alpha","均值","偏差","MAE"))
for (LL,UL),pick,nm in [((50,140),sel_cur,"现状 th=0.15"),((45,110),sel_cent,"新方案 重心")]:
    sps=[TB.spectrum(p,LL=LL,UL=UL) for p in pts_all if p is not None]; sps=[s for s in sps if s]
    for a in (0.25,0.30):
        v=ev(sps,pick,a=a); m=TB.metrics(v,TRUTH)
        print("%-26s %8.2f %8.2f %+8.2f %8.2f"%(nm,a,m["mean"],m["bias"],m["mae"]))

print()
print("="*78); print("B. 显示抖动: 相邻窗口之间输出的跳变幅度 (页面每0.24秒刷新一次)"); print("="*78)
for (LL,UL),pick,nm in [((50,140),sel_cur,"现状 th=0.15"),((45,110),sel_cent,"新方案 重心")]:
    sps=[TB.spectrum(p,LL=LL,UL=UL) for p in pts_all if p is not None]; sps=[s for s in sps if s]
    est=75.0; seq=[]
    for sp in sps:
        c=pick(sp["peaks"],est)
        if c is None: continue
        seq.append(c); est=0.30*c+(1-0.30)*est
    seq=np.array(seq,float); d=np.abs(np.diff(seq))
    # EMA(纯输出滤波, 不回灌选值)
    ema=[]; e=None
    for c in seq:
        e=c if e is None else 0.30*c+0.70*e
        ema.append(e)
    ema=np.array(ema); de=np.abs(np.diff(ema))
    print("%-22s 原始输出: 相邻跳变中位 %5.2f  P90 %6.2f  最大 %6.2f"%(nm,np.median(d),np.percentile(d,90),d.max()))
    print("%-22s EMA(0.3)后: 相邻跳变中位 %5.2f  P90 %6.2f  最大 %6.2f   |  EMA均值 %.2f"%("",np.median(de),np.percentile(de,90),de.max(),ema.mean()))
