import re, statistics as st
L=open('/tmp/live_seg.log','rb').read().decode('utf8','ignore').splitlines()
app=[];prod=[]
for ln in L:
    if '[HR-DEBUG]' in ln:
        m=re.search(r'appended_last=(-?\d{1,3})(?![\d.])',ln)
        if m: app.append(int(m.group(1)))
    if '[SHADOW-W6-DEBUG]' in ln:
        m=re.search(r'production_hr=(\d+\.\d+)(?![\d.])',ln)
        if m: prod.append(float(m.group(1)))
print("appended_last (严格): n=%d 中位=%d 范围=[%d,%d]  超过上限60的次数=%d  回退(-1)=%d"%(
    len(app),st.median(app),min(app),max(app),sum(1 for x in app if x>60),sum(1 for x in app if x==-1)))
low=[x for x in prod if x<60]
print("\nproduction_hr <60 的样本: n=%d (%.2f%%)"%(len(low),100*len(low)/len(prod)))
if low: print("  这些值 = %s"%sorted(set(round(x,1) for x in low)))
print("  落在旧伪值带 [40,50) 的数量 = %d   <-- 修复2的直接目标"%sum(1 for x in prod if 40<=x<50))
print("  落在 [50,58) 的数量 = %d"%sum(1 for x in prod if 50<=x<58))
print("  最小值 = %.2f (LL_PR=58 下界附近)"%min(prod))
