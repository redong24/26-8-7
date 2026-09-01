import re, statistics as st, math
L=open('/tmp/live_seg.log','rb').read().decode('utf8','ignore').splitlines()

fs=[];span=[];appended=[];newhr=[];nan_n=0;band_inv=0;band_skips=[];prod=[];disp=[]
for ln in L:
    if '[HR-DEBUG]' in ln:
        m=re.search(r'effective_fs=(\d+\.\d+)',ln); m2=re.search(r'time_span=(\d+\.\d+)s',ln); m3=re.search(r'appended_last=(-?\d+)',ln)
        if m: fs.append(float(m.group(1)))
        if m2: span.append(float(m2.group(1)))
        if m3: appended.append(int(m3.group(1)))
    if '[NEW-HR-DEBUG]' in ln:
        m=re.search(r'new_hr=(nan|-?\d+\.\d+)',ln)
        if m:
            v=m.group(1)
            if v=='nan': nan_n+=1
            else: newhr.append(float(v))
        if re.search(r'band_invalid=True',ln): band_inv+=1
        m4=re.search(r'band_skips=(\d+)',ln)
        if m4: band_skips.append(int(m4.group(1)))
    if '[SHADOW-W6-DEBUG]' in ln:
        m=re.search(r'production_hr=(\d+\.\d+)',ln); m2=re.search(r'disp_hr=(\d+\.\d+)',ln)
        if m: prod.append(float(m.group(1)))
        if m2: disp.append(float(m2.group(1)))

def pct(a,f): return 100.0*sum(1 for x in a if f(x))/len(a) if a else float('nan')
def q(a,p):
    a=sorted(a); return a[min(len(a)-1,int(p*len(a)))] if a else float('nan')

print("样本量: HR-DEBUG=%d NEW-HR-DEBUG=%d SHADOW=%d"%(len(fs),len(newhr)+nan_n,len(prod)))
print("\n--- 1. 缓冲密度 (修复1) ---")
if fs:
    print("effective_fs  中位=%.2f  P10=%.2f  P90=%.2f"%(st.median(fs),q(fs,.10),q(fs,.90)))
    print("被强制成30.0的比例 = %.2f%%  (修复前 100%%)"%pct(fs,lambda x: abs(x-30.0)<1e-6))
if span: print("time_span 中位=%.2fs  P10=%.2f  P90=%.2f  (修复前 290.2s)"%(st.median(span),q(span,.10),q(span,.90)))
if appended:
    print("appended_last 中位=%d  最小=%d  最大=%d"%(st.median(appended),min(appended),max(appended)))
    print("回退路径(-1)次数 = %d"%sum(1 for x in appended if x==-1))
print("\n--- 2. 回退门 (修复2) ---")
tot=len(newhr)+nan_n
print("new_hr=nan 比例 = %.2f%% (%d/%d)  [修复2前基线 8.91%%]"%(100.0*nan_n/tot if tot else float('nan'),nan_n,tot))
print("band_invalid=True 出现次数 = %d"%band_inv)
print("band_skips 累计计数器最终值 = %s"%(max(band_skips) if band_skips else 'n/a'))
if newhr: print("new_hr 有效值: 均值=%.2f 中位=%.2f 范围=[%.1f, %.1f]"%(st.mean(newhr),st.median(newhr),min(newhr),max(newhr)))
print("\n--- 3. 🔒生产链 production_hr (非用户可见) ---")
if prod:
    print("n=%d 均值=%.2f 中位=%.2f 标准差=%.2f 范围=[%.1f, %.1f]"%(len(prod),st.mean(prod),st.median(prod),st.pstdev(prod),min(prod),max(prod)))
    print("<60 占比 = %.2f%%    [修复前 9.5%%, 离线预测 0.6%%]"%pct(prod,lambda x:x<60))
    print("在[60,100] = %.2f%%  [修复前 78.3%%, 离线预测 86.1%%]"%pct(prod,lambda x:60<=x<=100))
print("\n--- 4. /max 显示值 disp_hr (用户可见, 影子链) ---")
if disp:
    j=[abs(disp[i]-disp[i-1]) for i in range(1,len(disp))]
    print("n=%d 均值=%.2f 中位=%.2f 标准差=%.2f 范围=[%.1f, %.1f]"%(len(disp),st.mean(disp),st.median(disp),st.pstdev(disp),min(disp),max(disp)))
    print("在[60,100] = %.2f%%"%pct(disp,lambda x:60<=x<=100))
    if j: print("逐次跳变 jitter: 中位=%.2f P90=%.2f 最大=%.2f"%(st.median(j),q(j,.90),max(j)))
