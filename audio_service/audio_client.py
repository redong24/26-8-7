# -*- coding: utf-8 -*-
"""
audio_client —— test2.py 侧的音频分析接入层
================================================================
放在独立模块而不是直接写进 test2.py 的原因
--------------------------------------------
test2.py 是 2477 行的生产文件，承载 rPPG 主链路（已通过 15 轮验证）。
音频功能与 rPPG 完全无耦合，没有理由把上百行新代码插进去增加
回归风险。这里把逻辑全部收在独立模块，test2.py 只需加极少量
挂载代码（见本文件末尾 INTEGRATION 说明）。

职责
----
1) AudioClient      : 调用 127.0.0.1:5003（照 OpenFaceClient 的模式）
2) AudioSessionState: 每会话的录音缓冲与最近一次分析结果
3) register_routes  : 把 3 个音频路由注册到既有 Flask app

设计约束（来自用户 2026-08-12 的决策，勿擅自更改）
--------------------------------------------------
- WAV 由【前端封装】后上传，服务端不装 ffmpeg
- 采样率固定 【48kHz】（jitter/shimmer 需 >= 44.1kHz）
- 任务设计：【5s 持续元音 /a/】 + 【固定文本朗读《北风与太阳》】
- 朗读段【不锁定时长】（用户决策 2026-08-12）：读完即止，按实际用时计语速。
  原先的 55s 硬窗口会把读得慢的用户截断，而语速分子仍按全文字数计，
  造成语速系统性高估 —— 且慢读者正是最需要被观察的一类，
  测量偏差方向与目标信号相反。
- 字数【只数汉字】：语速单位是字/分，标点不发音。
  「自由叙述」暂不启用 —— 中文声调由 F0 曲线实现，自由叙述会让
  F0 方差被词汇声调污染，跨用户不可比。固定文本使声调序列恒定，
  该污染成为常数偏移而自然抵消。
- 绕过 openface 的 psycho.*（它只是 3 行 AU 线性加权，与五维画像
  重复加权且未平滑）；本轮不使用 HRV（生产代码从未实现）
"""
from __future__ import annotations

import threading
import time

try:
    import requests
except ImportError:                       # 理论上不会发生，test2.py 已依赖
    requests = None


# ---------------------------------------------------------------- 任务定义

# 固定朗读文本 = 《北风与太阳》
# 来源：/home/lsz/HIKO/语音任务——《北风与太阳》.txt（用户提供的权威文档）
# 此前这里是我自拟的一段 225 字文本，现按文档更正。
# 选用《北风与太阳》的理由：它是国际语音学界朗读任务的标准篇章
# （The North Wind and the Sun，IPA 官方示例文本），
# 语义中性、覆盖常见声母韵母与四声，且跨研究可比。
FIXED_READING_TEXT = (
    "有一次，北风和太阳正在争论谁比较有本事。"
    "他们正好看到有个穿着大衣的人走过来，"
    "他们就说，谁可以让那个人脱掉那件大衣，就算谁比较有本事。"
    "于是北风开始拼命地吹。怎知，他吹得越厉害，"
    "那个人就越是用大衣包裹自己。最后，北风没办法，就放弃了。"
    "接着，太阳出来晒了一会儿，那个人感觉变得很热，"
    "立刻把大衣脱掉了。于是，北风只好认输了。"
)


