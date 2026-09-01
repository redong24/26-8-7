"""
tb17: 验证节流引入的真实回归 —— rppg_plot_buffer 时间密度坍缩
规则A(现状): 每次推理只 append 输出的最后1点
规则B(提议): 每次推理 append 最后K点(K=距上次推理新到达的帧数)
"""
import numpy as np, torch, glob, os, sys
sys.path.insert(0, '/home/lsz/real_time_plus/real_time_Demo')
os.chdir('/home/lsz/real_time_plus/real_time_Demo')
from PhaseNetModel import PhaseNet
from scipy import signal as ss

m = PhaseNet(feature_dim=128, latent_dim=32, hidden_dim=128)
sd = torch.load('./checkpoints/phasenet_epoch9.pth', map_location='cpu')
if isinstance(sd, dict) and 'state_dict' in sd: sd = sd['state_dict']
m.load_state_dict(sd); m.eval()

fs = sorted(glob.glob('frame_capture_diag/capture_1b0d780a_*.npz'), key=os.path.getmtime)
print(f"可用采集: {len(fs)}")

# 只取时间上连续(有重叠)的一串
runs = []
cur = []
prev_ts = None
for p in fs:
    ts = np.load(p)['timestamps']
    if prev_ts is not None and np.intersect1d(prev_ts, ts).size > 0:
        cur.append(p)
    else:
        if len(cur) >= 3: runs.append(cur)
        cur = [p]
    prev_ts = ts
if len(cur) >= 3: runs.append(cur)
runs.sort(key=len, reverse=True)
print(f"连续段数={len(runs)} 最长段长度={len(runs[0]) if runs else 0}")
if not runs:
    print("无连续段, 无法验证"); sys.exit(0)
run = runs[0]

# 逐份推理
outs = []
for p in run:
    d = np.load(p)
    fr = d['frames']; ts = d['timestamps']
    x = torch.from_numpy(fr).float().permute(1,0,2,3).unsqueeze(0)
    with torch.no_grad(): o,_ = m(x)
    outs.append((ts, o[0].numpy()))
    print(f"  {os.path.basename(p)[-20:]} ok")

def spec_hr(sig, fsamp, lo=45, hi=110):
    if len(sig) < 16 or fsamp <= 2*hi/60: return float('nan')
    sig = (sig - sig.mean())/(sig.std()+1e-9)
    try:
        b,a = ss.butter(6, [lo/60, hi/60], btype='bandpass', fs=fsamp)
        f2 = ss.filtfilt(b,a,np.double(sig))
    except Exception: return float('nan')
    N = int((60*fsamp)/0.1)
    F,P = ss.periodogram(f2, nfft=N, fs=fsamp, window='hann')
    bpm = F*60; mk = (bpm>=lo)&(bpm<=hi)
    if mk.sum()==0: return float('nan')
    return float(bpm[mk][np.argmax(P[mk])])

# 规则A: 每份只取最后1点
tsA = np.array([t[-1] for t,_ in outs])
sigA = np.array([o[-1] for _,o in outs])
spanA = tsA[-1]-tsA[0]; fsA = (len(tsA)-1)/spanA
print(f"\n规则A(每次1点): n={len(sigA)} span={spanA:.1f}s fs={fsA:.3f}  HR={spec_hr(sigA,fsA):.2f}")
print(f"  若按代码兜底误用 FS=30: HR={spec_hr(sigA,30.0):.2f}")

# 规则B: 拼接新增部分
ts_all=[]; sg_all=[]
last_t = -np.inf
for t,o in outs:
    mk = t > last_t
    ts_all.append(t[mk]); sg_all.append(o[mk])
    if t.size: last_t = t[-1]
tsB = np.concatenate(ts_all); sigB = np.concatenate(sg_all)
order = np.argsort(tsB); tsB=tsB[order]; sigB=sigB[order]
spanB = tsB[-1]-tsB[0]; fsB=(len(tsB)-1)/spanB
print(f"\n规则B(拼接新增): n={len(sigB)} span={spanB:.1f}s fs={fsB:.3f}")
# 用最近135点窗口, 与生产逻辑一致
for W in (135,):
    s = sigB[-W:]; t = tsB[-W:]
    sp = t[-1]-t[0]; f_ = (len(t)-1)/sp
    print(f"  末{W}点: span={sp:.2f}s fs={f_:.2f}  HR={spec_hr(s,f_):.2f}")
# 滑窗稳定性
hrs=[]
for i in range(0, max(1,len(sigB)-135), 20):
    s=sigB[i:i+135]; t=tsB[i:i+135]
    if len(s)<135: break
    sp=t[-1]-t[0]
    if sp<=0.1: continue
    hrs.append(spec_hr(s,(len(t)-1)/sp))
hrs=np.array([h for h in hrs if np.isfinite(h)])
if len(hrs): print(f"  滑窗HR n={len(hrs)} mean={hrs.mean():.2f} std={hrs.std():.2f} in[60,100]={100*np.mean((hrs>=60)&(hrs<=100)):.0f}%")
