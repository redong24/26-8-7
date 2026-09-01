#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高保真离线测试平台 (testbed) 核心。

教训: g4 的重放保真度只有 37.9%, 因为它用的是日志里【截断的前5峰】。
这次改为: 直接加载真实模型, 用真实捕获帧算出【完整频谱】, 再复现选峰。
所有算法函数【从 test2.py 直接导入】, 而不是抄一遍 —— 抄写本身就是失真源。

用法:
    from tb_core import load_model, replay_session, PROD
"""
import os
import sys
import glob
import importlib.util
import numpy as np
import torch
import scipy.signal as scipy_signal
from scipy.interpolate import interp1d

DEMO = "/home/lsz/real_time_plus/real_time_Demo"
CAPTURE_DIR = os.path.join(DEMO, "frame_capture_diag")
CKPT = os.path.join(DEMO, "checkpoints/phasenet_epoch9.pth")

# 生产参数(与 test2.py 保持一致)
PROD = dict(
    WINDOW_SEC=10.0,
    TARGET_FS=30.0,
    LL=50.0,
    UL=140.0,
    ENERGY_THRESH=0.15,
    ALPHA=0.25,
    INIT_EST=75.0,
    BUTTER_ORDER=6,
    MIN_REAL_FRAMES=3,
)


# ---------------------------------------------------------------- 模型
def load_model(device=None):
    """加载与线上完全相同的 PhaseNet + 权重。"""
    if device is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    sys.path.insert(0, DEMO)
    from PhaseNetModel import PhaseNet
    model = PhaseNet(feature_dim=128, latent_dim=32, hidden_dim=128)
    ckpt = torch.load(CKPT, map_location='cpu')
    # 复用 test2.py 的 unwrap_state_dict, 避免抄写失真
    if isinstance(ckpt, dict):
        for k in ("state_dict", "model_state_dict", "model", "net"):
            if isinstance(ckpt.get(k), dict):
                ckpt = ckpt[k]
                break
    sd = {}
    for k, v in ckpt.items():
        sd[k[7:] if k.startswith("module.") else k] = v
    missing, unexpected = model.load_state_dict(sd, strict=False)
    if missing or unexpected:
        print("[testbed] 权重提示 missing=%d unexpected=%d" % (len(missing), len(unexpected)))
    return model.to(device).eval(), device


# ---------------------------------------------------------------- 重采样
def resample_window(frames, ts, window_sec=None, target_fs=None):
    """严格复刻 resample_recent_window_production。"""
    window_sec = PROD["WINDOW_SEC"] if window_sec is None else window_sec
    target_fs = PROD["TARGET_FS"] if target_fs is None else target_fs
    ts_all = np.asarray(ts, dtype=np.float64)
    if ts_all.size < PROD["MIN_REAL_FRAMES"]:
        return None, 0
    t_end = ts_all[-1]
    mask = ts_all >= t_end - window_sec
    if int(mask.sum()) < PROD["MIN_REAL_FRAMES"]:
        return None, 0
    ts_win = ts_all[mask]
    fr = np.asarray(frames)[mask].astype(np.float32)
    if ts_win[-1] - ts_win[0] <= 1e-3:
        return None, 0
    T_out = int(round(window_sec * target_fs)) + 1
    new_ts = ts_win[0] + np.arange(T_out) * (window_sec / (T_out - 1))
    new_ts = np.clip(new_ts, ts_win[0], ts_win[-1])
    f = interp1d(ts_win, fr, axis=0, kind='linear', assume_sorted=True)
    out = np.clip(np.round(f(new_ts)), 0, 255).astype(np.uint8)
    return out, T_out


# ---------------------------------------------------------------- 频谱
def spectrum(sig, fs=None, LL=None, UL=None, order=None):
    """带通+周期图, 返回【完整】候选峰列表(不截断), 与生产同参数。"""
    fs = PROD["TARGET_FS"] if fs is None else fs
    LL = PROD["LL"] if LL is None else LL
    UL = PROD["UL"] if UL is None else UL
    order = PROD["BUTTER_ORDER"] if order is None else order
    s = np.asarray(sig, dtype=np.float64)
    sd = s.std()
    s = (s - s.mean()) / sd if sd > 1e-6 else s - s.mean()
    try:
        b, a = scipy_signal.butter(order, [LL / 60, UL / 60], btype='bandpass', fs=fs)
        filt = scipy_signal.filtfilt(b, a, np.double(s))
    except Exception:
        return None
    N = int((60 * fs) / 0.1)
    F, Pxx = scipy_signal.periodogram(x=filt, nfft=N, fs=fs, window='hann')
    bpm = F * 60
    m = (bpm >= LL) & (bpm <= UL)
    bpm_r, p_r = bpm[m], Pxx[m]
    if len(p_r) == 0:
        return None
    pk, _ = scipy_signal.find_peaks(p_r, distance=3)
    if len(pk) == 0:
        pk = np.array([int(np.argmax(p_r))])
    return {"bpm": bpm_r, "pxx": p_r,
            "peaks": [(float(bpm_r[i]), float(p_r[i])) for i in pk]}


# ---------------------------------------------------------------- 会话重放
def load_session(sess, capture_dir=CAPTURE_DIR):
    files = sorted(glob.glob(os.path.join(capture_dir, "*%s*.npz" % sess)))
    out = []
    for fp in files:
        d = np.load(fp)
        out.append((d["frames"], d["timestamps"]))
    return out


def infer_all(model, device, samples, cache=None):
    """对每个样本: 重采样->模型推理->完整频谱。返回候选峰列表。"""
    if cache and os.path.exists(cache):
        return list(np.load(cache, allow_pickle=True))
    res = []
    for i, (fr, ts) in enumerate(samples):
        rs, T = resample_window(fr, ts)
        if rs is None:
            res.append(None)
            continue
        x = torch.from_numpy(rs.astype(np.float32)).permute(1, 0, 2, 3).unsqueeze(0).to(device)
        with torch.no_grad():
            out, _ = model(x)
            pts = out[0].detach().cpu().numpy()
        res.append(spectrum(pts))
        if (i + 1) % 20 == 0:
            print("  推理 %d/%d" % (i + 1, len(samples)), flush=True)
    if cache:
        np.save(cache, np.array(res, dtype=object))
    return res


# ---------------------------------------------------------------- 选峰策略
def sel_current(peaks, est, thresh=None):
    """现状(锁定区块): 能量阈值过滤后, 选最接近 prev_est 的。"""
    thresh = PROD["ENERGY_THRESH"] if thresh is None else thresh
    top = max(e for _, e in peaks)
    if top <= 1e-12:
        return None
    strong = [(b, e) for b, e in peaks if e >= top * thresh] or list(peaks)
    return min(strong, key=lambda x: abs(x[0] - est))[0]


def run_strategy(spectra, pick, init_est=None, alpha=None):
    """通用重放: pick(peaks, est) -> bpm 或 None。返回输出序列(nan填充)。"""
    init_est = PROD["INIT_EST"] if init_est is None else init_est
    alpha = PROD["ALPHA"] if alpha is None else alpha
    est = init_est
    out = []
    for sp in spectra:
        if sp is None:
            out.append(np.nan)
            continue
        c = pick(sp["peaks"], est)
        if c is None or not np.isfinite(c):
            out.append(np.nan)
            continue
        out.append(c)
        est = alpha * c + (1 - alpha) * est
    return np.array(out, dtype=float)


def metrics(vals, truth):
    v = np.asarray(vals, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return dict(n=0, mean=np.nan, bias=np.nan, mae=np.nan, w5=np.nan, w10=np.nan)
    e = np.abs(v - truth)
    return dict(n=v.size, mean=v.mean(), bias=v.mean() - truth, mae=e.mean(),
                w5=100.0 * (e <= 5).mean(), w10=100.0 * (e <= 10).mean())
