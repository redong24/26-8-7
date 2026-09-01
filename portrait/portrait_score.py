# -*- coding: utf-8 -*-
"""
五维心理画像计分层（批次 3b）
================================================================
2026-08-13 新增。本模块是 ask#35 那份《五维心理画像设计方案》经数值
验证、修正、并由使用方拍板两项未决问题后的【可执行版本】。

它与 portrait_state.py 的分工：
  portrait_state.py  管三份数据的固化与完成度闸门（方案 B）
  portrait_score.py  管「数据齐备之后算什么、怎么算」

------------------------------------------------------------------
为什么不把公式直接写在 portrait_state.py 里
------------------------------------------------------------------
完成度判定是【事实】（数据在不在），计分是【假设】（怎么加权）。
后者会随临床反馈反复改，前者不该跟着一起动。分开放，改公式时
不用碰闸门代码，闸门的部署校验也不会因为公式改动而失效。

------------------------------------------------------------------
已落地的必改项（对应 FORMULA_FIXES，逐条注明落点）
------------------------------------------------------------------
  loudness_gain_invariant : 本模块【完全不使用】loudness_db_mean，
                            改用 rms_variation（变异系数，增益无关）
  speech_rate_range       : SPEECH_RATE_RANGE = (95, 160) 字/分
  au_cap                  : AU_CAP = 0.3（AU25 例外 0.6）+ sqrt 映射
  hr_null_and_clip        : 心率 None -> 相关维度整项 None，绝不填 0
  use_level_norm          : 压力维度取 level_norm，不取 pct
  drop_emo_switches       : 情绪稳定只用 emo_stability

------------------------------------------------------------------
本模块新增的第 7 项必改（实现时才发现，已回写 FORMULA_FIXES）
------------------------------------------------------------------
  au_fluctuation_no_source :
      原方案「情绪稳定 = f(emo_stability, AU 强度波动, 主导情绪时长)」
      里的「AU 强度波动」没有数据源。flask_openface_patch.py L78 取的是
      parsed（单帧瞬时值）而非 metrics（60s 聚合），快照里的 au_intensity
      是【一帧】的强度，无法算窗口内波动。要么让 openface_service 额外
      输出 au_std（跨目录，不在本次范围），要么去掉该项。本版去掉，
      权重并入 emo_stability，并在 terms 里显式记录 dropped 原因 ——
      不留一个看起来算了、实际上没算的空位。

------------------------------------------------------------------
两项未决问题的拍板结果（ask#39「全部确认」）
------------------------------------------------------------------
  vitality_signal          -> 采纳「标注为探索性指标，不计入综合分」
      活力值仍然计算并返回，但带 exploratory=True 与
      exclude_from_composite=True。理由：不算等于把已采到的信号
      丢掉；计入综合分则会把一个已知系统性偏低的量掺进结论。
      折中是「算、给看、但不进结论」，并在字段里写明为什么。
  voice_quality_availability -> 采纳「接受不可用，压力维度按三项重配」
      压力值 = DASS-21 压力分量表 level_norm + 心率 + AU04。
      jitter/shimmer 一律不进任何维度，即使某次采集恰好 reliable
      —— 时而进时而不进会让同一个人的分数不可纵向比较。

------------------------------------------------------------------
缺项政策（与 readiness 一致，不重复实现）
------------------------------------------------------------------
缺项时该维度返回 value=None + missing 列表，【不做权重重分配】。
重分配会让同一个人在不同完成度下得到不同分数，纵向比较失去意义。
综合分同理：任一计入维度不可用则综合分为 None。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 归一化常数
# 每个常数都必须能说出「为什么是这个数」，说不出的不许写在这里。

AU_CAP_DEFAULT = 0.3          # 见 FORMULA_FIXES.au_cap
AU_CAP_SPECIAL = {"AU25": 0.6}

# 中文朗读正常速度。见 FORMULA_FIXES.speech_rate_range
SPEECH_RATE_RANGE = (95.0, 160.0)

# 静息心率：60 视为放松端，100 视为紧张端（成人静息 60~100 bpm）
HR_RELAX_RANGE = (60.0, 100.0)
# 静息呼吸：12 视为放松端，24 视为紧张端（成人静息 12~20 /min）
RESP_RELAX_RANGE = (12.0, 24.0)

# F0 半音标准差：单调 1.5 st -> 抑扬 6.0 st（固定朗读文本下的经验区间，
# 标注为探索性的原因之一就是这个区间尚无本地样本支撑）
F0_STD_RANGE = (1.5, 6.0)
# RMS 变异系数：0.2 平淡 -> 0.8 起伏
RMS_VAR_RANGE = (0.2, 0.8)

POSITIVE_EMOTIONS = ("happy", "happiness", "surprise", "高兴", "快乐", "惊讶")

# DASS-21 压力分量表在 score_dass21() 输出里的键。
# 【务必注意】是 "S" 而不是 "stress" —— portrait_state.DASS_GROUPS 用的是
# 单字母键 D/A/S。第一版这里写了 "stress"，结果压力维度恒为 None，
# 连带综合分恒为 None，而【没有任何报错】：取不到就是缺项，缺项就是
# 合法状态。是集成测试断言「三项齐备时综合分应有值」才暴露出来的。
# 部署脚本已加闸门校验此键与 DASS_GROUPS 一致，见 deploy_psy_v3.sh。
DASS_STRESS_KEY = "S"

# 维度权重。写成常量而不是散在函数里，是为了能被 /portrait/formula_spec
# 原样吐给前端 —— 提示词里写的公式必须和实际算的一致。
WEIGHTS = {
    "emotion_stability": {"emo_stability": 0.70, "dominant_ratio": 0.30},
    "relaxation":        {"hr": 0.45, "resp": 0.25, "au04_inv": 0.30},
    "focus":             {"gaze_stability": 0.60, "pose_steady": 0.40},
    "stress":            {"dass_stress": 0.50, "hr": 0.30, "au04": 0.20},
    "vitality":          {"f0_std": 0.40, "positive_emo": 0.35, "rms_var": 0.25},
}

# 综合分只纳入这四项，活力值按拍板结果排除
COMPOSITE_DIMENSIONS = ("emotion_stability", "relaxation", "focus", "stress")
# 压力值是「越低越好」，进综合分时取反
COMPOSITE_INVERTED = ("stress",)


# ---------------------------------------------------------------- 基础工具
def _num(v):
    """取数值。None/空串/非数一律 None，不做默认值兜底。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / Inf
        return None
    return f


