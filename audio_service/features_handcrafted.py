# -*- coding: utf-8 -*-
"""
轨 A：手工声学特征提取（语言无关，中文英文通用）
================================================================
设计依据与边界（重要，勿删）：

1) 语言无关性
   F0 / jitter / shimmer / 频谱 / 语速 / 停顿 都是物理声学量，
   与语种无关，中文可直接使用。

2) 中文声调的处理【关键】
   普通话四声主要由 F0 曲线实现，因此 F0 的方差/范围会被词汇声调
   严重污染。本模块的应对是「任务侧约束」而非「算法侧校正」：
   要求前端使用【固定朗读文本】——所有用户读同一句话，声调序列
   恒定，声调对 F0 方差的贡献成为常数偏移，跨用户比较时自然抵消。
   ⚠️ 若前端改为「自由叙述」，f0_std / f0_range 的跨用户可比性
   即失效，必须改用强制对齐 + 按声调归一化，本模块不支持该场景。

3) jitter / shimmer 的可靠性边界【关键】
   两者是基频周期扰动测量，只在「持续元音 + 高信噪比」下稳定；
   在消费级麦克风录制的连续语音上，它们对麦克风型号与信噪比的
   敏感度会超过对说话人状态的敏感度（Praat 官方文档亦有此警告）。
   因此本模块只在 sustained_vowel 段计算 jitter/shimmer，
   并且始终附带 snr_db 与 reliable 标记，供上层决定是否采用。

4) 本模块只输出「可观测的测量量」，不做任何推断性判断。
   状态量的合成（五维画像等）由上层完成，不在此处。
"""
from __future__ import annotations
import math
import numpy as np

TARGET_SR = 16000          # 特征提取统一采样率（模型侧亦用此）
PRAAT_SR_MIN = 44100       # 周期扰动测量建议的最低采样率

# 输入电平下限：低于此值判定为「麦克风未拾音 / 静音 / 权限被拒」
# 依据：正常语音录音峰值在 -20 ~ -3 dBFS；静音轨道通常低于 -60 dBFS。
# ⚠️ 必须在峰值归一化【之前】检查 —— 归一化会把 -86dBFS 的底噪
# 放大到满幅，使 VAD 把纯噪声判成连续语音（该缺陷已由冒烟测试 E 暴露）。
MIN_PEAK_DBFS = -45.0
MIN_RMS_DBFS = -60.0

# VAD 退化判据：稳态信号上 webrtcvad 会大面积误判，
# 此时「语音帧/非语音帧」的功率比不再是信噪比，必须拒绝给值。
VAD_DEGENERATE_LO = 0.10   # 语音帧占比过低
VAD_DEGENERATE_HI = 0.95   # 语音帧占比过高（无噪声底可测）
VAD_MIN_FRAMES = 3         # 两类各自至少需要的帧数

# 持续元音段的质量门限改用 HNR（谐噪比）而非 VAD-SNR：
# 持续元音本身没有静音帧，无法测 VAD-SNR；HNR 测的是浊音内部
# 周期成分与非周期成分之比，正是此处需要的量。
MIN_HNR_DB = 15.0


# ---------------------------------------------------------------- 工具