def han_count(text):
    """只数汉字 —— 语速单位是「字/分」，标点不发音。

    此前用 len() 把标点也算进去，《北风与太阳》139 汉字 / 158 字符，
    会让字数虚高 13.7%，语速随之虚高同样比例。
    必须与服务端 audio_service._han_count 保持同一口径，
    否则朗读覆盖率（ASR字数/全文字数）会失真。
    """
    return sum(1 for ch in text
               if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf")

TASK_SPEC = {
    "sample_rate": 48000,          # 用户决策：48k
    "format": "wav_pcm16",         # 用户决策：前端封 WAV
    "encoding_note": "前端 AudioWorklet 采集 PCM 后自行封 WAV 头上传；"
                     "不使用 Opus/webm —— 有损压缩会污染 jitter/shimmer",
    "stages": [
        {
            "id": "vowel",
            "label": "持续元音",
            "duration_sec": 5,
            "prompt": "请用平稳的音量持续发「啊——」，尽量保持一口气",
            "sustained_vowel": True,     # 该段才允许测 jitter/shimmer
            "why": "周期扰动测量只在持续元音 + 高信噪比下稳定",
        },
        {
            "id": "reading",
            "label": "固定文本朗读",
            # 用户决策（2026-08-12）：朗读【不锁定固定时长】，读完即止，
            # 按实际用时计算语速（读完用 1 分 23 秒就按 83s 算）。
            # duration_sec 置 None = 由用户点「完成」结束，前端不倒计时。
            "duration_sec": None,
            "duration_mode": "until_user_done",
            "duration_hint_sec": 55,      # 仅用于给用户「大约需要多久」的预期
            "max_duration_sec": 180,      # 与服务端 MAX_DURATION_SEC 对齐的硬上限
            "prompt": "请按自己平常的语速朗读下面这段文字，读完后点击完成",
            "sustained_vowel": False,
            "text": FIXED_READING_TEXT,
            "text_char_count": han_count(FIXED_READING_TEXT),   # 只数汉字
            "text_total_chars": len(FIXED_READING_TEXT),        # 含标点，仅供参考
            "why": "固定文本使声调序列恒定，F0 方差的声调污染成为"
                   "常数偏移，跨用户比较时自然抵消",
            "why_no_fixed_duration":
                "锁定 55s 会让读得慢的用户被截断，而语速分子仍按全文字数计算，"
                "导致语速系统性高估 —— 且慢读者正是最需要被观察的一类，"
                "测量偏差方向与目标信号相反。按实际用时则语速为真值。"
                "分母取「首字起→末字止」的跨度（含内部停顿），"
                "故提前或延迟点「完成」都不影响结果。"
                "「是否读完全文」由服务端用 ASR 覆盖率校验，"
                "未读完时分子降级为实际识别字数并标记不可信。",
        },
    ],
    "disabled_stages": [
        {"id": "free_talk", "label": "自由叙述",
         "reason": "暂不启用（用户决策）。自由叙述下词汇声调随机，"
                   "F0 方差跨用户不可比，需强制对齐+按声调归一化才能支持"},
    ],
}


# ---------------------------------------------------------------- 客户端

class AudioClient:
    """
    audio_service(5003) 的 HTTP 客户端。
    照 test2.py 中 OpenFaceClient 的既有模式实现：失败不抛异常，
    统一返回 {"status": "error", "message": ...}，由调用方决定展示。
    """

    def __init__(self, base_url="http://127.0.0.1:5003", timeout=120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path, wav_bytes, params=None):
        if requests is None:
            return {"status": "error", "message": "requests 未安装"}
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                data=wav_bytes,
                params=params or {},
                headers={"Content-Type": "application/octet-stream"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                data.setdefault("status", "ok")
            return data
        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}

    def analyze(self, wav_bytes, sustained_vowel=False, text_char_count=None,
                deep=True):
        """上传 WAV 做分析。deep=False 时只跑轨A（不占 GPU）。"""
        params = {"sustained_vowel": "1" if sustained_vowel else "0"}
        if text_char_count:
            params["text_char_count"] = str(int(text_char_count))
        path = "/analyze" if deep else "/analyze_features"
        return self._post(path, wav_bytes, params)

    def health(self):
        if requests is None:
            return {"status": "error", "message": "requests 未安装"}
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"status": "error", "message": str(e)[:200]}


# ---------------------------------------------------------------- 会话状态

