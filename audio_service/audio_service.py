# -*- coding: utf-8 -*-
"""
audio_service —— 音频分析微服务（127.0.0.1:5003）
================================================================
在整体架构中的位置
------------------
心理综合评估有四路原始输入层，本服务是第 4 路（此前缺失的一路）：
    1) rPPG 模型          -> 生理（心率/呼吸）
    2) OpenFace 3.0       -> 面部（AU / 情绪 / 注意力）  127.0.0.1:5002
    3) 量表评估文件        -> 用户自评
    4) 音频分析模型  ★本服务★                            127.0.0.1:5003

为什么独立成微服务（与 openface_service 同构）
------------------------------------------------
音频依赖会把 numpy 顶到 2.x，而生产环境 rrpg_plus 是
numpy 1.26.4 + scipy 1.9.3 + torch 2.12.0（且 scipy 1.9.3 本就
要求 numpy<1.26.0，已处于 ABI 边缘）。装在一起会强制升级 numpy 并
静默破坏已通过 15 轮验证的 rPPG 推理链。用 HTTP 隔离后，
两边各用自己的环境，互不影响。

接口
----
  GET  /health           服务与模型状态（含 token id，便于发现模型漂移）
  POST /analyze          上传 WAV，返回轨A手工特征 + 轨B深度模型结果
  POST /analyze_features 只跑轨 A（纯 CPU，不占 GPU）

设计原则（与前端/上层的契约）
------------------------------
1) 任何不可靠的测量都【显式标记】reliable=False + 原因，
   绝不静默降级成一个「看起来精确」的数字。
2) 麦克风未拾音 / 电平过低 / 音频过短，一律返回 usable=False，
   不产出貌似正常的特征。
3) 本服务只输出【可观测的测量量】，不做心理状态推断。
   五维画像等状态量的合成在上层（test2.py / 前端）完成。
"""
from __future__ import annotations

import io
import os
import sys
import time
import traceback

import numpy as np
from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import features_handcrafted as FH          # noqa: E402
from models_deep import RUNNER             # noqa: E402

app = Flask(__name__)

PORT = int(os.environ.get("AUDIO_SERVICE_PORT", "5003"))
# 上传体积上限：60s * 48kHz * 2byte(int16) * 1ch ≈ 5.8MB，留足余量
MAX_UPLOAD_BYTES = int(os.environ.get("AUDIO_MAX_BYTES", str(32 * 1024 * 1024)))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
# ⚠️ werkzeug 对【表单】体另有一个默认 500KB 的上限（max_form_memory_size），
# 与 MAX_CONTENT_LENGTH 是两回事。若客户端不设 Content-Type，
# body 会被当成 x-www-form-urlencoded 表单，几百KB 的 WAV 就会撞上
# 这个上限、抛 RequestEntityTooLarge（413），而非走正常音频解析路径。
# 这里放宽该上限，使「客户端漏设 Content-Type」不至于直接失败。
# 正确用法仍是显式设 Content-Type: application/octet-stream 或 multipart。
app.config["MAX_FORM_MEMORY_SIZE"] = MAX_UPLOAD_BYTES

# 时长保护：过长音频会长时间占住 GPU 锁
MAX_DURATION_SEC = float(os.environ.get("AUDIO_MAX_DURATION", "180"))

_STATS = {"requests": 0, "errors": 0, "last_error": None,
          "last_ms": None, "started_at": time.time()}


# ---------------------------------------------------------------- 读音频

def read_wav(raw: bytes):
    """
    读 WAV -> (float32 单声道 [-1,1], sr)。

    刻意只依赖 soundfile（libsndfile），不依赖 ffmpeg：
      - 前端直接上传 WAV(PCM)，无需转码；
      - 避免 Opus/MP3 这类有损压缩 —— 它们会污染 jitter/shimmer
        这种微秒级周期扰动测量。
    多声道自动混为单声道（取均值）。
    """
    import soundfile as sf
    y, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    n_ch = y.shape[1]
    y = y.mean(axis=1) if n_ch > 1 else y[:, 0]
    return np.asarray(y, dtype=np.float64), int(sr), n_ch


def _get_raw():
    """
    取上传的音频字节。兼容 multipart 表单字段 file 与 raw body。

    ⚠️ 坑（已实测踩到）：不能无条件先访问 request.files —— 该属性会
    触发 Flask 解析请求体为表单。若客户端没显式设 Content-Type
    （curl --data-binary 默认发 application/x-www-form-urlencoded），
    body 会被表单解析器吃掉，之后 get_data() 返回空，
    表现为「明明传了 WAV 却报空请求体」。
    因此这里【先按 Content-Type 判断】，只有确认是 multipart 时
    才走 request.files。
    """
    ctype = (request.content_type or "").lower()
    if ctype.startswith("multipart/form-data"):
        if "file" in request.files:
            return request.files["file"].read()
        for fs in request.files.values():        # 字段名不叫 file 也接受
            return fs.read()
        return b""
    # 非 multipart：直接读原始体，并禁止表单解析吃掉 body
    raw = request.get_data(cache=False, parse_form_data=False)
    if raw:
        return raw
    return request.stream.read() or b""


