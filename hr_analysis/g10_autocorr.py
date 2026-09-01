import re, numpy as np
LOG="/home/lsz/real_time_plus/real_time_Demo/output.log.before_gate_20260810_164015"
S="4b989b29"; T=78.3
pat=re.compile(r"独立局部[峰峭]值前5\(bpm,能量\)=\[(.*?)\] chosen_shadow_hr=([0-9.]+)")
pt=re.compile(r"\(([0-9.]+),\s*([0-9.]+)\)")
ch,st=[],[]
for line in open(LOG,encoding="utf-8",errors="ignore"):
    if S not in line or "SHADOW-HR-DIAG" not in line: continue
    m=pat.search(line)
    if not m: continue
    pk=pt.findall(m.group(1))
    if pk: ch.append(float(m.group(2))); st.append(float(pk[0][0]))
ch=np.array(ch); st=np.array(st)
def ac(x,lag):
    a=x[:-lag]-x[:-lag].mean(); b=x[lag:]-x[lag:].mean()
    return float((a*b).mean()/(a.std()*b.std()))
print("误差自相关 (误差越'黏', 平均越无效)")
print("%-12s %8s %8s %8s %8s"%("lag","10","30","60","120"))
for nm,v in [("追踪器",ch-T),("最强峰",st-T)]:
    print("%-12s %8.3f %8.3f %8.3f %8.3f"%(nm,ac(v,10),ac(v,30),ac(v,60),ac(v,120)))
print("\n误差符号: 追踪器为正的比例=%.1f%% | 最强峰=%.1f%%"%(100*(ch>T).mean(),100*(st>T).mean()))
print("误差标准差: 追踪器=%.2f | 最强峰=%.2f"%((ch-T).std(),(st-T).std()))