def _lin(v, lo, hi):
    """线性归一化到 [0,1]，超界裁剪。v 为 None 时返回 None。"""
    v = _num(v)
    if v is None:
        return None
    if hi == lo:
        return None
    t = (v - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def _inv(t):
    return None if t is None else 1.0 - t


def au_norm(au_intensity, key):
    """
    AU 强度归一化。sqrt 映射的理由见 FORMULA_FIXES.au_cap：
    AU 日常波动多在 0~0.1，线性映射下看不出人和人的差别。
    """
    if not isinstance(au_intensity, dict):
        return None
    v = _num(au_intensity.get(key))
    if v is None:
        return None
    cap = AU_CAP_SPECIAL.get(key, AU_CAP_DEFAULT)
    if v <= 0:
        return 0.0
    return min(1.0, (v / cap) ** 0.5)


def _wsum(terms, weights):
    """
    加权求和。terms 里任一必需项为 None 则整体返回 None ——
    这是缺项政策的落点：不重分配权重。
    """
    total = 0.0
    for k, w in weights.items():
        t = terms.get(k)
        if t is None:
            return None
        total += t * w
    return total


def _pct(x):
    return None if x is None else round(x * 100.0, 1)


# ---------------------------------------------------------------- 维度：情绪稳定
def dim_emotion_stability(face):
    """
    情绪稳定 = 0.70 * emo_stability + 0.30 * 主导情绪持续占比

    emo_stability 来自 metrics_aggregator L263: exp(-emo_switches / 5.0)，
    已经是 0~1 且已含「切换次数」信息 —— 这正是 drop_emo_switches 的依据：
    再单独纳入 emo_switches 等于对同一信号加权两次。

    主导情绪持续占比 = emo_dominant_duration_sec / window_sec，
    比绝对秒数可比：20s 窗口里持续 15s 与 60s 窗口里持续 15s 不是一回事。
    """
    missing, terms = [], {}
    face = face or {}

    st = _num(face.get("emo_stability"))
    if st is None:
        missing.append("emo_stability（情绪稳定度未聚合，需更长采集窗口）")
    terms["emo_stability"] = None if st is None else max(0.0, min(1.0, st))

    dur = _num(face.get("emo_dominant_duration_sec"))
    win = _num(face.get("window_sec"))
    if dur is None or win is None or win <= 0:
        missing.append("emo_dominant_duration_sec / window_sec")
        terms["dominant_ratio"] = None
    else:
        terms["dominant_ratio"] = max(0.0, min(1.0, dur / win))

    return {
        "id": "emotion_stability", "label": "情绪稳定",
        "value": _pct(_wsum(terms, WEIGHTS["emotion_stability"])),
        "terms": terms, "weights": WEIGHTS["emotion_stability"],
        "missing": missing,
        "dropped": [{"term": "AU 强度波动",
                     "why": "快照中 au_intensity 是单帧瞬时值（"
                            "flask_openface_patch.py L78 取 parsed 而非 metrics），"
                            "无法计算窗口内波动。权重已并入 emo_stability。"}],
        "sources": ["face"],
    }


# ---------------------------------------------------------------- 维度：放松度
def dim_relaxation(face, hr):
    """
    放松度 = 0.45 * (心率越低越松) + 0.25 * (呼吸越慢越松) + 0.30 * (AU04 越低越松)

    心率缺测 -> 整维 None（FORMULA_FIXES.hr_null_and_clip）。
    这条必须硬：把「未测到」当 0 会算出「深度放松」，
    是最危险的假阳性方向。
    """
    missing, terms = [], {}
    face, hr = face or {}, hr or {}

    h = _num(hr.get("heart_rate"))
    if h is None:
        missing.append("心率未测得（rPPG 未出值，请正对摄像头保持光照稳定）")
    terms["hr"] = _inv(_lin(h, *HR_RELAX_RANGE))

    r = _num(hr.get("respiration_rate"))
    if r is None:
        missing.append("呼吸率未测得")
    terms["resp"] = _inv(_lin(r, *RESP_RELAX_RANGE))

    a4 = au_norm(face.get("au_intensity"), "AU04")
    if a4 is None:
        missing.append("AU04（皱眉）强度缺失")
    terms["au04_inv"] = _inv(a4)

    return {
        "id": "relaxation", "label": "放松度",
        "value": _pct(_wsum(terms, WEIGHTS["relaxation"])),
        "terms": terms, "weights": WEIGHTS["relaxation"],
        "missing": missing, "dropped": [],
        "sources": ["face", "hr"],
    }


# ---------------------------------------------------------------- 维度：专注度
def dim_focus(face):
    """
    专注度 = 0.60 * gaze_stability + 0.40 * (1 - pose_deviation_60s)

    pose_deviation_60s 是窗口内「视线状态含『偏移』」的帧占比
    （metrics_aggregator L240），本身就是 0~1 的比率，取反即稳定度。

    注意：不使用 attention_score。它在 metrics_aggregator L274 里被
    blink_rate/perclos 污染，而那两项来自恒为 0.30 的 ear —— 恒 0。
    """
    missing, terms = [], {}
    face = face or {}

    g = _num(face.get("gaze_stability"))
    if g is None:
        missing.append("gaze_stability（注视稳定性未聚合）")
    terms["gaze_stability"] = None if g is None else max(0.0, min(1.0, g))

    d = _num(face.get("pose_deviation_60s"))
    if d is None:
        missing.append("pose_deviation_60s（头姿偏移占比缺失）")
    terms["pose_steady"] = None if d is None else max(0.0, min(1.0, 1.0 - d))

    return {
        "id": "focus", "label": "专注度",
        "value": _pct(_wsum(terms, WEIGHTS["focus"])),
        "terms": terms, "weights": WEIGHTS["focus"],
        "missing": missing,
        "dropped": [{"term": "attention_score",
                     "why": "该值被 blink_rate / perclos 污染，"
                            "而二者源自恒为 0.30 的 ear，恒等于 0。"}],
        "sources": ["face"],
    }


# ---------------------------------------------------------------- 维度：压力值
def dim_stress(face, hr, scale):
    """
    压力值 = 0.50 * DASS-21 压力分量表 level_norm + 0.30 * 心率 + 0.20 * AU04
    数值越高压力越大（与其余四维方向相反，前端已按「越低越好」标注）。

    这就是 voice_quality_availability 的拍板落点：jitter/shimmer 不参与，
    自评 level_norm 顶上主权重。level_norm 而非 pct 的理由见
    FORMULA_FIXES.use_level_norm —— 三个分量表临床切点不同，
    pct 会让严重度标尺不一致。
    """
    missing, terms = [], {}
    face, hr, scale = face or {}, hr or {}, scale or {}

    subs = (scale.get("scored") or {}).get("subscales") or {}
    sub = subs.get(DASS_STRESS_KEY) or {}
    ln = _num(sub.get("level_norm"))
    if ln is None:
        missing.append("DASS-21 压力分量表未完成")
    terms["dass_stress"] = ln

    h = _num(hr.get("heart_rate"))
    if h is None:
        missing.append("心率未测得")
    terms["hr"] = _lin(h, *HR_RELAX_RANGE)

    a4 = au_norm(face.get("au_intensity"), "AU04")
    if a4 is None:
        missing.append("AU04（皱眉）强度缺失")
    terms["au04"] = a4

    return {
        "id": "stress", "label": "压力值",
        "value": _pct(_wsum(terms, WEIGHTS["stress"])),
        "terms": terms, "weights": WEIGHTS["stress"],
        "missing": missing,
        "dropped": [{"term": "jitter / shimmer",
                     "why": "voice_quality 要求 HNR>=15dB 才 reliable，"
                            "常规环境达不到。已按拍板结果永久排除 —— "
                            "时而进时而不进会让分数不可纵向比较。"}],
        "higher_is_worse": True,
        "sources": ["face", "hr", "scale"],
    }


# ---------------------------------------------------------------- 维度：活力值
def dim_vitality(face, voice):
    """
    活力值 = 0.40 * F0 半音标准差 + 0.35 * 正向情绪占比 + 0.25 * RMS 变异系数

    【探索性指标，不计入综合分】—— ask#39 拍板结果。
    原因不是阈值没调好，是任务与构念不匹配：中性朗读任务本身不诱发
    唤醒，实测健康被试仅 31.8/100。保留计算是为了不丢已采到的信号，
    排除出综合分是为了不让已知系统性偏低的量污染结论。

    此处【不使用】loudness_db_mean（FORMULA_FIXES.loudness_gain_invariant：
    峰值归一化后它与音量完全无关，三档增益输出全为 -7.27 dB）。
    改用 rms_variation —— 变异系数，天然增益无关。
    """
    missing, terms = [], {}
    face, voice = face or {}, voice or {}
    pros = voice.get("prosody") or {}

    f0 = _num(pros.get("f0_semitone_std"))
    if f0 is None:
        missing.append("f0_semitone_std（需完成朗读段）")
    terms["f0_std"] = _lin(f0, *F0_STD_RANGE)

    dist = face.get("emo_distribution")
    if not isinstance(dist, dict) or not dist:
        missing.append("emo_distribution（情绪分布缺失）")
        terms["positive_emo"] = None
    else:
        tot = sum(v for v in (_num(x) or 0.0 for x in dist.values()) if v > 0)
        if tot <= 0:
            missing.append("emo_distribution 全为 0")
            terms["positive_emo"] = None
        else:
            pos = sum((_num(v) or 0.0) for k, v in dist.items()
                      if str(k).strip().lower() in
                      [p.lower() for p in POSITIVE_EMOTIONS])
            terms["positive_emo"] = max(0.0, min(1.0, pos / tot))

    rv = _num(pros.get("rms_variation"))
    if rv is None:
        missing.append("rms_variation（需完成朗读段）")
    terms["rms_var"] = _lin(rv, *RMS_VAR_RANGE)

    return {
        "id": "vitality", "label": "活力值",
        "value": _pct(_wsum(terms, WEIGHTS["vitality"])),
        "terms": terms, "weights": WEIGHTS["vitality"],
        "missing": missing,
        "dropped": [{"term": "loudness_db_mean",
                     "why": "features_handcrafted.py L528-531 先做 y/peak "
                            "峰值归一化再测 dB，该量与音量无关（三档增益"
                            "输出全为 -7.27dB）。已改用 rms_variation。"}],
        "exploratory": True,
        "exclude_from_composite": True,
        "exploratory_reason": "中性朗读任务不诱发唤醒，该维度先天缺信号"
                             "（健康被试实测 31.8/100）。已按拍板结果"
                             "标注为探索性并排除出综合分。",
        "sources": ["face", "voice"],
    }


# ---------------------------------------------------------------- 总入口
DIMENSION_ORDER = ("emotion_stability", "relaxation", "focus", "stress", "vitality")


def compute_portrait(snap, readiness_report=None):
    """
    从快照算五维。snap 为 portrait_state.snapshot() 的返回结构：
        {"face": {...}|None, "voice": {...}|None, "scale": {...}|None,
         "readiness": {...}}
    hr 从 readiness_report["hr"] 取（它与面部同时固化，见 put_face）。

    返回结构固定，即使一项都算不出来也返回完整骨架 ——
    前端不必区分「没数据」和「没这个字段」。

    ready=False 时【仍然计算】各维度并返回，但 composite 为 None，
    且 gated=True。理由：告诉用户「面部已达标、语音还缺」比
    一个空白面板有用得多；但综合结论必须等三项齐备，
    否则会出现「补完语音后综合分反而下降」这种无法解释的现象。
    """
    snap = snap or {}
    rd = readiness_report or snap.get("readiness") or {}
    face = snap.get("face")
    voice = snap.get("voice")
    scale = snap.get("scale")
    hr = rd.get("hr")

    dims = {
        "emotion_stability": dim_emotion_stability(face),
        "relaxation":        dim_relaxation(face, hr),
        "focus":             dim_focus(face),
        "stress":            dim_stress(face, hr, scale),
        "vitality":          dim_vitality(face, voice),
    }

    ready = bool(rd.get("ready"))

    # ---- 综合分 ----
    # 只有 ready 且四个计入维度全部有值时才出综合分。
    comp, comp_missing = None, []
    if not ready:
        blk = "、".join(rd.get("blocking") or []) or "未知"
        comp_missing.append("三项采集未齐备（缺: %s）" % blk)
    else:
        vals = []
        for k in COMPOSITE_DIMENSIONS:
            v = dims[k]["value"]
            if v is None:
                comp_missing.append("%s 不可用: %s"
                                    % (dims[k]["label"],
                                       "；".join(dims[k]["missing"]) or "未知原因"))
                continue
            vals.append(100.0 - v if k in COMPOSITE_INVERTED else v)
        if not comp_missing and vals:
            comp = round(sum(vals) / len(vals), 1)

    return {
        "dimensions": [dims[k] for k in DIMENSION_ORDER],
        "composite": {
            "value": comp,
            "included": list(COMPOSITE_DIMENSIONS),
            "excluded": [{"id": "vitality",
                          "why": "探索性指标，中性朗读任务下先天缺信号"}],
            "inverted": list(COMPOSITE_INVERTED),
            "missing": comp_missing,
            "note": "等权平均；压力值取反后计入。任一计入维度不可用则不出综合分"
                    "——不做权重重分配，否则同一个人在不同完成度下会得到"
                    "不同分数，纵向比较失去意义。",
        },
        "gated": not ready,
        "ready": ready,
        "policy": "缺项维度返回 value=None + missing 说明缺什么，不填默认值、"
                  "不重分配权重。ready=False 时仍返回各维度以便用户看到进度，"
                  "但不出综合分。",
        "spec_version": "3b-2026-08-13",
    }
