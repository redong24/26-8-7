import re, ast, numpy as np
NEW="/home/lsz/real_time_plus/real_time_Demo/output.log"
import glob
OLDS=sorted(glob.glob("/home/lsz/real_time_plus/real_time_Demo/output.log.before_bandfix_*"))
pat=re.compile(r"\[SHADOW-HR-DIAG\].*?独立局部峭值前5\(bpm,能量\)=(\[.*?\]) chosen_shadow_hr=([\d.eE+-]+|nan)")
patT=re.compile(r"shadow_T=(\d+)")
def load(f):
    v=[];Ts=set()
    for line in open(f,errors='ignore'):
        m=patT.search(line)
        if m: Ts.add(int(m.group(1)))
        m=pat.search(line)
        if not m: continue
        try: v.append(float(m.group(2)))
        except: pass
    return np.array(v),Ts
new,Tn=load(NEW)
old,To=load(OLDS[-1]) if OLDS else (np.array([]),set())
print(f"重采样点数 T: 修复前={sorted(To)} 修复后={sorted(Tn)}  (181=6秒窗, 301=10秒窗)\n")
for nm,a in [("修复前(6秒窗,[58,180])",old),("修复后(10秒窗,[50,140])",new)]:
    if len(a)==0: continue
    print(f"{nm}: n={len(a):5d} 均值={a.mean():6.2f} 中位数={np.median(a):6.2f} "
          f"p25={np.percentile(a,25):5.1f} p75={np.percentile(a,75):6.1f}")
    print(f"{'':24s} >110bpm占比={100*(a>110).mean():5.1f}%  >120bpm占比={100*(a>120).mean():5.1f}%  最大={a.max():.1f}")
if len(new) and len(old):
    print(f"\n均值下降 {old.mean()-new.mean():+.2f} bpm；>110占比 {100*(old>110).mean():.1f}% -> {100*(new>110).mean():.1f}%")
    print(f"输出上界: 修复前max={old.max():.1f} 修复后max={new.max():.1f} (UL=140生效)")
