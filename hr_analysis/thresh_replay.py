import re, ast, numpy as np
LOG="/home/lsz/real_time_plus/real_time_Demo/output.log"
pat=re.compile(r"\[SHADOW-HR-DIAG\] session=(\S+) 独立局部峭值前5\(bpm,能量\)=(\[.*?\]) chosen_shadow_hr=([\d.eE+-]+|nan)")
seq={}
for line in open(LOG,errors='ignore'):
    m=pat.search(line)
    if not m: continue
    try: seq.setdefault(m.group(1),[]).append(ast.literal_eval(m.group(2)))
    except: pass
print("会话数:",len(seq),"总样本:",sum(len(v) for v in seq.values()))
print("\n在【已上线的10秒窗+[50,140]】基础上，再调能量阈值的效果:")
print(f"{'能量阈值':>8} {'均值':>8} {'中位':>8} {'>110占比':>9} {'与带内最强峰一致率':>18}")
for th in [0.15,0.30,0.45,0.60,0.80,1.00]:
    out=[];agree=0;n=0
    for sid,rows in seq.items():
        est=75.0
        for pk in rows:
            c=[(b,e) for b,e in pk if 50<=b<=140]
            if not c: continue
            top_b,top_e=max(c,key=lambda x:x[1])
            s=[(b,e) for b,e in c if e>=top_e*th] or c
            ch,_=min(s,key=lambda x:abs(x[0]-est))
            est=0.3*ch+0.7*est
            out.append(ch); n+=1
            if abs(ch-top_b)<=1.5: agree+=1
    o=np.array(out)
    print(f"{th:8.2f} {o.mean():8.2f} {np.median(o):8.2f} {100*(o>110).mean():8.1f}% {100*agree/n:17.1f}%")
print("\n注: 阈值=1.00 等价于'纯选带内能量最强峰'(完全禁用追踪偏好)")