class AudioSessionState:
    """
    单个会话的音频状态。挂在 ClientSession 上（懒创建）。
    只保存「最近一次各阶段的分析结果」，不保存音频本体 ——
    原始录音留在内存里既占空间又涉及隐私，分析完即弃。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.stages = {}          # stage_id -> 分析结果
        self.updated_at = 0.0

    def put(self, stage_id, result):
        with self.lock:
            self.stages[stage_id] = result
            self.updated_at = time.time()

    def snapshot(self):
        with self.lock:
            return {
                "stages": dict(self.stages),
                "updated_at": self.updated_at,
                "completed": sorted(self.stages.keys()),
            }

    def merged(self):
        """
        把两个阶段的结果合成一份「音频输入层」摘要，供上层五维公式使用。

        合并规则（重要）：
          - jitter/shimmer/HNR 只取 vowel 段（唯一可信来源）
          - 节奏/语速/停顿 只取 reading 段（元音段没有语言节奏）
          - F0/能量 优先取 reading 段（更能代表自然说话状态）
          - 情绪分布 取 reading 段（元音段无语义、模型判定不稳）
        每项都带来源标注与可信标记，绝不把不可信值混进来充数。
        """
        with self.lock:
            vowel = (self.stages.get("vowel") or {})
            read = (self.stages.get("reading") or {})

        def s(d):
            return (d.get("summary") or {}) if isinstance(d, dict) else {}

        sv, sr_ = s(vowel), s(read)
        out = {
            "sources": {
                "vowel_present": bool(vowel),
                "reading_present": bool(read),
            },
            # ---- 嗓音质量：仅来自持续元音段 ----
            "voice_quality": {
                "jitter_local": sv.get("jitter_local"),
                "shimmer_local": sv.get("shimmer_local"),
                "hnr_db": sv.get("hnr_db"),
                "reliable": sv.get("voice_quality_reliable"),
                "reasons": sv.get("voice_quality_reasons"),
                "source": "vowel",
            },
            # ---- 节奏/语速：仅来自朗读段 ----
            "rhythm": {
                "speech_sec": sr_.get("speech_sec"),
                "speech_ratio": sr_.get("speech_ratio"),
                "pause_count": sr_.get("pause_count"),
                "pause_ratio": sr_.get("pause_ratio"),
                "speech_rate_cpm": sr_.get("speech_rate_cpm"),
                "reliable": sr_.get("rhythm_reliable"),
                "source": "reading",
            },
            # ---- 音高/能量：来自朗读段 ----
            "prosody": {
                "f0_mean": sr_.get("f0_mean"),
                "f0_semitone_std": sr_.get("f0_semitone_std"),
                "rms_variation": sr_.get("rms_variation"),
                "loudness_db_mean": sr_.get("loudness_db_mean"),
                "source": "reading",
                "comparability_note": "F0 方差的跨用户可比性依赖固定朗读文本；"
                                      "若启用自由叙述则失效",
            },
            # ---- 情绪：来自朗读段 ----
            "emotion": {
                "label": sr_.get("emotion_label"),
                "confidence": sr_.get("emotion_confidence"),
                "distribution": sr_.get("emotion_distribution"),
                "reliable": sr_.get("emotion_reliable"),
                "reasons": sr_.get("emotion_reasons"),
                "source": "reading",
            },
        }
        # 整体可用性：两段都缺则整层不可用
        out["usable"] = bool(sv.get("usable") or sr_.get("usable"))
        return out


# ---------------------------------------------------------------- 路由注册

def register_routes(app, get_session, client=None, jsonify=None, request=None):
    """
    把音频路由注册到既有 Flask app。

    参数
      app         : Flask 实例
      get_session : callable(session_id) -> session 或 None
      client      : AudioClient（默认新建）
      jsonify/request : 由调用方传入 flask 的对象，避免本模块重复 import

    新增路由（均不影响既有路由）
      GET  /audio/task_spec     录音任务定义（前端据此渲染引导）
      POST /audio/upload        上传某阶段 WAV，返回该段分析结果
      GET  /audio/result        取本会话已完成阶段 + 合并摘要
      GET  /audio/health        透传 audio_service 健康状态
    """
    if jsonify is None or request is None:
        from flask import jsonify as _j, request as _r
        jsonify, request = _j, _r

    cli = client or AudioClient()

    def _state(sess):
        st = getattr(sess, "audio_state", None)
        if st is None:
            st = AudioSessionState()
            sess.audio_state = st
        return st

    def _stage(stage_id):
        for s in TASK_SPEC["stages"]:
            if s["id"] == stage_id:
                return s
        return None

    @app.route("/audio/task_spec", methods=["GET"])
    def audio_task_spec():
        return jsonify(TASK_SPEC)

    @app.route("/audio/health", methods=["GET"])
    def audio_health():
        return jsonify(cli.health())

    @app.route("/audio/upload", methods=["POST"])
    def audio_upload():
        session_id = request.cookies.get("session_id")
        sess = get_session(session_id)
        if not sess:
            return jsonify({"status": "error",
                            "message": "Invalid session"}), 400

        stage_id = (request.args.get("stage") or "").strip()
        spec = _stage(stage_id)
        if spec is None:
            return jsonify({
                "status": "error",
                "message": f"未知阶段 '{stage_id}'",
                "valid_stages": [s["id"] for s in TASK_SPEC["stages"]],
            }), 400

        wav = request.get_data(cache=False, parse_form_data=False)
        if not wav:
            return jsonify({"status": "error",
                            "message": "空请求体：需要 WAV 音频"}), 400

        # 朗读段不锁时长，但仍需一个硬上限：否则录音忘关会上传巨大音频、
        # 长时间占住 GPU 锁。在进入模型前先拦，报错比 413 更可读。
        max_sec = spec.get("max_duration_sec")
        if max_sec:
            # 48kHz / int16 / 单声道 + 44 字节 WAV 头；与前端封装参数一致。
            est_sec = max(0.0, (len(wav) - 44) / float(48000 * 2))
            if est_sec > max_sec * 1.05:      # 5% 宽容，容忍封装开销差异
                return jsonify({
                    "status": "error",
                    "message": f"录音过长（约 {est_sec:.0f}s > 上限 {max_sec:.0f}s）",
                    "hint": "朗读不锁定时长，但仍有上限；"
                            "请确认读完后已点击完成。",
                }), 413

        result = cli.analyze(
            wav,
            sustained_vowel=spec.get("sustained_vowel", False),
            text_char_count=spec.get("text_char_count"),
            deep=not spec.get("sustained_vowel", False),  # 元音段无语义，免跑ASR/情绪
        )
        result["stage"] = stage_id
        _state(sess).put(stage_id, result)
        return jsonify(result)

    @app.route("/audio/result", methods=["GET"])
    def audio_result():
        session_id = request.cookies.get("session_id")
        sess = get_session(session_id)
        if not sess:
            return jsonify({"status": "error",
                            "message": "Invalid session"}), 400
        st = _state(sess)
        snap = st.snapshot()
        snap["merged"] = st.merged()
        snap["status"] = "ok"
        snap["required_stages"] = [s["id"] for s in TASK_SPEC["stages"]]
        return jsonify(snap)

    return cli


# ==============================================================================
# INTEGRATION —— test2.py 需要加入的代码（仅 3 处，共 5 行）
# ==============================================================================
# 1) 文件头 import 区附近：
#        import audio_client
#
# 2) app = Flask(__name__) 之后（约 1990 行）：
#        audio_client.register_routes(app, rppg_app.get_session)
#    注意：需在 rppg_app 已创建之后调用。若 rppg_app 在 app 之后创建，
#    则把这行放到 rppg_app 创建之后。
#
# 3) （可选）ClientSession.__init__ 中显式初始化，便于调试：
#        self.audio_state = None      # 由 audio_client 懒创建
#
# 不需要改动 rPPG 主链路、upload_frame、video_feed 等任何既有逻辑。
# ==============================================================================
