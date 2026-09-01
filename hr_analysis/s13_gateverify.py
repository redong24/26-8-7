import numpy as np, glob, sys, os
sys.path.insert(0,"/home/lsz/real_time_plus/real_time_Demo")
os.chdir("/home/lsz/real_time_plus/real_time_Demo")
import importlib.util
spec=importlib.util.spec_from_file_location("t2","test2.py")
# 只取常量,不执行整个服务
src=open("test2.py").read()
ns={}
for line in src.split("\n"):
    if line.startswith(("SHADOW_QUALITY_GATE_ENABLED","SHADOW_GATE_MIN_REAL_FRAMES",
                        "SHADOW_GATE_MIN_SPAN_RATIO","SHADOW_WINDOW_SEC","SHADOW_HR_LL_PR","SHADOW_HR_UL_PR")):
        exec(line.split("#")[0].strip(),ns)
G_ON=ns['SHADOW_QUALITY_GATE_ENABLED']; G_N=ns['SHADOW_GATE_MIN_REAL_FRAMES']
G_R=ns['SHADOW_GATE_MIN_SPAN_RATIO']; W=ns['SHADOW_WINDOW_SEC']
print("从test2.py读出的常量:")
print("  门控启用=%s  最小真实帧=%d  最小跨度比=%.2f  窗口=%.1fs"%(G_ON,G_N,G_R,W))
print("  => 跨度门槛=%.2fs, 等效最低帧率=%.1ffps"%(W*G_R,G_N/W))
print()
fs=sorted(glob.glob("frame_capture_diag/capture_4b989b29_*.npz"))
npass=0; rej=0; rn=[];rs=[]
for f in fs:
    ts=np.load(f)['timestamps'].astype(np.float64)
    tw=ts[ts>=ts[-1]-W]
    n=len(tw); span=float(tw[-1]-tw[0]) if n>=2 else 0.0
    ok = (n>=G_N and span>=W*G_R)
    if ok: npass+=1
    else:
        rej+=1; rn.append(n); rs.append(span)
print("对受试者B全部120份样本套用门控:")
print("  通过 = %d (%.0f%%)   拦截 = %d (%.0f%%)"%(npass,100*npass/len(fs),rej,100*rej/len(fs)))
print("  被拦截样本: 真实帧数中位=%.0f  跨度中位=%.1fs"%(np.median(rn),np.median(rs)))
print()
print("对照离线实测预期: 门控后 n=46 (38%), MAE 10.93->4.22")
print("本次门控通过 n=%d (%.0f%%)  -> %s"%(npass,100*npass/len(fs),
      "与预期一致" if abs(npass-46)<=8 else "偏差较大需复核"))
