# -*- coding: utf-8 -*-
"""
portrait_state.py —— 五维心理画像的「快照仓」（方案 B）
==============================================================================
为什么需要这一层
----------------
清点三份原始数据的存活情况后发现，它们根本不共存：

  * 面部/视线：FrameBuffer(fps=15, window_sec=60) 是 deque(maxlen=900)，
               滚动 60 秒，之后被挤掉。用户花 3 分钟答量表，答完时最初的
               面部数据已经不存在。
  * 语音：     AudioState.stages 挂在 session 上，已是快照模式（无需改）。
  * DASS-21：  只活在浏览器 JS 的模块级变量里，刷新页面即全丢，
               后端完全不知道用户答了什么。

所以「等三份数据齐备再算五维」这件事，在原实现下做不到 —— 不是流程问题，
是数据结构问题。本模块的职责就是把「易失的实时值」转成「固化的快照」：

  面部 -> 用户点「完成采集」时把当下的 60s 聚合值固化一份
  语音 -> 沿用 audio_client 已有的 stages（本模块只读）
  量表 -> 前端 POST 上来，存进同一个快照

三者齐备后才允许计算五维。任一缺失，对应维度返回 None 并说明缺什么，
【不做权重重分配】—— 那会让同一个人在不同完成度下得到不同分数，
使纵向比较失去意义。

边界（重要）
------------
本模块只做「存取 + 完成度判定」，【不含五维公式】。公式尚有未决问题
（活力值在中性朗读任务下先天缺信号、jitter/shimmer 常态不可信、
pct 与临床切点不对齐），未定稿前不写进代码，避免用错误公式产出
「看起来精确」的分数 —— 那比没有分数更有害。

不改动 rPPG 主链路：本模块不 import test2，不触碰 upload_frame /
video_feed / 心率计算的任何逻辑，只在 session 上挂一个属性。
==============================================================================
"""
from __future__ import annotations

import threading
import time

# 计分层。作为【可选依赖】导入：公式改动频繁，若它出错也不该让
# 完成度闸门与三份数据的固化一起失效 —— 那是本模块更根本的职责。
_score = None
try:
    import portrait_score as _score          # 生产环境：与 test2.py 同级
except Exception:                            # pragma: no cover
    # 这里兜 Exception 而非仅 ImportError：模块顶层若有任何错
    # （语法、常量计算），都不该让完成度闸门一起挂掉。
    try:
        from . import portrait_score as _score   # 开发环境：包内相对导入
    except Exception:
        _score = None

# 叙述层（结论文案）。同样作为【可选依赖】：文案生成挂了应当退化为
# 「没有结论」，而不是让五维数值和完成度一起 500。
_narr = None
try:
    import portrait_narrate as _narr
except Exception:                            # pragma: no cover
    try:
        from . import portrait_narrate as _narr
    except Exception:
        _narr = None

# LLM 解读层。同样是【可选依赖】，而且是这几个里最该容错的一个：
# 它依赖外部网络与付费接口，不可用是常态而非异常。导入失败、凭证缺失、
# 接口超时，都必须退化成「这块暂不可用」，绝不能影响五维数值、完成度，
# 更不能波及 rPPG 主链路。
_llm = None
try:
    import llm_client as _llm
except Exception:                            # pragma: no cover
    try:
        from . import llm_client as _llm
    except Exception:
        _llm = None

# 报告单渲染层（2026-08-21 新增）。同样是【可选依赖】：
# 报告是「读快照 -> 出 HTML」的纯渲染，它挂掉绝不该影响采集、
# 完成度闸门、五维计分，更不能波及 rPPG 主链路。
# 【为什么由本模块挂载报告路由，而不是在 test2.py 里挂】
# test2.py 是线上独有文件（不在部署清单里，dev 侧那份是旧的），
# 改它意味着手工改线上代码，既无法版本化也无法回滚。
# portrait_state.py 已在部署清单且线上已调用其 register_routes，
# 从这里挂载可以让报告随常规部署一起上线。
_report = None
try:
    import portrait_report as _report
except Exception:                            # pragma: no cover
    try:
        from . import portrait_report as _report
    except Exception:
        _report = None


def _attach_narrative(out, snap):
    """
    给画像结果挂上结论文案。就地修改并返回 out。

    刻意在【后端】生成而不是前端拼字符串：规则文案与将来的 LLM 文案
    必须走同一条渲染路径，否则「LLM 挂了回落规则」会变成两套 DOM
    逻辑，其中一套永远没人测。
    """
    if _narr is None:
        out["narrative"] = {"title": None, "tags": [], "body": None,
                            "source": "unavailable",
                            "error": "叙述模块 portrait_narrate 未加载"}
        return out
    try:
        out["narrative"] = _narr.narrate(out, snap)
    except Exception as e:                   # pragma: no cover
        # 文案失败不能带走数值：五维仍要显示，只是结论栏空着。
        out["narrative"] = {"title": None, "tags": [], "body": None,
                            "source": "error",
                            "error": "结论生成失败: %s" % str(e)[:160]}
    return out


# 面部快照允许的最大陈旧时间（秒）。超过则视为过期，需重新采集。
# 取 15 分钟：既容忍用户中途去答量表，又不至于把半小时前的状态
# 当成「当下状态」参与计算。
FACE_SNAPSHOT_TTL_SEC = 900

# 面部快照要求的最小有效窗口（秒）。FrameBuffer 满窗是 60s，
# 低于 20s 的样本量不足以支撑 gaze_stability（其内部要求 >=5 样本）
# 与 60s 偏移率的统计意义。
FACE_MIN_WINDOW_SEC = 20

STAGE_IDS = ("vowel", "reading")     # 与 audio_client.TASK_SPEC 对齐


