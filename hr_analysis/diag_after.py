import re, ast, numpy as np
LOG="/home/lsz/real_time_plus/real_time_Demo/output.log"
pat=re.compile(r"\[SHADOW-HR-DIAG\] session=(\S+) 独立局部峭值前5\(bpm,能量\)=(\[.*?\]) chosen_shadow_hr=([\d.eE+-]+|nan)")
pfs=re.compile(r"\[HR-DEBUG\] session=(\S+) effective_fs=([\d.]+) time_span=([\d.]+)s")
rows=[];fss=[]
for line in open(LOG,errors='ignore'):
    m=pat.search(line)
    if m:
        try: rows.append((ast.literal_eval(m.group(2)), float(m.group(3))))
        except: pass
    m2=pfs.search(line)
    if m2: fss.append(float(m2.group(2)))
print(f"样本 n={len(rows)}")
fs=np.array(fss)
print(f"真实帧率 effective_fs: 中位数={np.median(fs):.2f} p25={np.percentile(fs,25):.2f} p75={np.percentile(fs,75):.2f}")
print(f"  10秒窗内真实帧数中位≈{np.median(fs)*10:.0f} 帧 -> 插值到301点")

ch=np.array([c for _,c in rows])
# 带内(50-140)最强峰
tops=[]
for pk,c in rows:
    inb=[(b,e) for b,e in pk if 50<=b<=140]
    tops.append(max(inb,key=lambda x:x[1])[0] if inb else np.nan)
tops=np.array(tops)
print(f"\nchosen均值={ch.mean():.2f}  带内[50,140]最强峰均值={np.nanmean(tops):.2f}")
print(f"chosen==带内最强峰 的比例={100*np.nanmean(np.abs(ch-tops)<=1.5):.1f}%")

# 关键: 谱峰本身就在高位吗?
allpk=[]
for pk,c in rows: allpk += [b for b,e in pk]
allpk=np.array(allpk)
print(f"\n所有诊断峭值(15~200)分布: 中位={np.median(allpk):.1f}")
h,ed=np.histogram(allpk,bins=[15,40,60,80,100,120,140,160,200])
for i in range(len(h)): print(f"   [{ed[i]:3.0f},{ed[i+1]:3.0f}): {h[i]:5d}  {100*h[i]/len(allpk):5.1f}%")

# 边界堆积检查
print(f"\n边界堆积: chosen在[135,140]的占比={100*((ch>=135)&(ch<=140)).mean():.1f}%")
print(f"          chosen在[50,55] 的占比={100*((ch>=50)&(ch<=55)).mean():.1f}%")
# 130+ 强峰是否被截断
tr=0
for pk,c in rows:
    if any(b>140 and e>=max([x[1] for x in pk]) for b,e in pk): tr+=1
print(f"最强峭值落在140以上(被截断)的样本占比={100*tr/len(rows):.1f}%")