def _bool_arg(name, default=False):
    v = request.args.get(name)
    if v is None:
        v = (request.form.get(name) if request.form else None)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _int_arg(name, default=None):
    v = request.args.get(name) or (request.form.get(name) if request.form else None)
    try:
        return int(v) if v is not None and str(v).strip() != "" else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 路由

@app.route("/health", methods=["GET"])
def health():
    info = RUNNER.info()
    return jsonify({
        "status": "ok" if info.get("ready") else "degraded",
        "service": "audio_service",
        "port": PORT,
        "deep_model": info,
        "track_a": {
            "target_sr": FH.TARGET_SR,
            "praat_sr_min": FH.PRAAT_SR_MIN,
            "min_peak_dbfs": FH.MIN_PEAK_DBFS,
            "min_hnr_db": FH.MIN_HNR_DB,
        },
        "limits": {"max_upload_bytes": MAX_UPLOAD_BYTES,
                   "max_duration_sec": MAX_DURATION_SEC},
        "stats": dict(_STATS,
                      uptime_sec=round(time.time() - _STATS["started_at"], 1)),
    })


@app.route("/analyze_features", methods=["POST"])
def analyze_features():
    """只跑轨 A（纯 CPU）。适合高频调用或 GPU 忙时。"""
    return _handle(run_deep=False)


@app.route("/analyze", methods=["POST"])
def analyze():
    """轨 A + 轨 B。"""
    return _handle(run_deep=True)


