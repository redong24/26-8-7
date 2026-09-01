import numpy as np, glob, os, torch, tb_core as TB
SESSIONS=["63d389c2","209812c9","c5a766cd","ffa6d09d","7909a2c5","1a0b863f"]
model,dev=TB.load_model()
def sel_cur(pk,est,th=0.15):
    top=max(e for _,e in pk); s=[(b,e) for b,e in pk if e>=top*th] or list(pk)
    return min(s,key=lambda x:abs(x[0]-est))[0]
def sel_cent(pk,est,frac=0.5):
    top=max(e for _,e in pk); s=[(b,e) for b,e in pk if e>=top*frac] or [max(pk,key=lambda x:x[1])]
    w=np.array([e for _,e in s]); b=np.array([bb for bb,_ in s]); return float((b*w).sum()/w.sum())
def ev(sps,pick,R=70,init=75.0,a=0.25):
    est=init; out=[]
    for sp in sps:
        for _ in range(R):
            c=pick(sp["peaks"],est)
            if c is None: continue
            out.append(c); est=a*c+(1-a)*est
    return np.array(out,float)
print("%-11s %5s | %-22s | %-22s"%("会话","n","现状 [50,140]+th0.15","新方案 [45,110]+重心"))
print("%-11s %5s | %8s %8s %6s | %8s %8s %6s"%("","","均值","起点极差","std","均值","起点极差","std"))
print("-"*80)
agg={}
for sess in SESSIONS:
    cf="/home/lsz/webapp/hr_analysis/tb_pts_%s.npy"%sess
    if os.path.exists(cf):
        pts_all=list(np.load(cf,allow_pickle=True))
    else:
        files=sorted(glob.glob(os.path.join(TB.CAPTURE_DIR,'*%s*.npz'%sess)))
        if len(files)<20: continue
        pts_all=[]
        for fp in files:
            d=np.load(fp); rs,_=TB.resample_window(d['frames'],d['timestamps'])
            if rs is None: pts_all.append(None); continue
            x=torch.from_numpy(rs.astype(np.float32)).permute(1,0,2,3).unsqueeze(0).to(dev)
            with torch.no_grad(): o,_=model(x)
            pts_all.append(o[0].detach().cpu().numpy())
        np.save(cf,np.array(pts_all,dtype=object))
    row=[]
    for (LL,UL),pick in [((50,140),sel_cur),((45,110),sel_cent)]:
        sps=[TB.spectrum(p,LL=LL,UL=UL) for p in pts_all if p is not None]
        sps=[s for s in sps if s]
        v=ev(sps,pick)
        ms=[ev(sps,pick,R=20,init=float(i)).mean() for i in (55,75,95,120)]
        row.append((v.mean(),max(ms)-min(ms),v.std()))
        agg.setdefault((LL,UL),[]).append(v.mean())
    print("%-11s %5d | %8.2f %8.2f %6.1f | %8.2f %8.2f %6.1f"%(sess,len(sps),
        row[0][0],row[0][1],row[0][2],row[1][0],row[1][1],row[1][2]))
print("-"*80)
for k,v in agg.items():
    print("  带%s 跨会话输出均值 = %.2f  (会话间std %.2f)"%(k,np.mean(v),np.std(v)))
print("\n静息心率生理常识: 成人 60~100, 中位约72")
