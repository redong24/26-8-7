import re, ast, numpy as np
LOG="/home/lsz/real_time_plus/real_time_Demo/output.log"
pat=re.compile(r"\[SHADOW-HR-DIAG\] session=(\S+) 独立局部峭值前5\(bpm,能量\)=(\[.*?\]) chosen_shadow_hr=([\d.eE+-]+|nan) shadow_track_est_after_pick=([\d.]+)")
seq={}
order=[]
with open(LOG,errors='ignore') as fh:
    for line in fh:
        m=pat.search(line)
        if not m: continue
        try: pk=ast.literal_eval(m.group(2)); ch=float(m.group(3))
        except: continue
        sid=m.group(1)
        if sid not in seq: seq[sid]=[]; order.append(sid)
        seq[sid].append((pk,ch,float(m.group(4))))

# 找最长会话，看 est 的时间演化 —— 是否单调爬升(棘轮)
sid=max(seq,key=lambda s:len(seq[s]))
s=seq[sid]
print(f"最长会话 {sid[:8]} 共{len(s)}轮 (每轮≈0.5s, 约{len(s)*0.5/60:.1f}分钟)")
ests=np.array([x[2] for x in s]); chs=np.array([x[1] for x in s])
tops=np.array([max([(b,e) for b,e in x[0] if 58<=b<=180] or [(np.nan,0)],key=lambda y:y[1])[0] for x in s])
print("\n轮次   est    chosen  带内最强峰")
for i in range(0,len(s),max(1,len(s)//25)):
    print(f"  {i:4d}  {ests[i]:6.1f}  {chs[i]:6.1f}   {tops[i]:6.1f}")

# 2倍谐波锁定的直接证据: chosen 是否≈2×(某个低频强峰)
print("\n=== 二次谐波锁定检验(全会话) ===")
n2=0; tot=0
for pk,ch,est in s:
    tot+=1
    inb=[(b,e) for b,e in pk if 58<=b<=180]
    if not inb: continue
    ce=next((e for b,e in inb if abs(b-ch)<=1.5),0.0)
    # chosen 的一半处是否有更强的峰
    for b,e in pk:
        if abs(b-ch/2)<=max(3,0.06*ch) and e>=ce:
            n2+=1; break
print(f"chosen≈2×(一个能量>=它的更低频峰) 的比例: {100*n2/tot:.1f}%")

print("\n=== 关键: est>120 的样本里, chosen/2 落在哪里 ===")
hi=[(pk,ch,est) for pk,ch,est in s if est>120]
if hi:
    halves=np.array([ch/2 for _,ch,_ in hi])
    print(f"n={len(hi)}  chosen均值={np.mean([c for _,c,_ in hi]):.1f}  chosen/2均值={halves.mean():.1f} bpm  <-- 这才是生理合理的真实心率")
