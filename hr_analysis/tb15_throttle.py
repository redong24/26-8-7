"""评估: 推理节流对(a)采集帧率 (b)心率精度 的影响。
节流只减少"多久算一次心率", 不减少"多久采一帧"。采集帧率提升 => 频谱质量提升。"""
import numpy as np, glob, os, torch, tb_core as TB
model,dev=TB.load_model()
def sel_cent(pk,est,frac=0.5):
    top=max(e for _,e in pk); s=[(b,e) for b,e in pk if e>=top*frac] or [max(pk,key=lambda x:x[1])]
    w=np.array([e for _,e in s]); b=np.array([bb for bb,_ in s]); return float((b*w).sum()/w.sum())

INFER_MS=1167.0; DETECT_MS=12.5; OTHER_MS=20.0
print("="*94)
print("A. 推理节流对采集帧率的理论影响 (检测%.1fms 推理%.0fms)"%(DETECT_MS,INFER_MS))
print("="*94)
print("%-26s %14s %14s %16s"%("策略","每帧平均开销","可达采集fps","10秒窗口内帧数"))
rows=[]
for lbl,every in [("现状: 每帧推理",1),("每2帧推理一次",2),("每4帧推理一次",4),
                  ("每8帧推理一次",8),("按时间: 每1.0秒一次",None)]:
    if every: cost=DETECT_MS+OTHER_MS+INFER_MS/every
    else:
        # 每1秒一次: 先估采集fps, 迭代求不动点
        fps=10.0
        for _ in range(50):
            cost=DETECT_MS+OTHER_MS+INFER_MS*(1.0/max(fps,0.1))/1.0
            fps=1000.0/cost
        cost=1000.0/fps
    fps=1000.0/cost
    rows.append((lbl,fps))
    print("%-26s %11.0f ms %11.1f fps %14.0f 帧"%(lbl,cost,fps,fps*10))

print()
print("="*94)
print("B. 采集帧率提升是否真的改善心率估计? (用真实采集帧做降采样对照)")
print("="*94)
SESS=["4b989b29","63d389c2","209812c9","c5a766cd"]
print("%-12s %8s | %-38s"%("会话","原始帧数","不同有效帧率下的输出"))
print("%-12s %8s | %8s %8s %8s %8s"%("","","~3.7fps","~7.5fps","15fps","30fps"))
print("-"*80)
allres={k:[] for k in (4,2,1,0)}
for sess in SESS:
    cf="/home/lsz/webapp/hr_analysis/tb_pts_%s.npy"%sess
    files=sorted(glob.glob(os.path.join(TB.CAPTURE_DIR,'*%s*.npz'%sess)))
    if len(files)<10: continue
    out={}
    for stride,key in [(4,4),(2,2),(1,1)]:
        vals=[]
        for fp in files[:40]:
            d=np.load(fp); fr=d['frames']; ts=d['timestamps']
            fr2,ts2=fr[::stride],ts[::stride]
            rs,_=TB.resample_window(fr2,ts2)
            if rs is None: continue
            x=torch.from_numpy(rs.astype(np.float32)).permute(1,0,2,3).unsqueeze(0).to(dev)
            with torch.no_grad(): o,_=model(x)
            sp=TB.spectrum(o[0].detach().cpu().numpy(),LL=45,UL=110)
            if sp: vals.append(sel_cent(sp["peaks"],75.0))
        out[key]=np.array(vals,float)
    ln="%-12s %8d |"%(sess,len(files))
    for k in (4,2,1):
        v=out.get(k,np.array([]))
        ln+=" %8s"%("%.1f"%v.mean() if v.size else "-")
        if v.size: allres[k].append(v.mean())
    ln+=" %8s"%"-"
    print(ln)
print("-"*80)
for k,lbl in [(4,"~3.7fps(4倍降采样)"),(2,"~7.5fps(2倍降采样)"),(1,"~15fps(原始全部帧)")]:
    a=allres[k]
    if a: print("  %-22s 跨会话均值 %.2f  会话间std %.2f"%(lbl,np.mean(a),np.std(a)))
print("\n【判据】若帧率越高、会话间std越小 => 提升帧率确实改善稳定性")
