# -*- coding: utf-8 -*-
"""
portrait_narrate.py —— 事实清单与结论文案生成（规则层）

为什么单独一层，而不是塞进 portrait_score.py：
  score 层回答「数值是多少」，narrate 层回答「这些数值怎么说给人听」。
  前者一旦改动就要重跑全部数值断言，后者改文案不该牵动任何数值测试。

为什么是规则而不是模型：
  这一栏要显示的每个数字都必须能溯源到一个具体字段。规则层的输出是
  确定性的、可单元测试的、零外部依赖的 —— 它既是最终兜底，也是喂给
  LLM 的唯一输入（LLM 只被允许改写它，不允许看原始数据、不允许自己算）。

产出两样东西：
  facts(pt, snap) -> dict   结构化事实清单，供 LLM 输入与数字回验
  narrate(pt,snap)-> dict   {title, tags, body, source:'rule'} 直接可渲染
"""

# 综合分分档。措辞刻意用【中性描述】而非评价性词汇：
# 「状态良好」是对人的评价，「各项指标偏高」是对数据的描述。
# 我们测的是指标，不是人。
BANDS = (
    (40.0, "多项指标偏低"),
    (60.0, "指标中等区间"),
    (80.0, "多项指标良好"),
    (None, "各项指标偏高"),
)

# 维度中文名 -> 拉低/贡献项的可读措辞。key 与 portrait_score.terms 对齐。
TERM_LABEL = {
    "emo_stability":  "情绪稳定度",
    "dominant_ratio": "主导情绪持续占比",
    "hr":             "心率",
    "resp":           "呼吸率",
    "au04_inv":       "皱眉强度（反向）",
    "au04":           "皱眉强度",
    "gaze_stability": "注视稳定性",
    "pose_steady":    "头姿稳定性",
    "dass_stress":    "DASS-21 压力分量表",
    "f0_std":         "音高变化幅度",
    "positive_emo":   "正向情绪占比",
    "rms_var":        "音量变异系数",
}

# 固定名词里自带的数字，必须预置进白名单。
# 否则数字回验会把「DASS-21」的 21、「/ 100」的 100 当成 LLM 编造的
# 测量值而误杀整段输出 —— 这类数字不是测量结果，是名称/量纲的一部分。
LITERAL_NUMBERS = ("21", "100")

# 采集步骤 id -> 中文名。portrait_score L433 直接把 readiness["blocking"]
# 里的 id 拼进 composite.missing，于是文案会长成「缺: face、voice」——
# 那是给程序看的标识符，不是给人看的话。在 narrate 层翻译，
# 不动 score 层：数值逻辑与措辞该分开改、分开测。
STEP_LABEL = {
    "face":  "面部与视线采集",
    "voice": "语音测试",
    "scale": "量表评估 (DASS-21)",
}


def _humanize(text):
    """把后端 missing 文案里的步骤 id 换成中文名。"""
    if not text:
        return text
    for k, v in STEP_LABEL.items():
        # 只替换作为独立词出现的 id，避免命中 face_full 这类子串
        for a, b in ((k + "、", v + "、"), ("缺: " + k, "缺: " + v),
                     (k + "）", v + "）"), ("、" + k, "、" + v)):
            text = text.replace(a, b)
    return text


def _band(v):
    for hi, txt in BANDS:
        if hi is None or v < hi:
            return txt
    return BANDS[-1][1]


