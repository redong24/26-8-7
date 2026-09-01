import numpy as np, glob, os, torch, tb_core as TB
model,dev=TB.load_model()
def sel_cent(pk,est,frac=0.5):
    top=max(e for _,e in pk); s=[(b,e) for b,e in pk if e>=top*frac] or [max(pk,key=lambda x:x[1])]
    w=np.array([e for _,e in s]); b=np.array([bb for bb,_ in s]); return float((b*w).sum()/w.sum())
def sel_cur(pk,est,th=0.15):
    top=max(e for _,e in pk); s=[(b,e) for b,e in pk if e>=top*th] or list(pk)
    return min(s,key=lambda x:abs(x[0]-est))[0]
def load(sess):
    cf="/home/lsz/webapp/hr_analysis/tb_pts_%s.npy"%sess
    if os.path.exists(cf): return list(np.load(cf,allow_pickle=True))
    files=sorted(glob.glob(os.path.join(TB.CAPTURE_DIR,'*%s*.npz'%sess))); out=[]
    for fp in files:
        d=np.load(fp); rs,_=TB.resample_window(d['frames'],d['timestamps'])
        if rs is None: out.append(None); continue
        x=torch.from_numpy(rs.astype(np.float32)).permute(1,0,2,3).unsqueeze(0).to(dev)
        with torch.no_grad(): o,_=model(x)
        out.append(o[0].detach().cpu().numpy())
    np.save(cf,np.array(out,dtype=object)); return out

def seq_out(sps,pick,R,init,ema_a):
    """R = 每个窗口重复轮数(模拟线上更新密度)。ema_a=None 表示不平滑。"""
    est=init; e=None; out=[]
    for sp in sps:
        for _ in range(R):
            c=pick(sp["peaks"],est)
            if c is None: continue
            est=0.30*c+0.70*est
            if ema_a is None: out.append(c)
            else:
                e=c if e is None else ema_a*c+(1-ema_a)*e
                out.append(e)
    return np.array(out,float)

TRUTH=78.3
print("="*92)
print("A. 输出端EMA对精度的影响 (会话4b989b29, R=70 线上真实更新密度, 真值%.1f仅供相对比较)"%TRUTH)
print("="*92)
pts=load("4b989b29")
sps_c=[s for s in (TB.spectrum(p,LL=45,UL=110) for p in pts if p is not None) if s]
print("%-30s %8s %8s %8s %8s %9s"%("方案","均值","偏差","MAE","±5bpm","±10bpm"))
for lbl,a in [("重心 无平滑",None),("重心 + EMA0.30",0.30),("重心 + EMA0.15",0.15),("重心 + EMA0.08",0.08)]:
    v=seq_out(sps_c,sel_cent,70,75.0,a); m=TB.metrics(v,TRUTH)
    print("%-30s %8.2f %+8.2f %8.2f %7.1f%% %8.1f%%"%(lbl,m["mean"],m["bias"],m["mae"],m["w5"],m["w10"]))

print()
print("="*92)
print("B. EMA是否重新引入起点依赖?  (R=20, 起点 55/75/95/120)")
print("="*92)
for lbl,a in [("重心 无平滑",None),("重心 + EMA0.15",0.15),("重心 + EMA0.08",0.08)]:
    ms=[seq_out(sps_c,sel_cent,20,float(i),a).mean() for i in (55,75,95,120)]
    print("  %-18s 各起点均值 %s   起点极差 = %.3f"%(lbl,["%.2f"%x for x in ms],max(ms)-min(ms)))
ms=[seq_out([s for s in (TB.spectrum(p,LL=50,UL=140) for p in pts if p is not None) if s],sel_cur,20,float(i),None).mean() for i in (55,75,95,120)]
print("  %-18s 各起点均值 %s   起点极差 = %.3f"%("[对照]现状",["%.2f"%x for x in ms],max(ms)-min(ms)))

print()
print("="*92)
print("C. 页面跳字幅度 (相邻刷新之间, 单位bpm)")
print("="*92)
print("%-30s %10s %10s %10s"%("方案","中位","P90","最大"))
v=seq_out([s for s in (TB.spectrum(p,LL=50,UL=140) for p in pts if p is not None) if s],sel_cur,1,75.0,None)
d=np.abs(np.diff(v)); print("%-30s %10.2f %10.2f %10.2f"%("[对照]现状(线上实际观感)",np.median(d),np.percentile(d,90),d.max()))
for lbl,a in [("重心 无平滑",None),("重心 + EMA0.30",0.30),("重心 + EMA0.15",0.15),("重心 + EMA0.08",0.08)]:
    v=seq_out(sps_c,sel_cent,1,75.0,a); d=np.abs(np.diff(v))
    print("%-30s %10.2f %10.2f %10.2f"%(lbl,np.median(d),np.percentile(d,90),d.max()))

print()
print("="*92)
print("D. 响应速度: EMA从75起步, 达到稳态所需刷新次数 (线上约4.2次/秒)")
print("="*92)
for lbl,a in [("EMA0.30",0.30),("EMA0.15",0.15),("EMA0.08",0.08)]:
    n=int(np.ceil(np.log(0.05)/np.log(1-a)))
    print("  %-10s 收敛95%%需 %3d 次刷新 ≈ %.1f 秒"%(lbl,n,n/4.2))