# ---------------------------------------------------------------- 量表校验
def _validate_dass21(answers):
    """
    校验前端上传的 DASS-21 答案。

    为什么要在后端重新校验：前端 shell_panels.js 里已有构造期自检，
    但那只保证「代码里的分组没写错」。上传的答案来自网络，题号可能
    缺失/越界/重复/非整数。若不校验就入库，会在算分时静默得到偏低
    但看起来正常的分数 —— 这类「合理的错值」最难发现。

    返回 (ok: bool, cleaned: dict|None, err: str|None)
    cleaned 的键统一为 int，值为 0..3 的 int。
    """
    if not isinstance(answers, dict):
        return False, None, "answers 必须是对象（题号 -> 0..3）"

    cleaned = {}
    for k, v in answers.items():
        # 键可能是字符串（JSON 对象的键天然是字符串）
        try:
            n = int(k)
        except (TypeError, ValueError):
            return False, None, "题号非整数: %r" % (k,)
        if not (1 <= n <= 21):
            return False, None, "题号越界（应为 1..21）: %d" % n
        if n in cleaned:
            return False, None, "题号重复: %d" % n
        # 值必须是 0..3 的整数。布尔值在 Python 里是 int 的子类，
        # 会让 True 悄悄变成 1，故显式排除。
        if isinstance(v, bool) or not isinstance(v, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                return False, None, "第%d题答案非整数: %r" % (n, v)
        if not (0 <= v <= 3):
            return False, None, "第%d题答案越界（应为 0..3）: %d" % (n, v)
        cleaned[n] = v

    if len(cleaned) != 21:
        missing = [n for n in range(1, 22) if n not in cleaned]
        return False, None, ("DASS-21 必须 21 题全部作答，缺 %d 题: %s。"
                             "缺项求和会得到偏低但看似正常的分数，故拒收"
                             % (len(missing), missing))
    return True, cleaned, None


def _face_snapshot_usable(snap, now=None):
    """
    判定面部快照是否可用。返回 (ok, reason)。

    三个门槛，缺一不可：
      1) 存在
      2) 未过期（TTL）—— 半小时前的状态不是「当下状态」
      3) 窗口足够长 —— 样本量不足时 gaze_stability 本身就是 None
    """
    if not snap:
        return False, "尚未采集面部数据"
    now = now if now is not None else time.time()
    age = now - float(snap.get("captured_at") or 0)
    if age > FACE_SNAPSHOT_TTL_SEC:
        return False, ("面部快照已过期（%.0f 分钟前采集，上限 %.0f 分钟），"
                       "需重新采集" % (age / 60.0,
                                       FACE_SNAPSHOT_TTL_SEC / 60.0))
    win = snap.get("window_sec")
    if win is None or float(win) < FACE_MIN_WINDOW_SEC:
        return False, ("面部有效窗口不足（%s 秒 < %d 秒），"
                       "样本量不够支撑注视稳定性统计"
                       % (win, FACE_MIN_WINDOW_SEC))
    return True, None


# ---------------------------------------------------------------- 快照仓
class PortraitState:
    """
    单会话的画像快照仓。挂在 ClientSession 上，与 audio_state 平级。

    线程安全：Flask 多 worker 下同一 session 可能被并发访问
    （前端同时轮询面部与提交量表），故所有读写走同一把锁。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.face = None          # 面部 60s 聚合快照（固化）
        self.scale = None         # DASS-21 答案 + 计分
        self.hr = None            # 心率/呼吸快照（与面部同时固化）
        # AI 综合解读的会话副本。
        # 【为什么要在这里再存一份】llm_client 内部那个 _cache 是
        # 「防重复付费调用」的性能缓存：TTL 只有 5 分钟、键是数据指纹、
        # 进程重启即空。而解读在语义上是【本次测评的结论】，和量表得分
        # 一样属于会话资产，不该随性能缓存一起过期。此前报告页只读那个
        # 缓存，导致解读生成 5 分钟后再打开报告，AI 那栏就凭空留白。
        self.ai = None

    # ---------------- 面部 ----------------
    def put_face(self, payload, hr_text=None, resp_text=None):
        """
        固化面部快照。payload 为 /get_openface 的完整响应。

        只挑【真实可用】字段落盘。固化假值一个都不存 ——
        存了就迟早有人拿去算，注释拦不住。被排除的是：
        ear_l/ear_r(恒0.30)、perclos(恒0)、blink_rate(恒0)、
        pose.roll(恒0)、au_symmetry(恒0.96)、au_activity(恒0)、
        attention_score(被前三项污染而退化)、psycho.*(用户已禁用)。
        """
        if not isinstance(payload, dict):
            return False, "面部数据格式错误"
        if payload.get("status") not in ("ok", "success", None):
            # status 为 idle/error 时没有可固化的内容
            if payload.get("status") in ("idle", "error"):
                return False, ("摄像头数据未就绪（status=%s），"
                               "请确认画面中有人脸后再完成采集"
                               % payload.get("status"))

        pose = payload.get("pose") or {}
        win = payload.get("window_sec")
        if win is None:
            return False, "面部聚合窗口未就绪（window_sec 缺失）"
        if float(win) < FACE_MIN_WINDOW_SEC:
            return False, ("面部有效窗口仅 %s 秒，需至少 %d 秒。"
                           "请保持在画面中继续采集"
                           % (win, FACE_MIN_WINDOW_SEC))

        snap = {
            "captured_at": time.time(),
            "window_sec": win,
            # --- 情绪（EMA 平滑后的分布 + 稳定性）---
            "emo_distribution": payload.get("emo_distribution"),
            "emo_stability": payload.get("emo_stability"),
            "emo_switches": payload.get("emo_switches"),
            "emo_dominant_duration_sec": payload.get("emo_dominant_duration_sec"),
            # --- 头姿/视线（yaw/pitch 已做零点校准，见 behavior_view.js）---
            "pose_yaw": pose.get("yaw"),
            "pose_pitch": pose.get("pitch"),
            "gaze_state": payload.get("gaze_state"),
            "gaze_stability": payload.get("gaze_stability"),
            "pose_deviation_60s": payload.get("pose_deviation_60s"),
            # --- AU（8 维强度 + 最强项）---
            "au_intensity": payload.get("au_intensity"),
            "au_dominant": payload.get("au_dominant"),
            # --- 诊断用，不参与公式 ---
            "confidence": payload.get("confidence"),
            "is_demo": payload.get("is_demo"),
        }
        with self.lock:
            self.face = snap
            # 心率与面部同时固化：两者都是「当下状态」，
            # 若分开采集会出现「面部是 10 分钟前、心率是现在」的错配。
            self.hr = _norm_hr(hr_text, resp_text)
        return True, None

    # ---------------- 量表 ----------------
    def put_scale(self, answers, scored=None):
        """
        存 DASS-21。answers 必须 21 题齐全（校验见 _validate_dass21）。
        scored 为前端算好的分数，仅作留档核对；后端不信任它，
        实际计分在 score_dass21() 里独立重算。
        """
        ok, cleaned, err = _validate_dass21(answers)
        if not ok:
            return False, err
        mine = score_dass21(cleaned)
        rec = {
            "submitted_at": time.time(),
            "answers": cleaned,
            "scored": mine,
        }
        # 前后端算分不一致 = 有一边的实现错了，必须暴露而非静默取一方
        if isinstance(scored, dict):
            rec["client_scored"] = scored
            rec["client_agrees"] = _scores_agree(mine, scored)
        with self.lock:
            self.scale = rec
        return True, None

    # ---------------- AI 解读 ----------------
    def put_ai(self, result, fingerprint=None):
        """
        固化一次成功的 AI 解读，使其不再受 llm_client 缓存 TTL 约束。

        fingerprint 是【生成这份解读时所依据的数据】的指纹
        （llm_client.payload_fingerprint）。存它不是为了让解读过期，
        而是为了在读取时能诚实地告诉用户「此后又新增了测量项」——
        直接丢弃反而会退回到用户不接受的留白行为。
        """
        if not isinstance(result, dict) or not (
                result.get("summary") or result.get("points")):
            # 只固化真有内容的解读。失败态（unavailable/reason）不落库，
            # 否则会把一条错误信息永久钉在报告上，且盖住后续成功的解读。
            return False
        rec = dict(result)
        rec.pop("cached", None)       # 缓存命中标记属于那次调用，不属于内容
        rec["generated_at"] = rec.get("cached_at") or time.time()
        rec["fingerprint"] = fingerprint
        with self.lock:
            self.ai = rec
        return True

    def get_ai(self, fingerprint=None):
        """
        取会话固化的解读。返回 dict 或 None。

        传入当前数据指纹时，额外附带 stale 标记：
          stale=False  解读依据的数据与当下一致
          stale=True   此后又采集了新数据，解读只覆盖较早的一部分
        照常返回内容而不是丢弃 —— 有旧结论并标注清楚，
        比什么都不显示更有信息量（这正是本次修复的初衷）。
        """
        with self.lock:
            rec = self.ai
        if not isinstance(rec, dict):
            return None
        out = dict(rec)
        if fingerprint is not None and rec.get("fingerprint") is not None:
            out["stale"] = (rec["fingerprint"] != fingerprint)
        else:
            out["stale"] = False
        out.pop("fingerprint", None)   # 指纹是内部实现，不外泄到快照/报告
        return out

    def clear_ai(self):
        with self.lock:
            self.ai = None

    def clear_scale(self):
        with self.lock:
            self.scale = None

    def clear_face(self):
        with self.lock:
            self.face = None
            self.hr = None


# ---------------------------------------------------------------- DASS-21 计分
# 分量表题号。与前端 shell_panels.js 的 groups 必须一致，
# 构造期自检在下方 _verify_groups()。
DASS_GROUPS = {
    "D": {"label": "抑郁", "items": [3, 5, 10, 13, 16, 17, 21]},
    "A": {"label": "焦虑", "items": [2, 4, 7, 9, 15, 19, 20]},
    "S": {"label": "压力", "items": [1, 6, 8, 11, 12, 14, 18]},
}

# 严重程度切点，针对 ×2 之后的分数（DASS-21 是 DASS-42 半量表）。
# 不乘 2 则单项满分仅 21，永远达不到「极重度」——
# 这个错误不会报错，只会一直算低。
DASS_CUTOFFS = {
    "D": [(9, "正常"), (13, "轻度"), (20, "中度"), (27, "重度"), (None, "极重度")],
    "A": [(7, "正常"), (9, "轻度"), (14, "中度"), (19, "重度"), (None, "极重度")],
    "S": [(14, "正常"), (18, "轻度"), (25, "中度"), (33, "重度"), (None, "极重度")],
}
DASS_RAW_MULTIPLIER = 2
DASS_SCORE_MAX = 42          # 7 题 × 3 分 × 2

# 严重度 -> [0,1] 的等距映射。
# 为什么不用前端的 pct（= score/42）：三个分量表的临床切点完全不同
# （焦虑「正常」上限 7 -> pct 17%，压力「正常」上限 14 -> pct 33%），
# 同一个 pct 在不同分量表代表的严重度天差地别。直接拿 pct 进公式
# 会让三维的严重度标尺不一致。改用分级序号归一，各分量表对齐。
DASS_LEVEL_NORM = {"正常": 0.0, "轻度": 0.25, "中度": 0.5,
                   "重度": 0.75, "极重度": 1.0}


def _verify_groups():
    all_items = sorted(sum((g["items"] for g in DASS_GROUPS.values()), []))
    if all_items != list(range(1, 22)):
        raise RuntimeError("DASS-21 分组异常：21 题未被恰好覆盖一次 -> %s"
                           % all_items)
    for k, g in DASS_GROUPS.items():
        if len(g["items"]) != 7:
            raise RuntimeError("DASS-21 %s 分量表题数应为 7，实为 %d"
                               % (k, len(g["items"])))


_verify_groups()


def _level_of(key, score):
    for bound, name in DASS_CUTOFFS[key]:
        if bound is None or score <= bound:
            return name
    return "极重度"


def score_dass21(answers):
    """
    独立计分（不依赖前端）。answers 已由 _validate_dass21 清洗过。

    缺项不求和：任一分量表缺题则该分量表返回 None。
    缺项求和会得到偏低但看起来正常的分数，比缺失更危险。
    """
    out = {"complete": True, "subscales": {},
           "raw_multiplier": DASS_RAW_MULTIPLIER,
           "score_max": DASS_SCORE_MAX}
    for key, g in DASS_GROUPS.items():
        missing = [n for n in g["items"] if answers.get(n) is None]
        if missing:
            out["complete"] = False
            out["subscales"][key] = {
                "key": key, "label": g["label"], "raw": None, "score": None,
                "level": None, "level_norm": None, "pct": None,
                "missing": missing,
            }
            continue
        raw = sum(answers[n] for n in g["items"])
        score = raw * DASS_RAW_MULTIPLIER
        level = _level_of(key, score)
        out["subscales"][key] = {
            "key": key, "label": g["label"],
            "raw": raw, "score": score, "level": level,
            # level_norm 才是应该进公式的量（见 DASS_LEVEL_NORM 说明）
            "level_norm": DASS_LEVEL_NORM[level],
            # pct 保留仅供 UI 画进度条，标注其不可跨分量表比较
            "pct": round(score / DASS_SCORE_MAX * 100),
            "pct_note": "占满分百分比，非常模百分位；不可跨分量表比较严重度",
            "missing": [],
        }
    return out


def _scores_agree(mine, theirs):
    """比对前后端计分。只比 raw（最原始的和），避免因四舍五入误报。"""
    try:
        t = (theirs or {}).get("subscales") or {}
        for k in DASS_GROUPS:
            a = (mine["subscales"].get(k) or {}).get("raw")
            b = (t.get(k) or {}).get("raw")
            if a != b:
                return False
        return True
    except Exception:
        return False


def _norm_hr(hr_text, resp_text):
    """
    把心率/呼吸的展示字符串转成可用数值。

    这是必须做的一步：primary_hr_display_val 初值是字符串 "0"。
    若不判空直接进公式，「未测到」会被当成「心率 0」——
    在放松度里算出负分，在压力值里算成「毫无压力」，
    恰恰是最危险的假阳性方向。故这里显式区分「未测到」与「测到 0」。
    """
    def one(v, lo, hi):
        if v is None:
            return None
        try:
            f = float(str(v).strip())
        except (TypeError, ValueError):
            return None
        # 0 是「尚未测到」的哨兵值，不是真实测量结果
        if f <= 0:
            return None
        if not (lo <= f <= hi):
            return None          # 超出生理范围，视为无效而非截断
        return round(f, 1)

    hr = one(hr_text, 30.0, 220.0)
    resp = one(resp_text, 4.0, 60.0)
    return {
        "captured_at": time.time(),
        "heart_rate": hr,
        "respiration_rate": resp,
        "heart_rate_available": hr is not None,
        "respiration_rate_available": resp is not None,
        "note": ("展示值 \"0\" 表示尚未测到，已转为 None；"
                 "不可当成「心率 0」参与计算"),
    }


# ---------------------------------------------------------------- 完成度判定
# ---------------------------------------------------------------- 公式规格
# ask#35 的《五维心理画像设计方案》经逐项数值验证后，有 3 处硬错误与
# 6 项必改。它们都不是风格问题，是「按原方案实现会得到系统性偏差」。
# 这里把结论连同证据一起固化，原因：公式尚未定稿（见 FORMULA_OPEN_QUESTIONS），
# 但这些修正是已验证的事实，只留在对话里下一个动手的人会重新写错一遍。
#
# 每条 fix 的字段含义
#   term      涉及的公式项
#   wrong     原方案的写法
#   right     应改成什么
#   evidence  为什么 —— 一律给可复现的数值/源码位置，不写"经验上"
FORMULA_FIXES = [
    {
        "id": "loudness_gain_invariant",
        "term": "响度 / 音量",
        "wrong": "loudness_db_mean，归一化区间 [-40, -15] dB",
        "right": "改用 energy.rms_variation；若确需绝对音量，"
                 "取 meta.rms_dbfs（它在峰值归一化【之前】测得）",
        "evidence": "features_handcrafted.py L528-531：yn = y / peak 之后才调用 "
                    "energy_features(yn, sr)。峰值归一化把绝对电平抹掉了，"
                    "该量实际测的是波峰因数而非响度。三档增益仿真"
                    "(-6/-20/-34 dBFS) 输出全部为 -7.27 dB，"
                    "即该项与音量【完全无关】。原区间 [-40,-15] 使该项对"
                    "任何人恒等于 1.0，等于常数，不携带信息。",
    },
    {
        "id": "speech_rate_range",
        "term": "语速",
        "wrong": "归一化区间 [200, 400] 字/分",
        "right": "[95, 160] 字/分",
        "evidence": "朗读文本《北风与太阳》实测 139 个汉字（含标点 158）。"
                    "中文朗读正常速度 100~160 字/分：55s 读完 = 151.6，"
                    "83s = 100.5。原区间下正常朗读者该项恒为 0，"
                    "把「正常」判成「最差」。",
    },
    {
        "id": "au_cap",
        "term": "AU 强度归一化",
        "wrong": "统一除以 0.6",
        "right": "除以 0.3（AU25 例外，仍为 0.6），并沿用前端的 "
                 "sqrt(v/cap) 映射",
        "evidence": "shell_panels.js 行为卡 AU 上限表：AU12/AU06/AU04/AU09/BROW "
                    "均为 0.3，仅 AU25 为 0.6。除以 0.6 会把所有面部项"
                    "系统性砍半。sqrt 的理由是 AU 日常波动多在 0~0.1，"
                    "线性映射下看不出差异。",
    },
    {
        "id": "hr_null_and_clip",
        "term": "心率项",
        "wrong": "直接取 primary_hr_display_val 参与计算",
        "right": "None 时该维度整项不计算并说明缺什么；有值时按生理范围裁剪",
        "evidence": "primary_hr_display_val 初值是字符串 \"0\"，是"
                    "「尚未测量」哨兵而非测得的 0。喂进公式会得到"
                    "「深度放松 / 零压力」—— 这是最危险的假阳性方向。"
                    "_norm_hr() 已在本模块拦掉 (f <= 0 -> None) 并做 "
                    "30-220 bpm 范围校验。",
    },
    {
        "id": "use_level_norm",
        "term": "量表分进公式",
        "wrong": "使用 pct（= score / 42）",
        "right": "使用 level_norm（严重度分级 -> 0/0.25/0.5/0.75/1.0）",
        "evidence": "pct 是占满分百分比，不是百分位。三个分量表临床切点"
                    "完全不同（抑郁正常 <=9 -> 21%，焦虑 <=7 -> 17%，"
                    "压力 <=14 -> 33%）。线上实测：score=12 时"
                    "「焦虑 中度」与「压力 正常」都显示 29%。"
                    "直接拿 pct 进公式会让三维的严重度标尺不一致。",
    },
    {
        "id": "au_fluctuation_no_source",
        "term": "情绪稳定维度中的「AU 强度波动」",
        "wrong": "情绪稳定 = f(emo_stability, AU 强度波动, 主导情绪时长)",
        "right": "去掉 AU 强度波动项，权重并入 emo_stability",
        "evidence": "实现批次 3b 时发现该项【没有数据源】。"
                    "flask_openface_patch.py L78 取的是 parsed（单帧瞬时值）"
                    "而非 metrics（60s 聚合），故快照里的 au_intensity 是"
                    "【一帧】的强度，算不出窗口内波动。要么让 "
                    "openface_service 额外输出 au_std（跨目录，本次范围外），"
                    "要么去掉。已去掉，并在维度返回的 dropped 字段里"
                    "写明原因 —— 不留一个看起来算了、实际没算的空位。",
    },
    {
        "id": "drop_emo_switches",
        "term": "情绪稳定维度",
        "wrong": "同时纳入 emo_stability 与 emo_switches",
        "right": "只用 emo_stability，去掉 emo_switches",
        "evidence": "两者测的是同一件事（情绪波动程度），"
                    "metrics_aggregator 里 emo_stability 本身就由窗口内"
                    "情绪方差导出。同时纳入等于对同一信号加权两次。",
    },
]

# 公式定稿前必须由使用方拍板的问题。写在代码里而不是只在对话里，
# 是因为「为什么还不出分」这个问题会被反复问到。
FORMULA_OPEN_QUESTIONS = [
    {
        "id": "vitality_signal",
        "question": "活力值在中性朗读任务下先天缺信号，如何处置？",
        "detail": "按原方案对一名健康被试实测得 31.8/100，且 5 项中有 3 项"
                  "在中性朗读任务里结构性接近 0（任务本身不诱发唤醒）。"
                  "这不是阈值问题，是任务设计与构念不匹配。",
        "options": ["改为相对被试自身基线的变化量（需要方案 C 的跨会话留存）",
                    "标注为探索性指标，不计入综合分",
                    "更换任务（如加入情绪诱发或自由表达段）"],
        "resolved": True,
        "decision": "标注为探索性指标，不计入综合分",
        "decided_at": "ask#39 (2026-08-13)",
        "rationale": "不算等于把已采到的信号丢掉；计入综合分则会把一个"
                     "已知系统性偏低的量掺进结论。折中是「算、给看、"
                     "但不进结论」，并在返回字段里写明为什么。"
                     "「相对自身基线」这个更好的选项需要方案 C 的"
                     "跨会话留存，等持久化落地后可切换。",
    },
    {
        "id": "voice_quality_availability",
        "question": "是否接受 jitter / shimmer 常态不可用？",
        "detail": "voice_quality_features 要求 HNR >= 15 dB 才标 reliable，"
                  "常规采集环境下多数样本达不到。若接受，压力维度需重配到"
                  "自评 level_norm + 心率 + AU04 三项。",
        "options": ["接受不可用，压力维度按三项重配",
                    "提高录音要求（安静环境 + 近场麦克风）后重评",
                    "降低 HNR 门槛并标注结果可信度"],
        "resolved": True,
        "decision": "接受不可用，压力维度重配为 自评 level_norm + 心率 + AU04",
        "decided_at": "ask#39 (2026-08-13)",
        "rationale": "jitter/shimmer 一律不进任何维度，即使某次采集恰好 "
                     "reliable —— 时而进时而不进会让同一个人的分数"
                     "不可纵向比较，而纵向比较正是这套系统的主要用途。",
    },
]

FORMULA_SPEC = {
    # 批次 3b：两项未决问题已由使用方拍板（见各 question 的 decision），
    # 公式已实现于 portrait_score.py。此处 status 随之改为已定稿。
    "status": ("已定稿 (3b-2026-08-13)" if _score is not None
               else "已定稿但计分模块未加载"),
    "spec_version": "3b-2026-08-13",
    "fixes": FORMULA_FIXES,
    "open_questions": FORMULA_OPEN_QUESTIONS,
    # 权重从计分模块原样取出，不在此处复写 —— 复写就会出现
    # 「文档写的和实际算的不一样」，而这种不一致没有任何报错。
    "weights": (getattr(_score, "WEIGHTS", None) if _score else None),
    "composite": ({
        "included": list(getattr(_score, "COMPOSITE_DIMENSIONS", ())),
        "inverted": list(getattr(_score, "COMPOSITE_INVERTED", ())),
    } if _score else None),
    "ranges": ({
        "speech_rate_cpm": list(getattr(_score, "SPEECH_RATE_RANGE", ())),
        "heart_rate_bpm": list(getattr(_score, "HR_RELAX_RANGE", ())),
        "respiration_rate": list(getattr(_score, "RESP_RELAX_RANGE", ())),
        "f0_semitone_std": list(getattr(_score, "F0_STD_RANGE", ())),
        "rms_variation": list(getattr(_score, "RMS_VAR_RANGE", ())),
        "au_cap_default": getattr(_score, "AU_CAP_DEFAULT", None),
        "au_cap_special": getattr(_score, "AU_CAP_SPECIAL", None),
    } if _score else None),
    "policy": "缺项时对应维度返回 None 并说明缺什么，不做权重重分配 —— "
              "重分配会让同一个人在不同完成度下得到不同分数，"
              "纵向比较失去意义。",
}


def readiness(pstate, audio_state=None, now=None):
    """
    三份数据的完成度报告 —— 方案 B 的核心闸门。

    这是「拿到所有数据才能出画像」这条规则的唯一判定处。
    前端据此渲染完成度清单（而不是强制线性向导），
    用户能看到还缺什么，而不是被进度条推着走。

    返回结构固定，字段齐全，缺失项写明原因。
    """
    now = now if now is not None else time.time()
    with pstate.lock:
        face = dict(pstate.face) if pstate.face else None
        scale = dict(pstate.scale) if pstate.scale else None
        hr = dict(pstate.hr) if pstate.hr else None

    # --- 面部 ---
    f_ok, f_reason = _face_snapshot_usable(face, now)
    face_step = {
        "id": "face", "label": "面部与视线采集",
        "done": bool(f_ok),
        "reason": f_reason,
        "captured_at": (face or {}).get("captured_at"),
        "window_sec": (face or {}).get("window_sec"),
    }

    # --- 语音（读 audio_client 已有的 stages，本模块不重复存）---
    completed = []
    if audio_state is not None:
        try:
            completed = sorted((audio_state.snapshot() or {}).get("completed") or [])
        except Exception:
            completed = []
    missing_stages = [s for s in STAGE_IDS if s not in completed]
    voice_step = {
        "id": "voice", "label": "语音测试",
        "done": not missing_stages,
        "reason": (None if not missing_stages
                   else "尚未完成: %s" % "、".join(
                       {"vowel": "持续元音(5s)",
                        "reading": "固定文本朗读"}.get(s, s)
                       for s in missing_stages)),
        "completed_stages": completed,
        "required_stages": list(STAGE_IDS),
    }

    # --- 量表 ---
    s_done = bool(scale and (scale.get("scored") or {}).get("complete"))
    scale_step = {
        "id": "scale", "label": "量表评估 (DASS-21)",
        "done": s_done,
        "reason": (None if s_done else "尚未提交完整的 21 题作答"),
        "submitted_at": (scale or {}).get("submitted_at"),
    }

    steps = [face_step, voice_step, scale_step]
    blocking = [s["id"] for s in steps if not s["done"]]

    return {
        "steps": steps,
        "ready": not blocking,
        "blocking": blocking,
        # 心率不作为独立步骤（它随面部采集一同固化），但要单独报告
        # 可用性 —— 缺它会让放松度/压力值/活力值的生理项失效。
        "hr": hr,
        "hr_available": bool(hr and hr.get("heart_rate_available")),
        "policy": ("五维画像需三项全部完成。缺项时对应维度返回 None 并"
                   "说明缺什么，不做权重重分配 —— 重分配会让同一个人在"
                   "不同完成度下得到不同分数，纵向比较失去意义。"),
        # 批次 3b 起公式已定稿并实现。这里如实反映当前状态，
        # 不再写「不输出五维分数」—— 那已经过期了，而过期的
        # 状态描述比没有描述更糟：它会让人不去查真实行为。
        "formula_status": (
            "已定稿 (3b-2026-08-13)。7 项必改已落地；"
            "活力值按拍板结果标注为探索性指标、不计入综合分；"
            "jitter/shimmer 已永久排除，压力维度重配为"
            "自评 level_norm + 心率 + AU04。五维见 GET /portrait/portrait。"
            if _score is not None else
            "计分模块 portrait_score 未加载，五维不可用（仅完成度判定可用）"),
        # 详细的必改项与未决问题见 GET /portrait/formula_spec
        "formula_fix_count": len(FORMULA_FIXES),
        "formula_open_count": len(FORMULA_OPEN_QUESTIONS),
    }


def snapshot(pstate, audio_state=None, now=None):
    """完整快照：三份原始数据 + 完成度。供前端渲染与后续公式消费。"""
    with pstate.lock:
        face = dict(pstate.face) if pstate.face else None
        scale = dict(pstate.scale) if pstate.scale else None
    voice = None
    if audio_state is not None:
        try:
            voice = audio_state.merged()
        except Exception as e:
            voice = {"error": str(e)[:120]}
    rd = readiness(pstate, audio_state, now)
    out = {
        "face": face,
        "voice": voice,
        "scale": scale,
        "readiness": rd,
    }
    # 五维直接附在快照里，省掉前端再发一次请求。
    # 计分失败不影响快照本身可用 —— 快照是数据，五维是解释。
    out["portrait"] = _compute_or_error({"face": face, "voice": voice,
                                         "scale": scale}, rd)
    # narrate 需要 readiness（心率在 rd["hr"]）与 scale（DASS 转述），
    # 故把 out 本身当 snap 传进去 —— 它已含这两者。
    _attach_narrative(out["portrait"], out)
    return out


def _compute_or_error(snap, rd):
    """
    调用计分层并兜住异常。返回 None 会让前端无从判断，
    故失败时返回带 error 的骨架，前端可原样显示。
    """
    if _score is None:
        return {"error": "计分模块 portrait_score 未加载",
                "dimensions": [], "composite": {"value": None}}
    try:
        return _score.compute_portrait(snap, rd)
    except Exception as e:                       # pragma: no cover
        return {"error": "计分失败: %s" % str(e)[:160],
                "dimensions": [], "composite": {"value": None}}


# ---------------------------------------------------------------- 路由注册
def register_routes(app, get_session, jsonify=None, request=None):
    """
    挂载画像路由。与 audio_client.register_routes 同构，复用既有 cookie 会话。

    新增路由（均不影响既有路由）
      POST /portrait/face      固化面部快照（前端在「完成采集」时调用）
      POST /portrait/scale     提交 DASS-21 全部作答
      GET  /portrait/readiness  三份数据完成度（前端轮询渲染清单）
      GET  /portrait/snapshot   完整快照（三份原始数据 + 完成度）
      GET  /portrait/portrait   五维画像（只回结论，不回原始数据）
      GET  /portrait/formula_spec 公式规格（必改项/拍板结果/权重，只读常量）
      POST /portrait/reset      重置（face / scale / all）
      GET  /portrait/report     最终报告单（HTML，浏览器内打印/另存为 PDF）
      GET  /portrait/report.json 报告数据（调试用）
    """
    if jsonify is None or request is None:
        from flask import jsonify as _j, request as _r
        jsonify, request = _j, _r

    def _pstate(sess):
        st = getattr(sess, "portrait_state", None)
        if st is None:
            st = PortraitState()
            sess.portrait_state = st
        return st

    def _astate(sess):
        # 只读取 audio_client 懒创建的状态，绝不在这里替它创建 ——
        # 那会绕过 audio_client 自己的初始化逻辑。
        return getattr(sess, "audio_state", None)

    def _sess_or_400():
        sid = request.cookies.get("session_id")
        sess = get_session(sid)
        if not sess:
            return None, (jsonify({"status": "error",
                                   "message": "Invalid session"}), 400)
        return sess, None

    @app.route("/portrait/face", methods=["POST"])
    def portrait_face():
        sess, err = _sess_or_400()
        if err:
            return err
        # 面部数据由后端自己读取，不接受前端上传 ——
        # 前端可篡改，且它拿不到 5002 的 60s 聚合值。
        payload = getattr(sess, "latest_openface", None)
        hr_text = getattr(sess, "primary_hr_display_val", None)
        resp_text = getattr(sess, "current_resp_display_val", None)
        ok, msg = _pstate(sess).put_face(payload, hr_text, resp_text)
        if not ok:
            return jsonify({"status": "error", "message": msg}), 409
        return jsonify({"status": "ok",
                        "readiness": readiness(_pstate(sess), _astate(sess))})

    @app.route("/portrait/scale", methods=["POST"])
    def portrait_scale():
        sess, err = _sess_or_400()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        ok, msg = _pstate(sess).put_scale(body.get("answers"),
                                          body.get("scored"))
        if not ok:
            return jsonify({"status": "error", "message": msg}), 400
        st = _pstate(sess)
        return jsonify({"status": "ok",
                        "scored": (st.scale or {}).get("scored"),
                        "client_agrees": (st.scale or {}).get("client_agrees"),
                        "readiness": readiness(st, _astate(sess))})

    @app.route("/portrait/readiness", methods=["GET"])
    def portrait_readiness():
        sess, err = _sess_or_400()
        if err:
            return err
        r = readiness(_pstate(sess), _astate(sess))
        r["status"] = "ok"
        return jsonify(r)

    @app.route("/portrait/snapshot", methods=["GET"])
    def portrait_snapshot():
        sess, err = _sess_or_400()
        if err:
            return err
        s = snapshot(_pstate(sess), _astate(sess))
        s["status"] = "ok"
        return jsonify(s)

    @app.route("/portrait/portrait", methods=["GET"])
    def portrait_portrait():
        """
        五维画像。与 /portrait/snapshot 的区别：这里只回结论，
        不回三份原始数据 —— 前端渲染五维卡不需要几十个原始字段，
        少传就少一份被误用的机会。
        """
        sess, err = _sess_or_400()
        if err:
            return err
        st, ast_ = _pstate(sess), _astate(sess)
        rd = readiness(st, ast_)
        with st.lock:
            face = dict(st.face) if st.face else None
            scale = dict(st.scale) if st.scale else None
        voice = None
        if ast_ is not None:
            try:
                voice = ast_.merged()
            except Exception:
                voice = None
        out = _compute_or_error({"face": face, "voice": voice,
                                 "scale": scale}, rd)
        out["readiness"] = rd
        _attach_narrative(out, {"face": face, "voice": voice,
                                "scale": scale, "readiness": rd})
        out["status"] = "ok"
        return jsonify(out)

    @app.route("/portrait/formula_spec", methods=["GET"])
    def portrait_formula_spec():
        # 只读常量，不需要会话 —— 它是设计规格而非用户数据。
        return jsonify(dict(FORMULA_SPEC, status="ok"))

    @app.route("/portrait/ai_summary", methods=["POST"])
    def portrait_ai_summary():
        """
        AI 综合解读。前端「生成解读」按钮打这个接口。

        设计取舍：
          * 用 POST 而非 GET —— 它有副作用（真的花钱调外部模型），
            且不该被浏览器/代理缓存。
          * 只喂【已采集】的指标，未测项连键都不出现（见 llm_client.build_payload），
            以免模型顺手把洞填上 —— 这正是原先硬编码文案编出
            "疲劳指数 41""高于常模基线"的同类失误。
          * 任何失败都回 200 + status="unavailable" + reason，而不是 5xx。
            前端据此回落到诚实占位；这块是旁路功能，
            绝不能因为模型不可用就把整个面板搞成错误态。
        """
        sess, err = _sess_or_400()
        if err:
            return err

        if _llm is None:
            return jsonify({"status": "unavailable",
                            "reason": "AI 解读模块未加载"})

        st = _pstate(sess)
        s = snapshot(st, _astate(sess))
        try:
            res = _llm.interpret_cached(s)
            # 固化进会话：llm_client 的缓存 5 分钟就过期，而报告可能在
            # 很久之后才打开。不固化的话，报告里 AI 那栏会凭空留白。
            try:
                fp, _ = _llm.payload_fingerprint(s)
                st.put_ai(res, fingerprint=fp)
            except Exception as e:                   # pragma: no cover
                # 固化失败不影响本次返回 —— 用户此刻已经拿到解读了，
                # 受影响的只是稍后打开报告时能否读到。
                print("[PORTRAIT] AI 解读固化失败（不影响本次展示）: %s" % e)
            res["status"] = "ok"
            return jsonify(res)
        except Exception as e:
            # 这里刻意不打印堆栈：失败原因（凭证/超时/采集不足）都是
            # 可预期的运行状况，不是缺陷。原文回给前端展示即可。
            return jsonify({"status": "unavailable",
                            "reason": str(e)[:200]})

    @app.route("/portrait/ai_status", methods=["GET"])
    def portrait_ai_status():
        """
        轻量探测：凭证是否就绪。只读配置，不发网络请求、不花钱。
        前端用它决定「生成解读」按钮是否可点。
        """
        if _llm is None:
            return jsonify({"status": "ok", "available": False,
                            "reason": "AI 解读模块未加载"})
        avail = _llm.available()
        return jsonify({"status": "ok", "available": avail,
                        "reason": None if avail else "未配置模型 API 凭证"})

    @app.route("/portrait/reset", methods=["POST"])
    def portrait_reset():
        sess, err = _sess_or_400()
        if err:
            return err
        what = ((request.get_json(silent=True) or {}).get("what") or "all")
        st = _pstate(sess)
        if what in ("face", "all"):
            st.clear_face()
        if what in ("scale", "all"):
            st.clear_scale()
        # 解读是对被清掉那批数据下的结论，数据没了它就是无主的旧话。
        # 不一并清除，会出现「已重置，但报告里仍挂着上一轮结论」的错配。
        st.clear_ai()
        return jsonify({"status": "ok", "cleared": what,
                        "readiness": readiness(st, _astate(sess))})

    # ---------------- 报告单 ----------------
    # 委托给 portrait_report 挂载。注入 snapshot_fn 而不是让报告层
    # 直接 import 本模块 —— 那会形成循环 import。
    if _report is not None:
        try:
            def _ai_for_report(sess, snap):
                """
                报告用的 AI 解读取数。【只读】—— 绝不触发模型调用：
                报告页是 GET，若在这里现场调模型，用户每刷新一次报告
                就会真花一次钱，且违反 GET 无副作用的约定。

                取数顺序（2026-08-22 修）：
                  ① 会话固化副本 —— 权威来源，不受 5 分钟 TTL 影响
                  ② llm_client.peek_cached —— 兜底：解读刚生成、
                     或由固化之前的老路径产生时仍能取到
                  ③ 都没有 -> None，报告如实留白
                先固化后缓存，是因为固化副本带 stale 标记（能说明
                「解读之后又新增了测量项」），信息比裸缓存更完整。
                """
                fp = None
                if _llm is not None:
                    try:
                        fp, _ = _llm.payload_fingerprint(snap)
                    except Exception:
                        fp = None
                try:
                    got = _pstate(sess).get_ai(fingerprint=fp)
                    if got:
                        return got
                except Exception:
                    pass
                if _llm is not None:
                    peek = getattr(_llm, "peek_cached", None)
                    if peek is not None:
                        try:
                            return peek(snap)
                        except Exception:
                            pass
                return None

            _report.register_routes(
                app, get_session,
                snapshot_fn=lambda sess: snapshot(_pstate(sess),
                                                  _astate(sess)),
                jsonify=jsonify, request=request,
                ai_peek_fn=_ai_for_report)
        except Exception as e:                       # pragma: no cover
            # 报告路由挂载失败绝不能影响上面已注册的采集路由。
            print("[PORTRAIT] 报告路由挂载失败，报告功能不可用: %s" % e)