def _safe(v, nd=4):
    """把 numpy / nan / inf 统一成 JSON 可序列化的干净值。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def _stats(arr, prefix, nd=4):
    """一组数值的统计摘要。空数组返回全 None，不抛异常。"""
    a = np.asarray([x for x in np.ravel(arr) if x is not None and np.isfinite(x)],
                   dtype=float)
    if a.size == 0:
        return {f"{prefix}_{k}": None
                for k in ("mean", "std", "min", "max", "range", "p05", "p95")}
    return {
        f"{prefix}_mean":  _safe(a.mean(), nd),
        f"{prefix}_std":   _safe(a.std(), nd),
        f"{prefix}_min":   _safe(a.min(), nd),
        f"{prefix}_max":   _safe(a.max(), nd),
        f"{prefix}_range": _safe(a.max() - a.min(), nd),
        f"{prefix}_p05":   _safe(np.percentile(a, 5), nd),
        f"{prefix}_p95":   _safe(np.percentile(a, 95), nd),
    }


# ---------------------------------------------------------------- VAD / 停顿

def voice_activity(y, sr, frame_ms=30, aggressiveness=2):
    """
    用 webrtcvad 做语音活动检测，返回逐帧布尔序列与帧长。
    webrtcvad 只接受 8/16/32/48kHz 的 16-bit 单声道 PCM，
    且帧长必须是 10/20/30ms，因此这里固定重采样到 16k + 30ms 帧。
    """
    import webrtcvad
    import librosa

    if sr != TARGET_SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=TARGET_SR)
        sr = TARGET_SR

    pcm = np.clip(y, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16).tobytes()

    vad = webrtcvad.Vad(aggressiveness)
    n = int(sr * frame_ms / 1000)          # 每帧采样数
    step = n * 2                           # int16 = 2 bytes
    flags = []
    for i in range(0, len(pcm) - step + 1, step):
        try:
            flags.append(vad.is_speech(pcm[i:i + step], sr))
        except Exception:
            flags.append(False)
    return np.asarray(flags, dtype=bool), frame_ms / 1000.0


def _voiced_edge(speech_flags, min_run):
    """
    找「稳定发声」的首/末帧下标，返回 (head, tail)；找不到返回 (None, None)。

    为什么不能直接用 idx[0] / idx[-1]：
      span 是语速的分母，端点由【单帧】决定时，一个孤立的 VAD 误判帧
      就能把整段首尾静音全拉进分母。实测（19s 合成音，真实发声 3.0→14.6s，
      真值 span=11.60s）：头部静音里有 3 个连续误判帧落在 0.03~0.09s，
      于是 span 被撑到 14.73s —— 虚高 27%，语速被系统性低估 21%。
      这恰好把 ask#30 要消除的「按下录音后的犹豫」又放回了分母里。

    因此端点改为要求【连续 min_run 帧】都是语音才认定。min_run 的依据是
    汉语最短单音节时长约 150~200ms，取 120ms（30ms 帧 × 4）留安全余量：
    实测 150ms / 200ms 的真实起始音都能保住（误差 +0.12/+0.13s，
    即帧量化量级），而 90ms 的孤立杂音被正确跳过。
    """
    n = len(speech_flags)
    head = None
    run = 0
    for i in range(n):
        if speech_flags[i]:
            run += 1
            if run >= min_run:
                head = i - min_run + 1     # 回退到这段连续发声的起点
                break
        else:
            run = 0
    tail = None
    run = 0
    for i in range(n - 1, -1, -1):
        if speech_flags[i]:
            run += 1
            if run >= min_run:
                tail = i + run - 1         # 前进到这段连续发声的终点
                break
        else:
            run = 0
    if head is None or tail is None or tail < head:
        return None, None
    return head, tail


def rhythm_features(speech_flags, frame_sec, min_pause_sec=0.25,
                    min_voiced_run_sec=0.12):
    """
    节奏/停顿特征。停顿定义为「连续 >= min_pause_sec 的非语音段」，
    250ms 阈值用于排除塞音闭塞、字间自然间隙这类非停顿静音。
    首尾静音不计入停顿（那是「还没开口 / 已说完」，非语言停顿）。

    min_voiced_run_sec：认定「开口/说完」所需的最短连续发声时长，
    用于防止孤立 VAD 误判帧把首尾静音拉进 span（详见 _voiced_edge）。
    """
    total = len(speech_flags) * frame_sec
    if len(speech_flags) == 0 or not speech_flags.any():
        return {"duration_sec": _safe(total), "speech_sec": 0.0,
                "span_sec": 0.0,
                "speech_ratio": 0.0, "pause_count": 0,
                "pause_total_sec": 0.0, "pause_ratio": 0.0,
                "pause_mean_sec": None, "pause_max_sec": None,
                "reliable": False}

    # 至少 1 帧，避免 frame_sec 偏大时 min_run 退化成 0
    min_run = max(1, int(round(min_voiced_run_sec / frame_sec)))
    head, tail = _voiced_edge(speech_flags, min_run)
    if head is None:
        # 全程没有任何一段达到 min_run 的连续发声：只有零星帧，
        # 不足以支撑「说过话」的判断。span 归零并标记不可靠，
        # 由上层据此把语速置 None（0 会被当成「语速为零」这个真实数据点）。
        return {"duration_sec": _safe(total), "speech_sec": 0.0,
                "span_sec": 0.0,
                "speech_ratio": 0.0, "pause_count": 0,
                "pause_total_sec": 0.0, "pause_ratio": 0.0,
                "pause_mean_sec": None, "pause_max_sec": None,
                "reliable": False}
    core = speech_flags[head:tail + 1]

    pauses, run = [], 0
    for f in core:
        if f:
            if run:
                pauses.append(run)
                run = 0
        else:
            run += 1
    if run:
        pauses.append(run)

    pause_secs = [p * frame_sec for p in pauses if p * frame_sec >= min_pause_sec]
    core_sec = len(core) * frame_sec
    speech_sec = int(core.sum()) * frame_sec

    return {
        "duration_sec":    _safe(total),
        "speech_sec":      _safe(speech_sec),
        # span_sec = 掐掉首尾静音后的【跨度】，即「第一个字起 → 最后一个字止」，
        # 内部的停顿【计入】。这才是朗读语速的正确分母：
        #   - 用 duration_sec 会把「按下录音后的犹豫」和「读完后忘记点停止」
        #     算进去，用户手速直接污染语速；
        #   - 用 speech_sec（纯发声时长，已剔除所有停顿）会把停顿时间凭空抹掉，
        #     一个读得慢但停顿多的人会被算成语速正常 —— 而停顿多本身就是
        #     要观察的信号，不能在分母里被消掉。
        # 取 span 后，用户提前/延迟点「完成」都不影响结果，
        # 这正是「不锁定固定时长、按实际用时计算」所必需的稳健性。
        "span_sec":        _safe(core_sec),
        "speech_ratio":    _safe(speech_sec / core_sec if core_sec else 0),
        "pause_count":     len(pause_secs),
        "pause_total_sec": _safe(sum(pause_secs)),
        "pause_ratio":     _safe(sum(pause_secs) / core_sec if core_sec else 0),
        "pause_mean_sec":  _safe(np.mean(pause_secs)) if pause_secs else None,
        "pause_max_sec":   _safe(max(pause_secs)) if pause_secs else None,
        "reliable":        bool(speech_sec >= 3.0),   # 少于 3s 有效语音不可信
    }


# ---------------------------------------------------------------- 音高 / 能量 / 频谱

def pitch_features(y, sr, fmin=60.0, fmax=450.0):
    """
    F0 特征。用 librosa.pyin（概率 YIN），对噪声比自相关法稳健。
    fmin/fmax 覆盖成人男女声基频范围（男 ~85-180，女 ~165-255，
    留出余量到 60-450 以容纳个体差异与轻微倍频误判）。

    ⚠️ 中文声调提醒：f0_std / f0_range 含声调贡献，
    只在「固定文本」条件下具备跨用户可比性（见模块头注）。
    半音（semitone）尺度比 Hz 更接近听觉感知，故一并输出。
    """
    import librosa
    try:
        f0, voiced, _ = librosa.pyin(y, sr=sr, fmin=fmin, fmax=fmax,
                                     frame_length=2048)
    except Exception as e:
        return {"f0_error": str(e)[:80], "voiced_ratio": None}

    f0v = f0[~np.isnan(f0)]
    out = _stats(f0v, "f0", nd=2)
    out["voiced_ratio"] = _safe(float(np.mean(~np.isnan(f0))))

    # 半音尺度（相对 f0 中位数），听觉上等距
    if f0v.size > 1:
        ref = float(np.median(f0v))
        st = 12.0 * np.log2(f0v / ref)
        out["f0_semitone_std"]   = _safe(st.std(), 3)
        out["f0_semitone_range"] = _safe(np.percentile(st, 95) - np.percentile(st, 5), 3)
    else:
        out["f0_semitone_std"] = out["f0_semitone_range"] = None
    return out


def energy_features(y, sr, frame_length=2048, hop_length=512):
    """RMS 能量与响度稳定性。variation 用变异系数，与绝对增益无关。"""
    import librosa
    rms = librosa.feature.rms(y=y, frame_length=frame_length,
                              hop_length=hop_length)[0]
    out = _stats(rms, "rms", nd=5)
    m = float(np.mean(rms)) if rms.size else 0.0
    out["rms_variation"] = _safe(float(np.std(rms) / m) if m > 1e-9 else None, 4)

    db = librosa.amplitude_to_db(np.maximum(rms, 1e-10), ref=1.0)
    out["loudness_db_mean"] = _safe(np.mean(db), 2)
    out["loudness_db_std"]  = _safe(np.std(db), 2)
    return out


def spectral_features(y, sr, n_mfcc=13):
    """MFCC / 谱质心 / 带宽 / 滚降 / 零率 / 谱通量 的统计摘要。"""
    import librosa
    out = {}
    try:
        mf = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc)
        for i in range(n_mfcc):
            out[f"mfcc{i+1}_mean"] = _safe(mf[i].mean(), 3)
            out[f"mfcc{i+1}_std"]  = _safe(mf[i].std(), 3)

        out.update(_stats(librosa.feature.spectral_centroid(y=y, sr=sr)[0],
                          "spec_centroid", 1))
        out.update(_stats(librosa.feature.spectral_bandwidth(y=y, sr=sr)[0],
                          "spec_bandwidth", 1))
        out.update(_stats(librosa.feature.spectral_rolloff(y=y, sr=sr)[0],
                          "spec_rolloff", 1))
        out.update(_stats(librosa.feature.zero_crossing_rate(y)[0], "zcr", 4))

        S = np.abs(librosa.stft(y))
        flux = np.sqrt(((np.diff(S, axis=1)) ** 2).sum(axis=0))
        out.update(_stats(flux, "spec_flux", 3))
    except Exception as e:
        out["spectral_error"] = str(e)[:80]
    return out


def estimate_snr_db(y, speech_flags, frame_sec, sr):
    """
    用 VAD 分段粗估 SNR：语音帧功率 / 非语音帧功率。

    ⚠️ 该估计【只在「语音与静音交替」的信号上成立】。
    在持续元音这类稳态信号上，webrtcvad 会把大部分帧误判为非语音
    （实测 3s 稳态元音：100 帧中仅 3 帧判为语音），此时所谓
    「非语音帧」其实是满幅元音，功率比恒等于 1 → 得出 0dB 的假信噪比。

    因此这里在两类帧占比退化时【拒绝给值】（返回 None + 原因），
    而不是返回一个看似精确、实际无意义的数字。
    返回：(snr_db 或 None, 说明字符串 或 None)
    """
    if len(speech_flags) == 0:
        return None, "无 VAD 帧"

    ratio = float(speech_flags.sum()) / len(speech_flags)
    n = int(sr * frame_sec)
    sp, ns = [], []
    for i, f in enumerate(speech_flags):
        seg = y[i * n:(i + 1) * n]
        if seg.size == 0:
            continue
        (sp if f else ns).append(float(np.mean(seg ** 2)))

    if len(sp) < VAD_MIN_FRAMES or len(ns) < VAD_MIN_FRAMES:
        return None, (f"VAD 分段退化（语音帧 {len(sp)} / 非语音帧 {len(ns)}）："
                      "缺少可比对的噪声底，VAD-SNR 不适用")
    if not (VAD_DEGENERATE_LO <= ratio <= VAD_DEGENERATE_HI):
        return None, (f"VAD 语音帧占比 {ratio:.2f} 落在退化区间外："
                      "稳态信号上 VAD-SNR 不适用（持续元音请改用 HNR）")

    ps, pn = float(np.mean(sp)), float(np.mean(ns))
    if pn <= 1e-12 or ps <= 1e-12:
        return None, "帧功率过低，无法估计信噪比"

    snr = 10.0 * math.log10(ps / pn)
    # 功率比接近 1（|snr| < 1dB）说明两类帧其实是同一种内容
    if abs(snr) < 1.0:
        return None, (f"语音帧与非语音帧功率几乎相同（{snr:.2f}dB）："
                      "VAD 未能真正区分语音与噪声，该估计无效")
    return _safe(snr, 1), None


# ---------------------------------------------------------------- 嗓音质量（受限）

def voice_quality_features(y, sr, min_snr_db=15.0, snr_db=None,
                           is_sustained_vowel=False):
    """
    jitter / shimmer / HNR —— 周期扰动测量。

    ⚠️⚠️ 本函数的输出【默认不可信】，除非同时满足：
        (1) is_sustained_vowel=True   —— 只在持续元音段测量
        (2) snr_db >= min_snr_db      —— 信噪比达标
        (3) sr >= 44100               —— 采样率足够解析周期微扰

    原因：jitter 量级是基频周期的 0.2%~2%（微秒级），在低采样率或
    低信噪比下，测量误差会超过被测量本身；且它对麦克风型号的敏感度
    高于对说话人状态的敏感度。因此这里【不做静默降级】，而是
    显式返回 reliable=False + 具体原因，由上层决定是否采用。

    这样设计是为了避免「界面上出现一个看起来精确、实际由麦克风
    决定的数字」——那比没有这个数字更糟。
    """
    reasons = []
    if not is_sustained_vowel:
        reasons.append("非持续元音段：连续语音上周期扰动测量不稳定")
    if sr < PRAAT_SR_MIN:
        reasons.append(f"采样率不足（{sr}Hz < {PRAAT_SR_MIN}Hz）")

    # 【注意】这里【不】用 VAD-SNR 作为门限。
    # 持续元音段内没有静音帧，VAD-SNR 结构上无法测量（详见
    # estimate_snr_db 文档）。信噪质量改由下方实测的 HNR 判定，
    # HNR 测的是浊音内部周期/非周期成分之比，才是此处需要的量。
    out = {"jitter_local": None, "jitter_rap": None,
           "shimmer_local": None, "shimmer_apq3": None,
           "hnr_db": None,
           "reliable": False,
           "unreliable_reasons": reasons}

    try:
        import parselmouth
        from parselmouth.praat import call

        snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr)
        pp = call(snd, "To PointProcess (periodic, cc)", 60, 450)

        out["jitter_local"]  = _safe(call(pp, "Get jitter (local)",
                                          0, 0, 1e-4, 0.02, 1.3), 5)
        out["jitter_rap"]    = _safe(call(pp, "Get jitter (rap)",
                                          0, 0, 1e-4, 0.02, 1.3), 5)
        out["shimmer_local"] = _safe(call([snd, pp], "Get shimmer (local)",
                                          0, 0, 1e-4, 0.02, 1.3, 1.6), 5)
        out["shimmer_apq3"]  = _safe(call([snd, pp], "Get shimmer (apq3)",
                                          0, 0, 1e-4, 0.02, 1.3, 1.6), 5)

        harm = call(snd, "To Harmonicity (cc)", 0.01, 60, 0.1, 1.0)
        out["hnr_db"] = _safe(call(harm, "Get mean", 0, 0), 2)

        # 用实测 HNR 做信噪质量门限（替代结构上不适用的 VAD-SNR）
        hnr = out["hnr_db"]
        if hnr is None:
            reasons.append("HNR 测量失败，无法确认信噪质量")
        elif hnr < MIN_HNR_DB:
            reasons.append(f"谐噪比不足（HNR {hnr}dB < {MIN_HNR_DB}dB）："
                           "噪声占比过高，周期扰动测量会被噪声主导")

        out["unreliable_reasons"] = reasons
        out["reliable"] = (len(reasons) == 0)
    except Exception as e:
        out["error"] = str(e)[:100]
        out["reliable"] = False
        out["unreliable_reasons"] = reasons + [f"测量失败: {str(e)[:60]}"]

    # VAD-SNR 仅作参考记录，不参与可靠性判定（见上方说明）
    out["vad_snr_db_ref"] = snr_db
    return out


# ---------------------------------------------------------------- 顶层入口

def extract_all(y, sr, is_sustained_vowel=False, text_char_count=None):
    """
    提取全部轨 A 特征。

    参数
      y                   : float32/64 单声道波形，范围 [-1,1]
      sr                  : 采样率（建议 48000，见模块头注）
      is_sustained_vowel  : 该段是否为持续元音（决定 jitter/shimmer 是否可信）
      text_char_count     : 固定朗读文本的字数，用于计算语速（字/分）
                            None 时 speech_rate_cpm 返回 None，不做估算

    返回：dict，含 rhythm / pitch / energy / spectral / voice_quality
          五组 + meta。任何子项失败都不会让整体失败（各组独立 try），
          失败的组以 {"error": ...} 呈现，不影响其余组。
    """
    y = np.asarray(y, dtype=np.float64).ravel()
    out = {"meta": {"sr": sr, "n_samples": int(y.size),
                    "duration_sec": _safe(y.size / sr if sr else None),
                    "is_sustained_vowel": bool(is_sustained_vowel)}}

    if sr is None or sr <= 0:
        out["meta"]["usable"] = False
        out["meta"]["reason"] = "采样率非法"
        return out

    if y.size < sr * 0.5:                     # 不足 0.5s，直接判不可用
        out["meta"]["usable"] = False
        out["meta"]["reason"] = "音频过短（< 0.5s）"
        return out

    # ---- 电平检查【必须在峰值归一化之前】------------------------------
    # 归一化会把 -86dBFS 的底噪放大到满幅，导致 VAD 把纯噪声判成
    # 连续语音、并输出一整套看似正常的特征。麦克风被静音 / 权限被拒
    # 是真实高发场景，必须在此拦截而不是产出「好看的假数据」。
    y = y - float(np.mean(y))                 # 去直流
    peak = float(np.max(np.abs(y)))
    rms = float(np.sqrt(np.mean(y ** 2)))
    peak_dbfs = 20.0 * math.log10(peak) if peak > 1e-12 else -999.0
    rms_dbfs = 20.0 * math.log10(rms) if rms > 1e-12 else -999.0
    out["meta"]["peak_dbfs"] = _safe(peak_dbfs, 1)
    out["meta"]["rms_dbfs"] = _safe(rms_dbfs, 1)

    if peak_dbfs < MIN_PEAK_DBFS or rms_dbfs < MIN_RMS_DBFS:
        out["meta"]["usable"] = False
        # 文案里【不能出现 -999】：那是「数字静音」的内部哨兵值（peak<=1e-12），
        # 不是真实测量到的分贝数。而麦克风被静音/权限被拒恰恰是最高发的
        # 触发场景，也就是说这个哨兵值会高频出现在用户眼前 ——
        # 用户看到「峰值 -999.0dBFS」只会困惑，无法据此排查。
        # 数值本身仍保留在 meta.peak_dbfs / rms_dbfs 供诊断与测试使用。
        def _lvl(db):
            return "几乎无信号" if db <= -900.0 else f"{db:.1f}dBFS"
        out["meta"]["reason"] = (
            f"输入电平过低（峰值 {_lvl(peak_dbfs)} / "
            f"有效值 {_lvl(rms_dbfs)}）："
            "疑似麦克风未拾音、被静音或权限被拒，未采集到有效语音")
        return out

    out["meta"]["usable"] = True

    # 峰值归一化：抵消不同设备的增益差异（此后 y 的绝对电平无意义，
    # 真实电平已记录在 meta.peak_dbfs / meta.rms_dbfs）
    if peak > 1e-9:
        yn = y / peak
    else:
        yn = y

    # ---- 各组独立计算，互不牵连 --------------------------------------
    snr, snr_note = None, None
    try:
        flags, frame_sec = voice_activity(yn, sr)
        snr, snr_note = estimate_snr_db(yn, flags, frame_sec, sr)
        out["rhythm"] = rhythm_features(flags, frame_sec)
        out["rhythm"]["snr_db"] = snr
        if snr_note:
            out["rhythm"]["snr_note"] = snr_note
        # ---- 语速 ------------------------------------------------------
        # 用户决策（2026-08-12）：朗读【不锁定 55s】，按实际用时计算。
        # 读完用 1 分 23 秒就按 83s 算，语速即真值而非估计值。
        #
        # 分母用 span_sec（首字起→末字止，含内部停顿），不用 speech_sec：
        #   speech_sec 剔除了所有停顿，会把「读得慢但停顿多」的人算成语速正常，
        #   而停顿多本身就是要观察的信号，不能在分母里被消掉。
        # 分子的正确性依赖「确实读完了全文」，该校验在服务层用 ASR 覆盖率完成
        # （见 audio_service._speech_rate），此处只提供按全文字数的原始口径，
        # 并附上分母，供上层重算。
        sp = out["rhythm"].get("span_sec") or 0
        if text_char_count and sp > 0:
            out["rhythm"]["speech_rate_cpm"] = _safe(
                text_char_count / (sp / 60.0), 1)
            out["rhythm"]["speech_rate_basis"] = {
                "char_count": int(text_char_count),
                "span_sec": _safe(sp),
                "denominator": "span_sec",
                "note": "分子为文本全文字数，未经 ASR 覆盖率校验；"
                        "若实际未读完，此值会系统性偏高",
            }
        else:
            out["rhythm"]["speech_rate_cpm"] = None
            out["rhythm"]["speech_rate_basis"] = None
    except Exception as e:
        out["rhythm"] = {"error": str(e)[:120], "reliable": False}

    for key, fn in (("pitch",    lambda: pitch_features(yn, sr)),
                    ("energy",   lambda: energy_features(yn, sr)),
                    ("spectral", lambda: spectral_features(yn, sr))):
        try:
            out[key] = fn()
        except Exception as e:
            out[key] = {"error": str(e)[:120]}

    try:
        out["voice_quality"] = voice_quality_features(
            yn, sr, snr_db=snr, is_sustained_vowel=is_sustained_vowel)
    except Exception as e:
        out["voice_quality"] = {"error": str(e)[:120], "reliable": False}

    return out
