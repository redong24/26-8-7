import re, ast, numpy as np
LOG="/home/lsz/real_time_plus/real_time_Demo/output.log"
pat=re.compile(r"\[SHADOW-HR-DIAG\] session=(\S+) 独立局部峭值前5\(bpm,能量\)=(\[.*?\]) chosen_shadow_hr=([\d.eE+-]+|nan) shadow_track_est_after_pick=([\d.]+)")
seq={}
with open(LOG,errors='ignore') as fh:
    for line in fh:
        m=pat.search(line)
        if not m: continue
        try: pk=ast.literal_eval(m.group(2)); ch=float(m.group(3))
        except: continue
        seq.setdefault(m.group(1),[]).append((pk,ch))

def cur(peaks, est, LL=58., UL=180., th=0.15, alpha=0.3):
    c=[(b,e) for b,e in peaks if LL<=b<=UL]
    if not c: return None, est
    top=max(e for _,e in c)
    if top<=1e-12: return None, est
    s=[(b,e) for b,e in c if e>=top*th] or c
    ch,_=min(s,key=lambda x:abs(x[0]-est))
    return ch, alpha*ch+(1-alpha)*est

def fixed(peaks, est, LL=58., UL=150., th=0.45, alpha=0.25,
          w_prox=0.55, harm_tol=0.10, harm_bonus=1.6):
    """能量+邻近度联合打分 + 二次谐波惩罚，提供回复力，消除棘轮。"""
    c=[(b,e) for b,e in peaks if LL<=b<=UL]
    if not c: return None, est
    top=max(e for _,e in c)
    if top<=1e-12: return None, est
    s=[(b,e) for b,e in c if e>=top*th] or c
    best=None; bs=-1e18
    for b,e in s:
        en=e/top                                  # 能量得分 0..1
        prox=np.exp(-abs(b-est)/12.0)             # 邻近度得分(软, 12bpm尺度)
        score=(1-w_prox)*en + w_prox*prox
        # 二次谐波惩罚: 若 b/2 处存在能量不弱的峰, b 很可能是谐波
        for b2,e2 in peaks:
            if abs(b2-b/2)<=harm_tol*(b/2) and e2>=0.5*e and b2>=45:
                score/=harm_bonus; break
        if score>bs: bs=score; best=b
    est=alpha*best+(1-alpha)*est
    est=float(np.clip(est,55,150))                # 约束追踪状态，防跑飞
    return best, est

for name,fn,init in [("当前生产",cur,75.0),("修复版",fixed,75.0)]:
    allv=[]
    for sid,rows in seq.items():
        est=init
        for pk,_ in rows:
            v,est=fn(pk,est)
            if v is not None: allv.append(v)
    a=np.array(allv)
    print(f"{name:8s}: n={len(a)} 均值={a.mean():6.2f} 中位数={np.median(a):6.2f} "
          f"p25={np.percentile(a,25):5.1f} p75={np.percentile(a,75):6.1f} "
          f">110占比={100*(a>110).mean():5.1f}% >120占比={100*(a>120).mean():5.1f}%")

# 初始值依赖性 = 锁定强度
print("\n初始值依赖性(理想: 与init无关):")
for nm,fn in [("当前生产",cur),("修复版",fixed)]:
    res=[]
    for init in [60.,75.,95.,120.,145.]:
        v=[]
        for sid,rows in seq.items():
            est=init
            for pk,_ in rows:
                x,est=fn(pk,est)
                if x is not None: v.append(x)
        res.append(np.mean(v))
    print(f"  {nm:8s} init60..145 -> 均值 {['%.1f'%r for r in res]}  极差={max(res)-min(res):.2f}")
