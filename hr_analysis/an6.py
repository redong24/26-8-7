import re, ast, numpy as np
LOG="/home/lsz/real_time_plus/real_time_Demo/output.log"
pat=re.compile(r"\[SHADOW-HR-DIAG\] session=(\S+) 独立局部峭值前5\(bpm,能量\)=(\[.*?\]) chosen_shadow_hr=([\d.eE+-]+|nan) shadow_track_est_after_pick=([\d.]+)")
rows=[]
with open(LOG,errors='ignore') as fh:
    for line in fh:
        m=pat.search(line)
        if not m: continue
        try: pk=ast.literal_eval(m.group(2)); ch=float(m.group(3))
        except: continue
        rows.append((m.group(1),pk,ch,float(m.group(4))))

V=[]
for sid,pk,ch,est in rows:
    inband=[(b,e) for b,e in pk if 58<=b<=180]
    if inband: V.append((sid,inband,ch,est,pk))
print("有效样本:",len(V))
ch=np.array([v[2] for v in V])
top=np.array([max(v[1],key=lambda x:x[1])[0] for v in V])
print(f"\n展示心率 chosen : mean={ch.mean():6.2f} median={np.median(ch):6.2f} p75={np.percentile(ch,75):.1f} >110占比={100*(ch>110).mean():.1f}%")
print(f"带内最强峰 top  : mean={top.mean():6.2f} median={np.median(top):6.2f} p75={np.percentile(top,75):.1f} >110占比={100*(top>110).mean():.1f}%")
print(f"chosen - top    : mean={(ch-top).mean():+6.2f}  选中最强峰的比例={100*(np.abs(ch-top)<=1.5).mean():.1f}%")

half_exists=0; half_stronger=0; n=0
for sid,inband,c,est,allpk in V:
    n+=1
    ce=next((e for b,e in inband if abs(b-c)<=1.5),0.0)
    h=[(b,e) for b,e in allpk if abs(b-c/2)<=max(3,0.08*c/2)]
    if h:
        half_exists+=1
        if max(e for _,e in h)>ce: half_stronger+=1
print(f"\nchosen/2 处存在峰的比例        : {100*half_exists/n:.1f}%")
print(f"  且该半频峰能量更强(谐波锁定)  : {100*half_stronger/n:.1f}%")

est=np.array([v[3] for v in V])
print("\n=== 追踪状态锁定效应(带内最强峰为参照) ===")
for lo,up in [(58,80),(80,100),(100,120),(120,200)]:
    m=(est>=lo)&(est<up)
    if m.sum()<20: continue
    print(f"  est∈[{lo},{up}): n={m.sum():5d} chosen均值={ch[m].mean():6.1f} 带内最强峰均值={top[m].mean():6.1f} 差={ch[m].mean()-top[m].mean():+6.1f}")

print("\n=== 重放: 不同UL上限 + 能量阈值 对输出的影响 ===")
for ULx in [180,150,140,130]:
    for th in [0.15,0.5]:
        e=75.0; out=[]
        for sid,inband,c,_,allpk in V:
            cand=[(b,en) for b,en in allpk if 58<=b<=ULx]
            if not cand: continue
            tope=max(en for _,en in cand)
            strong=[(b,en) for b,en in cand if en>=tope*th] or cand
            pick,_=min(strong,key=lambda x:abs(x[0]-e))
            e=0.3*pick+0.7*e; out.append(pick)
        o=np.array(out)
        print(f"  UL={ULx:3d} thresh={th:.2f}: 均值={o.mean():6.2f} 中位数={np.median(o):6.2f} >110占比={100*(o>110).mean():5.1f}%")
