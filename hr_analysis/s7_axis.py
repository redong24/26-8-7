import numpy as np
from scipy.interpolate import interp1d
from scipy import signal as ss
print("="*66)
print("验证A: 重采样时间轴是否被压缩(我的头号嫌疑)")
print("="*66)
# 293-295行逻辑: T_out=round(window_sec*target_fs)+1
#                new_ts = ts_win[0] + arange(T_out)*(window_sec/(T_out-1))
window_sec=10.0; target_fs=30.0
T_out=int(round(window_sec*target_fs))+1
step=window_sec/(T_out-1)
print("T_out=%d  步长=%.6fs  覆盖=%.4fs"%(T_out,step,step*(T_out-1)))
print("下游 shadow_fs = SHADOW_TARGET_FS = 30.0")
print("重采样后真实等效fs = (T_out-1)/window_sec = %.2f"%((T_out-1)/window_sec))
print("-> 两者%s"%("一致, 时间轴【没有】被压缩"if abs((T_out-1)/window_sec-30.0)<1e-9 else "不一致!"))
print()
# 合成已知频率信号端到端验证
for true_bpm in [60,78,100]:
    fs_real=13.9
    n=int(fs_real*window_sec)
    ts=np.arange(n)/fs_real
    sig=np.sin(2*np.pi*(true_bpm/60)*ts)
    fr=np.repeat(sig[:,None,None,None],3,axis=1)*40+128
    T=T_out
    new_ts=ts[0]+np.arange(T)*(window_sec/(T-1))
    new_ts=np.clip(new_ts,ts[0],ts[-1])
    out=interp1d(ts,fr,axis=0,kind='linear',assume_sorted=True)(new_ts)
    s=out[:,0,0,0]; s=(s-s.mean())/s.std()
    fq,px=ss.periodogram(s,fs=30.0,window='hann',nfft=8192)
    bpm=fq*60; m=(bpm>=40)&(bpm<=180)
    est=bpm[m][np.argmax(px[m])]
    print("真实%3dbpm -> 重采样+FS=30解出 %.1f bpm  误差%+.1f"%(true_bpm,est,est-true_bpm))
print()
print("结论: 时间轴与频率标定【正确】, 不存在2.16倍放大。头号嫌疑排除。")