def _handle(run_deep=True):
    t0 = time.time()
    _STATS["requests"] += 1
    try:
        raw = _get_raw()
        if not raw:
            return jsonify({"status": "error",
                            "message": "空请求体：需要 WAV 音频"}), 400

        try:
            y, sr, n_ch = read_wav(raw)
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"WAV 解析失败：{str(e)[:120]}",
                "hint": "请上传未压缩的 WAV(PCM)。本服务不装 ffmpeg，"
                        "不接受 mp3/opus/webm —— 有损压缩会污染 "
                        "jitter/shimmer 测量。",
            }), 400

        dur = y.size / sr if sr else 0
        if dur > MAX_DURATION_SEC:
            return jsonify({"status": "error",
                            "message": f"音频过长（{dur:.1f}s > "
                                       f"{MAX_DURATION_SEC}s）"}), 413

        # 参数：是否为持续元音段、朗读文本字数
        is_vowel = _bool_arg("sustained_vowel", False)
        char_count = _int_arg("text_char_count", None)

        resp = {
            "status": "ok",
            "input": {"sr": sr, "channels": n_ch,
                      "duration_sec": round(dur, 3),
                      "bytes": len(raw)},
        }

        # ---- 轨 A ----
        feats = FH.extract_all(y, sr,
                               is_sustained_vowel=is_vowel,
                               text_char_count=char_count)
        resp["features"] = feats
        usable = bool(feats.get("meta", {}).get("usable"))

        # ---- 轨 B ----
        # 电平过低/过短时不跑深度模型：模型不会拒绝垃圾输入，
        # 它会对静音也给出一个 label，那正是我们要避免的假数据。
        if not run_deep:
            resp["deep"] = {"skipped": "run_deep=False"}
        elif not usable:
            resp["deep"] = {
                "skipped": "轨A 判定音频不可用，跳过深度模型",
                "reason": feats.get("meta", {}).get("reason")}
        else:
            resp["deep"] = RUNNER.analyze(y, sr, want_text=True)

        # ---- 供上层直接使用的摘要（不含任何心理推断）----
        resp["summary"] = _summary(feats, resp.get("deep"),
                                   expected_chars=char_count)

        _STATS["last_ms"] = int((time.time() - t0) * 1000)
        resp["elapsed_ms"] = _STATS["last_ms"]
        return jsonify(resp)

    except Exception as e:
        _STATS["errors"] += 1
        _STATS["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        traceback.print_exc()
        return jsonify({"status": "error", "message": _STATS["last_error"]}), 500


def _han_count(text):
    """只数汉字。

    语速的单位是「字/分」，标点不发音，把标点算进字数会让语速虚高。
    《北风与太阳》139 个汉字 / 158 个字符，用 len() 会高估 13.7%。
    CJK 基本区 + 扩展A，已覆盖简繁体常用字。
    """
    if not text:
        return 0
    return sum(1 for ch in text
               if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf")


# 朗读完成度阈值。低于此值即认为「没读完」，不再用全文字数当分子。
# 取 0.90 而非 1.0：ASR 对末尾轻声字、语气词的漏识很常见，
# 卡太严会把正常读完的用户误判成未读完。
READING_COVERAGE_MIN = float(os.environ.get("AUDIO_READ_COVERAGE_MIN", "0.90"))


def _speech_rate(feats, deep, expected_chars=None):
    """
    朗读语速的最终裁定 —— 【不锁定固定时长，按实际用时计算】。

    用户决策（2026-08-12）：
      「是否可以不锁定55秒，比如说我读完用时1分23秒，就按照实际时长来计算？」
      → 是。锁定 55s 反而会产生错数，理由见下。

    为什么锁 55s 是错的
    -------------------
    原实现的分子恒为【全文字数】。若 55s 到点只读了 2/3，分子仍按全文算，
    语速被系统性高估约 50%。它不会报错，只会给出一个偏快的「正常值」——
    这是最危险的失败模式（假数据比缺数据更有害）。
    更关键的是方向性错误：读得慢的人正是需要被观察的那一类（迟滞、费力），
    锁 55s 会把慢读者一律截断成「没读完」，再高估回正常区间，
    测量偏差与目标信号方向恰好相反。

    按实际用时后，风险从「时间被截断」转移为「文本没读完」，
    因此必须有本函数的覆盖率校验，否则分子依旧会虚高。

    三种裁定结果
    ------------
      complete   : ASR 覆盖率 >= 阈值 → 视为读完，分子用【全文字数】
                   （全文字数比 ASR 字数准，ASR 有识别误差）
      incomplete : 覆盖率不足 → 分子降级为【ASR 实际识别字数】，
                   并置 reliable=False + 写明原因，绝不静默用全文字数
      unknown    : 拿不到 ASR（深度模型被跳过/失败）→ 返回 None，
                   宁缺勿猜（此时无法判断是否读完，任何取值都是猜测）
    """
    rh = (feats or {}).get("rhythm", {}) or {}
    span = rh.get("span_sec") or 0
    if not expected_chars or span <= 0:
        return None

    asr = ((deep or {}).get("asr") or {})
    asr_text = asr.get("text")
    # ASR 侧此前用 len(clean) 计数（含标点），此处统一按汉字重算，
    # 保证与 expected_chars 同口径 —— 口径不一致会让覆盖率失真。
    asr_chars = _han_count(asr_text) if asr_text else None

    base = {
        "expected_chars": int(expected_chars),
        "span_sec": round(float(span), 3),
        "denominator": "span_sec",
        "denominator_note": "首字起→末字止，含内部停顿；"
                            "提前/延迟点「完成」不影响结果",
        "policy": "按实际用时计算，不锁定固定时长",
    }

    if asr_chars is None:
        base.update({
            "cpm": None, "verdict": "unknown", "reliable": False,
            "reason": "无 ASR 文本，无法判断是否读完全文。"
                      "按实际时长计速的前提是确实读完，"
                      "故不输出估计值（宁缺勿猜）",
        })
        return base

    coverage = asr_chars / float(expected_chars)
    base["asr_chars"] = asr_chars
    base["coverage"] = round(coverage, 3)

    # ASR 一个字都没识别出来：这不是「语速为 0」，而是【没测到语音】。
    # 若按公式算会输出 cpm=0.0，下游极易把 0 当成一个真实测量值
    # （比如画进趋势图、参与均值），比返回 None 危险得多。
    # 常见成因：麦克风拾到的是噪声/音乐、用户全程没出声、语言不匹配。
    if asr_chars == 0:
        base.update({
            "cpm": None, "verdict": "no_speech", "reliable": False,
            "reason": "ASR 未识别出任何汉字：未采集到可识别的朗读语音"
                      "（可能是噪声、未出声或非目标语言）。"
                      "此为「没测到」而非「语速为 0」，故不输出数值",
        })
        return base

    if coverage >= READING_COVERAGE_MIN:
        base.update({
            "cpm": round(expected_chars / (span / 60.0), 1),
            "numerator": "expected_chars",
            "verdict": "complete",
            "reliable": True,
            "reason": f"ASR 覆盖率 {coverage:.0%} >= "
                      f"{READING_COVERAGE_MIN:.0%}，视为读完全文；"
                      "分子取全文字数（比 ASR 字数更准）",
        })
    else:
        base.update({
            "cpm": round(asr_chars / (span / 60.0), 1),
            "numerator": "asr_chars",
            "verdict": "incomplete",
            "reliable": False,
            "reason": f"ASR 覆盖率仅 {coverage:.0%} < "
                      f"{READING_COVERAGE_MIN:.0%}，判定未读完全文。"
                      "分子已降级为实际识别字数，而非全文字数"
                      "（用全文字数会让语速虚高）；"
                      "但 ASR 字数本身含识别误差，故标记不可信",
        })
    return base


def _summary(feats, deep, expected_chars=None):
    """
    把散落的字段收敛成上层最常用的几项，并【明确标注每项是否可信】。
    这里只做「搬运 + 可信度标注」，不做任何加权或状态推断 ——
    五维公式属于上层职责，放在这里会让边界糊掉。
    """
    meta = feats.get("meta", {}) or {}
    rh = feats.get("rhythm", {}) or {}
    pi = feats.get("pitch", {}) or {}
    en = feats.get("energy", {}) or {}
    vq = feats.get("voice_quality", {}) or {}
    emo = (deep or {}).get("emotion") or {}
    sr_info = _speech_rate(feats, deep, expected_chars)

    return {
        "usable": bool(meta.get("usable")),
        "unusable_reason": meta.get("reason"),
        "peak_dbfs": meta.get("peak_dbfs"),
        # 节奏
        "speech_sec": rh.get("speech_sec"),
        "speech_ratio": rh.get("speech_ratio"),
        "pause_count": rh.get("pause_count"),
        "pause_ratio": rh.get("pause_ratio"),
        # 语速：以 _speech_rate 的裁定为准（含读完与否的校验）。
        # 轨A 里的 rh.speech_rate_cpm 是未校验的原始口径，
        # 这里【不再直接搬运】它，避免上层误用一个可能虚高的值。
        "speech_rate_cpm": (sr_info or {}).get("cpm"),
        "speech_rate": sr_info,
        "span_sec": rh.get("span_sec"),
        "rhythm_reliable": rh.get("reliable"),
        # 音高（跨用户可比性依赖「固定朗读文本」，见 features 模块头注）
        "f0_mean": pi.get("f0_mean"),
        "f0_semitone_std": pi.get("f0_semitone_std"),
        # 能量
        "rms_variation": en.get("rms_variation"),
        "loudness_db_mean": en.get("loudness_db_mean"),
        # 嗓音质量（默认不可信，除非持续元音 + 高 HNR + 高采样率）
        "jitter_local": vq.get("jitter_local"),
        "shimmer_local": vq.get("shimmer_local"),
        "hnr_db": vq.get("hnr_db"),
        "voice_quality_reliable": vq.get("reliable"),
        "voice_quality_reasons": vq.get("unreliable_reasons"),
        # 深度模型情绪
        "emotion_label": emo.get("label"),
        "emotion_confidence": emo.get("confidence"),
        "emotion_distribution": emo.get("distribution"),
        "emotion_reliable": emo.get("reliable"),
        "emotion_reasons": emo.get("unreliable_reasons"),
    }


# ---------------------------------------------------------------- 启动

def _preload():
    print("[startup] 预加载 SenseVoiceSmall ...", flush=True)
    t = time.time()
    ok = RUNNER.load()
    if ok:
        print(f"[startup] 模型就绪 ({time.time()-t:.1f}s) "
              f"device={RUNNER.device}", flush=True)
    else:
        # 不退出：轨 A 仍可独立服务，/health 会显示 degraded
        print(f"[startup] 模型加载失败：{RUNNER.load_error}", flush=True)
        print("[startup] 服务继续启动，/analyze_features（轨A）仍可用",
              flush=True)


if __name__ == "__main__":
    _preload()

    # ⚠️ 用 waitress + threads=1，【不要】用 app.run()。
    # 这条经验来自 openface_service 的性能事故复盘（2026-08-09）：
    # Flask/werkzeug 开发服务器给每个请求分配新线程，而 PyTorch/cuDNN
    # 的算法自动调优缓存是【线程本地】的 —— 换线程就要重新 benchmark，
    # 实测每请求多付 2.4~4.8s，与算力无关。
    # threads=1 让整个进程用同一 worker 线程，cuDNN 只预热一次；
    # 同时天然避免「同一份 GPU 模型被多线程并发 forward 踩状态」的
    # 正确性风险（models_deep 内另有锁，双重保险）。
    try:
        from waitress import serve
        print(f"[startup] waitress threads=1 port={PORT}", flush=True)
        serve(app, host="127.0.0.1", port=PORT, threads=1,
              channel_timeout=300)
    except ImportError:
        print("[startup] waitress 未安装，退回 app.run（性能会明显下降）",
              flush=True)
        app.run(host="127.0.0.1", port=PORT, threaded=False)