def _fmt(v):
    """分数统一取整显示。保留一位小数会让人误以为精度到 0.1。"""
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _isnum(v):
    """bool 是 int 的子类，必须显式排除，否则 True 会被当成 1。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _add_num(bag, v):
    """
    往白名单里塞一个数，同时塞它的等价写法。
    72.0 与 72 是同一个测量值的两种写法；只收一种会让回验
    在 LLM 合理地写「72」时误判为编造。
    """
    if v is None:
        return
    s = ("%g" % v) if isinstance(v, float) else str(v)
    bag.add(s)
    if isinstance(v, float) and abs(v - round(v)) < 1e-9:
        bag.add(str(int(round(v))))
    else:
        bag.add(s)
    if isinstance(v, float):
        bag.add("%.1f" % v)


def _find_hr(pt, snap):
    """
    心率的真实路径是 readiness["hr"]，【不在 face 里】。
    它随面部采集一同固化（见 portrait_state.put_face），
    compute_portrait 也是从 rd["hr"] 取的 —— 这里刻意与计分层
    同源，避免「结论栏的心率」和「放松度用的心率」不是一个数。
    """
    for src in (pt.get("readiness"), snap.get("readiness"), snap):
        if isinstance(src, dict) and isinstance(src.get("hr"), dict):
            return src["hr"]
    return None


def facts(pt, snap=None):
    """
    把计分结果压成一份【只含事实】的清单。
    刻意不含任何评价、建议、推断 —— 那些是下游的事，而且下游不该有。
    """
    pt = pt or {}
    snap = snap or {}
    dims = pt.get("dimensions") or []
    comp = pt.get("composite") or {}

    nums = set(LITERAL_NUMBERS)
    out = {
        "composite": _fmt(comp.get("value")),
        "composite_note": comp.get("note"),
        "composite_missing": [_humanize(x) for x in (comp.get("missing") or [])],
        "gated": bool(pt.get("gated")),
        "dimensions": [],
        "scale": None,
        "unavailable": [],
    }
    _add_num(nums, out["composite"])

    for d in dims:
        v = _fmt(d.get("value"))
        item = {
            "id": d.get("id"),
            "label": d.get("label"),
            "value": v,
            "higher_is_worse": bool(d.get("higher_is_worse")),
            "exploratory": bool(d.get("exploratory")),
            "missing": list(d.get("missing") or []),
            "top_term": None,
            "low_term": None,
        }
        if v is None:
            out["unavailable"].append({
                "label": d.get("label"),
                "why": [_humanize(x) for x in (d.get("missing") or [])],
            })
        else:
            _add_num(nums, v)
            # 找贡献最高/最低的项，用于「主要由…贡献 / 被…拉低」。
            # 只在 term 有值时比较：None 是缺测，不是 0。
            ts = {k: x for k, x in (d.get("terms") or {}).items()
                  if _isnum(x)}
            if ts:
                hi = max(ts, key=lambda k: ts[k])
                lo = min(ts, key=lambda k: ts[k])
                item["top_term"] = TERM_LABEL.get(hi, hi)
                if lo != hi:
                    item["low_term"] = TERM_LABEL.get(lo, lo)
        out["dimensions"].append(item)

    # 缺项条数会被写进标签行，LLM 也可能合理地转述它，故进白名单
    if out["unavailable"]:
        _add_num(nums, len(out["unavailable"]))

    hr = _find_hr(pt, snap)
    if isinstance(hr, dict) and _isnum(hr.get("heart_rate")):
        out["heart_rate"] = round(float(hr["heart_rate"]), 1)
        _add_num(nums, out["heart_rate"])

    # DASS-21：量表自带分档解释，直接转述，不自己造判断
    sc = ((snap.get("scale") or {}).get("scored") or {}).get("subscales") or {}
    if sc:
        rows = []
        for k in ("D", "A", "S"):
            g = sc.get(k)
            if not isinstance(g, dict):
                continue
            raw = g.get("raw")
            rows.append({"key": k, "label": g.get("label") or k,
                         "raw": raw, "level": g.get("level")})
            _add_num(nums, raw)
        if rows:
            out["scale"] = rows

    out["numbers"] = sorted(nums)
    return out


def narrate(pt, snap=None):
    """
    规则层文案。三个槽位一次给齐：
      title  左侧大圆圈下方的定性标题
      tags   标题下的标签行
      body   核心结论段落
    """
    f = facts(pt, snap)
    comp = f["composite"]

    # ---- 标题与标签 ----
    if comp is None:
        title = "待评估"
        tags = [{"text": "采集未齐备", "kind": "gray"}]
    else:
        title = _band(float(comp))
        tags = [{"text": "综合 %d" % comp, "kind": "cy"}]
        if f["unavailable"]:
            tags.append({"text": "%d 项暂缺" % len(f["unavailable"]),
                         "kind": "gray"})

    # ---- 正文 ----
    seg = []
    if comp is None:
        why = f["composite_missing"][0] if f["composite_missing"] else "采集未齐备"
        seg.append("综合分暂未计算：%s。" % why)
    else:
        seg.append("本次综合 %d 分（四维等权，压力值取反后计入；"
                   "活力值为探索性指标，不计入）。" % comp)

    # 已出数的维度：按分值降序，先说高的
    got = [d for d in f["dimensions"] if d["value"] is not None]
    if got:
        parts = []
        for d in sorted(got, key=lambda x: -x["value"]):
            s = "%s %d" % (d["label"], d["value"])
            if d["higher_is_worse"]:
                s += "（越低越好）"
            if d["low_term"]:
                s += "，主要拉低项为%s" % d["low_term"]
            elif d["top_term"]:
                s += "，主要由%s贡献" % d["top_term"]
            parts.append(s)
        seg.append("；".join(parts) + "。")

    if "heart_rate" in f:
        seg.append("本次心率 %.1f bpm。" % f["heart_rate"])

    # 缺项：必须写明缺什么。这一句是整段里最要紧的 ——
    # 把「没测到」说成「正常」，比不给结论危险得多。
    if f["unavailable"]:
        us = ["%s（%s）" % (u["label"], "；".join(u["why"]) or "数据缺失")
              for u in f["unavailable"]]
        seg.append("以下维度本次未计算，因所需数据尚未采集到：" +
                   "、".join(us) + "。")

    if f["scale"]:
        rs = ["%s %s（%s）" % (r["label"], r["raw"], r["level"])
              for r in f["scale"] if r["raw"] is not None]
        if rs:
            seg.append("DASS-21 自评：" + "、".join(rs) + "。")

    seg.append("以上为本次采集指标的客观描述，非临床结论。")

    return {"title": title, "tags": tags, "body": "".join(seg),
            "source": "rule", "facts": f}
