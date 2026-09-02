# -*- coding: utf-8 -*-
"""
最终报告单渲染层（2026-08-21 新增）

设计拍板结果（ask#6）：
  1) 颜色：白底黑字，允许彩色布局（不是单色！配色用于分区与等级标注）
  2) 不做五维拆解页（terms/weights 明细不进报告）
  3) DASS-21 逐题作答【进】报告
  4) 数据不齐备时【允许】生成，但必须显著标注
  5) 单次报告，不做纵向趋势（后端无持久化，见下）

为什么是「HTML + @media print」而不是后端生成 PDF：
  用户要求是「通过浏览器的 PDF 浏览页面进行浏览，并提供下载和打印」。
  浏览器的打印预览天然同时满足这三件事（预览/另存为PDF/打印），
  且不引入 WeasyPrint/ReportLab 这类重依赖，也不需要额外配中文字体。
  jsPDF 截图方案被否决：文字会变成位图，不可选、不可搜、缩放模糊。

为什么报告不做「较上次改善 X%」：
  PortraitState 挂在 session 上，/portrait/reset 即清空，后端【没有
  任何持久化存储】。硬做趋势就只能编造基线，那比没有趋势更糟。
  要纵向对比需先加测评历史落库层，属独立需求。

本模块只读快照，不写任何状态；渲染失败也不影响其它路由。
"""

import html
import json
import time

# DASS-21 题干。
# 【重要】这份文本与前端 static/shell_panels.js 的 DASS.items 必须逐字一致。
# 报告在后端渲染，拿不到前端那份数组，故这里必须留一份。
# 两处不一致会让「报告上的题干」与「用户实际看到并作答的题干」错位 ——
# 这是静默错误，没有任何报错，但报告就是错的。
# 故下方 verify_dass_items() 提供交叉校验入口，部署闸门会调用它。
DASS_ITEM_TEXTS = {
    1:  ("S", "我觉得很难让自己平静下来。"),
    2:  ("A", "我感到口干。"),
    3:  ("D", "我好像无法再有任何愉快、舒畅的感觉。"),
    4:  ("A", "我感到呼吸困难（例如：呼吸急促、透不过气，并且不是因为体力消耗造成的）。"),
    5:  ("D", "我感到很难主动去开始做事情。"),
    6:  ("S", "我对事情往往反应过度。"),
    7:  ("A", "我感到颤抖（例如：双手发抖）。"),
    8:  ("S", "我觉得自己消耗了很多精力在紧张焦虑上。"),
    9:  ("A", "我担心一些让自己惊慌或出丑的场合。"),
    10: ("D", "我觉得自己对未来没有什么可期待的。"),
    11: ("S", "我发现自己很容易心烦意乱。"),
    12: ("S", "我感到很难放松下来。"),
    13: ("D", "我感到忧郁、沮丧。"),
    14: ("S", "对任何阻碍我继续完成手头工作的事情，我都无法容忍。"),
    15: ("A", "我感到自己接近恐慌。"),
    16: ("D", "我对任何事情都无法产生热情。"),
    17: ("D", "我觉得自己作为一个人没什么价值。"),
    18: ("S", "我感觉自己很容易被激怒。"),
    19: ("A", "即使在没有体力消耗的情况下，我也能感觉到自己的心跳（例如：感到心率加快、心跳漏拍）。"),
    20: ("A", "我无缘无故地感到害怕。"),
    21: ("D", "我觉得生活毫无意义。"),
}

# DASS-21 作答选项。0~3 四档，与前端一致。
DASS_CHOICE_LABELS = {
    0: "不符合",
    1: "有时",
    2: "经常",
    3: "总是",
}

# 情绪英文 -> 中文。与前端 emotion_view.js 的口径保持一致。
EMO_ZH = {
    "neutral": "平静", "happy": "愉悦", "happiness": "愉悦",
    "sad": "悲伤", "sadness": "悲伤",
    "angry": "愤怒", "anger": "愤怒",
    "fear": "恐惧", "fearful": "恐惧",
    "disgust": "厌恶", "surprise": "惊讶", "surprised": "惊讶",
    "contempt": "轻蔑",
}

# AU 编号 -> 解剖学含义。报告要给出可读名称，光写 AU04 对用户没有意义。
AU_ZH = {
    "AU01": "抬眉内侧", "AU02": "抬眉外侧", "AU04": "皱眉",
    "AU05": "上睑提升", "AU06": "颊部提升", "AU07": "眼睑收紧",
    "AU09": "皱鼻", "AU10": "上唇提升", "AU12": "嘴角上扬",
    "AU14": "嘴角收紧", "AU15": "嘴角下压", "AU17": "下巴提升",
    "AU20": "嘴角横拉", "AU23": "双唇收紧", "AU25": "双唇分开",
    "AU26": "下颌下降", "AU45": "眨眼",
}


def verify_dass_items(frontend_js_text):
    """
    交叉校验：后端题干 vs 前端 shell_panels.js 里的题干。
    返回 (ok, problems)。供部署闸门调用 —— 这类不一致必须在上线前拦住。
    """
    import re
    problems = []
    pat = re.compile(r"\{\s*n:\s*(\d+)\s*,\s*dim:\s*'([DAS])'\s*,"
                     r"\s*text:\s*'([^']*)'\s*\}")
    found = {int(n): (d, t) for n, d, t in pat.findall(frontend_js_text)}
    if not found:
        return False, ["未能从前端文件解析出任何 DASS 题目（正则不匹配，"
                       "可能前端结构已变）"]
    for n in range(1, 22):
        fe, be = found.get(n), DASS_ITEM_TEXTS.get(n)
        if fe is None:
            problems.append("第 %d 题：前端缺失" % n)
            continue
        if be is None:
            problems.append("第 %d 题：后端缺失" % n)
            continue
        if fe[0] != be[0]:
            problems.append("第 %d 题分量表不一致：前端 %s / 后端 %s"
                            % (n, fe[0], be[0]))
        if fe[1] != be[1]:
            problems.append("第 %d 题题干不一致：\n  前端 %s\n  后端 %s"
                            % (n, fe[1], be[1]))
    return (not problems), problems


# ---------------------------------------------------------------- 小工具

def _e(v):
    """HTML 转义。所有进模板的动态内容都必须过这里。"""
    return html.escape("" if v is None else str(v), quote=True)


def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return None if f != f else f          # NaN
    except (TypeError, ValueError):
        return None


def _fmt(v, nd=1, unit="", dash="—"):
    """数值格式化。None 一律显示为破折号 —— 绝不显示 0 代替缺失。"""
    f = _num(v)
    if f is None:
        return dash
    s = ("%%.%df" % nd) % f
    if nd > 0:
        s = s.rstrip("0").rstrip(".") or "0"
    return s + unit


def _pct1(v, dash="—"):
    """0~1 的比率 -> 百分比字符串。"""
    f = _num(v)
    return dash if f is None else "%.0f%%" % (f * 100.0)


def _ts(v):
    f = _num(v)
    if f is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f))


def _emo_zh(k):
    return EMO_ZH.get(str(k).strip().lower(), str(k))


def _level_color(level):
    """DASS 严重度 -> 配色。白底报告上用色块标注等级，比纯文字易读。"""
    return {
        "正常": "#1B7F4B", "轻度": "#8A6D1F", "中度": "#B25E00",
        "重度": "#B3261E", "极重度": "#7A1216",
    }.get(level, "#444")


def _score_color(v, higher_is_worse=False):
    """
    分数 -> 配色。注意 higher_is_worse 维度（压力值）方向相反，
    不做这个区分会让「压力 85」显示成绿色，是严重的误导。
    """
    f = _num(v)
    if f is None:
        return "#9AA0A6"
    if higher_is_worse:
        f = 100.0 - f
    if f >= 75:
        return "#1B7F4B"
    if f >= 50:
        return "#8A6D1F"
    if f >= 25:
        return "#B25E00"
    return "#B3261E"


# ---------------------------------------------------------------- 打印样式

# 拍板：白底黑字 + 彩色布局。
# 关键取舍记录：
#   - 屏幕与打印【共用同一套白底样式】。首页是深色科技风，但报告若屏幕深色
#     打印白色，用户在预览里看到的和打印出来的不一致，"所见即所得"就没了。
#     报告是要被打印/存档的正式文档，一致性 > 与首页视觉统一。
#   - 彩色仅用于「信息编码」：分区色条、等级色块、分数色。
#     不用大面积色底 —— 那会吃墨且在灰度打印机上糊成一片。
#   - @page 用 A4 纵向 + 14mm 页边距。
#   - 分页控制靠 page-break-inside:avoid，不靠固定高度：
#     固定高度在不同浏览器/缩放下会错位。
REPORT_CSS = """
:root{
  --ink:#14181F; --ink-2:#3C4450; --ink-3:#6B7480; --line:#D8DEE6;
  --line-2:#EDF0F4; --brand:#0B5FA5; --brand-2:#0E7C86;
  --warn-bg:#FFF6E5; --warn-bd:#E0A93B; --warn-ink:#7A4E00;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{
  background:#F2F4F7; color:var(--ink);
  font-family:"Source Han Sans SC","Noto Sans CJK SC","Microsoft YaHei",
              "PingFang SC","Hiragino Sans GB",sans-serif;
  font-size:16px; line-height:1.6;
  -webkit-print-color-adjust:exact; print-color-adjust:exact;
}
.sheet{
  width:210mm; min-height:297mm; margin:12px auto; padding:14mm 14mm 16mm;
  background:#fff; box-shadow:0 2px 14px rgba(0,0,0,.14); position:relative;
}
/* ---- 操作条：仅屏幕可见，打印时隐藏 ---- */
.toolbar{
  position:sticky; top:0; z-index:50; background:#14181F; color:#fff;
  padding:10px 16px; display:flex; gap:10px; align-items:center;
  flex-wrap:wrap; font-size:13px;
}
.toolbar .sp{flex:1;}
.toolbar button{
  font:inherit; cursor:pointer; border:1px solid #4A5563; border-radius:4px;
  background:#232A34; color:#fff; padding:6px 14px;
}
.toolbar button.primary{background:var(--brand); border-color:var(--brand);}
.toolbar button:hover{filter:brightness(1.15);}
.toolbar .hint{color:#AEB6C2; font-size:12px;}
/* ---- 页头 ---- */
.rpt-head{border-bottom:2px solid var(--brand); padding-bottom:10px;
          display:flex; align-items:flex-start; gap:14px;}
.rpt-head h1{margin:0; font-size:21px; letter-spacing:.5px;}
.rpt-head .sub{color:var(--ink-3); font-size:11px; margin-top:3px;}
.rpt-head .meta{margin-left:auto; text-align:right; font-size:11px;
                color:var(--ink-2); line-height:1.7; white-space:nowrap;}
.rpt-head .meta b{color:var(--ink); font-weight:600;}
/* ---- 缺数据横幅（拍板4：允许生成但显著标注）---- */
.banner{
  margin:12px 0 0; border:1px solid var(--warn-bd); border-left-width:5px;
  background:var(--warn-bg); color:var(--warn-ink); padding:9px 12px;
  border-radius:3px; page-break-inside:avoid;
}
.banner .t{font-weight:700; font-size:12.5px;}
.banner ul{margin:5px 0 0 18px; padding:0;}
.banner li{margin:2px 0;}
.banner.ok{background:#EAF6EE; border-color:#3E8E5A; color:#1B5E33;}
/* ---- 区块 ---- */
.sec{margin-top:16px; page-break-inside:avoid;}
.sec>h2{
  font-size:13.5px; margin:0 0 8px; padding-left:9px;
  border-left:4px solid var(--brand); line-height:1.25;
}
.sec>h2 small{font-weight:400; color:var(--ink-3); font-size:10.5px;
              margin-left:6px;}
.sec.alt>h2{border-left-color:var(--brand-2);}
/* ---- 综合评分 ---- */
.hero{display:flex; gap:16px; align-items:stretch; page-break-inside:avoid;}
.hero .big{
  border:1.5px solid var(--brand); border-radius:6px; padding:12px 18px;
  text-align:center; min-width:150px; display:flex; flex-direction:column;
  justify-content:center;
}
.hero .big .v{font-size:40px; font-weight:700; line-height:1.05;
              color:var(--brand);}
.hero .big .v small{font-size:14px; font-weight:400; color:var(--ink-3);}
.hero .big .k{font-size:11px; color:var(--ink-2); margin-top:2px;}
.hero .big .lv{margin-top:5px; font-size:12px; font-weight:700;}
.hero .txt{flex:1; font-size:11.5px; color:var(--ink-2);}
/* ---- 表格 ---- */
table.t{width:100%; border-collapse:collapse; font-size:11px;}
table.t th,table.t td{border:1px solid var(--line); padding:4px 7px;
                      text-align:left; vertical-align:top;}
table.t th{background:#F5F7FA; font-weight:600; color:var(--ink-2);
           white-space:nowrap;}
table.t td.n{text-align:right; font-variant-numeric:tabular-nums;
             white-space:nowrap;}
table.t tr.zebra td{background:#FAFBFC;}
table.t td.dash{color:var(--ink-3);}
/* ---- 条形（五维/情绪/DASS 共用）---- */
.bar{position:relative; height:9px; background:var(--line-2);
     border-radius:5px; overflow:hidden; min-width:70px;}
.bar>i{position:absolute; left:0; top:0; bottom:0; border-radius:5px;
       display:block;}
/* ---- 两栏 ---- */
.cols{display:flex; gap:14px;}
.cols>*{flex:1; min-width:0;}
/* ---- 逐题作答（拍板3）---- */
.qa{font-size:10.5px;}
.qa th,.qa td{padding:3px 6px;}
.qa .q{width:auto;}
.qa .ans{white-space:nowrap; font-weight:600;}
.privacy{font-size:10px; color:var(--ink-3); margin:0 0 6px;}
/* ---- 附录 / 免责 ---- */
.note{font-size:10.5px; color:var(--ink-2); line-height:1.65;}
.note ul{margin:4px 0 0 16px; padding:0;}
.note li{margin:2.5px 0;}
.disc{
  margin-top:14px; border:1px solid var(--line); border-radius:3px;
  background:#FAFBFC; padding:9px 12px; font-size:10.5px; color:var(--ink-2);
  page-break-inside:avoid;
}
.disc b{color:#B3261E;}
.foot{margin-top:12px; border-top:1px solid var(--line); padding-top:7px;
      font-size:9.5px; color:var(--ink-3); display:flex; gap:10px;}
.foot .sp{flex:1;}
.tag{display:inline-block; padding:1px 6px; border-radius:9px;
     font-size:9.5px; border:1px solid currentColor; white-space:nowrap;}
.pill{display:inline-block; padding:1px 7px; border-radius:3px; color:#fff;
      font-size:10px; font-weight:600; white-space:nowrap;}

/* ================= 打印 ================= */
@page{ size:A4 portrait; margin:14mm; }
@media print{
  body{background:#fff;}
  .toolbar{display:none !important;}
  .sheet{width:auto; min-height:0; margin:0; padding:0; box-shadow:none;}
  .pb{page-break-before:always;}
  a[href]:after{content:"";}
}
"""


# ---------------------------------------------------------------- 区块渲染

def _sec_banner(snap):
    """
    数据完备性横幅。拍板4：不齐备也生成，但必须显著标注。
    这块永远渲染 —— 齐备时显示绿色确认条，缺失时显示黄色警示条。
    「没有横幅」会让人分不清是齐备还是模块没跑。
    """
    rd = (snap or {}).get("readiness") or {}
    steps = rd.get("steps") or []
    miss = [s for s in steps if not s.get("done")]
    hr_ok = bool(rd.get("hr_available"))

    if not miss and hr_ok:
        return ('<div class="banner ok"><div class="t">'
                '数据完备性：三项采集已全部完成，心率/呼吸可用。'
                '本报告基于完整数据生成。</div></div>')

    li = []
    for s in miss:
        li.append("<li><b>%s</b>：%s</li>"
                  % (_e(s.get("label") or s.get("id")),
                     _e(s.get("reason") or "未完成")))
    if not hr_ok:
        li.append("<li><b>心率 / 呼吸</b>：未测得。"
                  "受此影响，<b>放松度</b>与<b>压力值</b>的生理项缺失，"
                  "该两项得分不完整。</li>")
    warn = ""
    if miss:
        warn = ("<div style='margin-top:6px'>按既定策略，缺项时对应维度"
                "不做权重重分配（重分配会使同一个人在不同完成度下得到"
                "不同分数，失去可比性），因此<b>综合评分不予输出</b>。</div>")
    return ('<div class="banner"><div class="t">'
            '⚠ 数据不完备 —— 本报告基于部分数据生成，请谨慎解读</div>'
            '<ul>%s</ul>%s</div>' % ("".join(li), warn))


def _sec_hero(snap):
    """综合评分 + AI 解读摘要。"""
    p = (snap or {}).get("portrait") or {}
    comp = p.get("composite") or {}
    v = _num(comp.get("value"))
    lv = comp.get("level") or comp.get("label")

    if v is None:
        big = ('<div class="v" style="color:#9AA0A6">—</div>'
               '<div class="k">综合心理表现</div>'
               '<div class="lv" style="color:#B25E00">待评估</div>')
    else:
        col = _score_color(v)
        big = ('<div class="v" style="color:%s">%s<small>/100</small></div>'
               '<div class="k">综合心理表现</div>'
               '<div class="lv" style="color:%s">%s</div>'
               % (col, _fmt(v, 0), col, _e(lv or "")))

    # 综合分构成说明：必须交代活力值被排除，否则用户会算不平账
    dims = p.get("dimensions") or []
    inc = [d.get("label") for d in dims
           if not d.get("exclude_from_composite") and d.get("value") is not None]
    exc = [d for d in dims if d.get("exclude_from_composite")]
    txt = []
    if inc:
        txt.append("<div><b>计入综合分：</b>%s</div>"
                   % _e("、".join(str(x) for x in inc)))
    for d in exc:
        txt.append("<div style='margin-top:4px'><b>未计入：</b>%s —— %s</div>"
                   % (_e(d.get("label")),
                      _e(d.get("exploratory_reason") or "探索性指标")))
    if comp.get("note"):
        txt.append("<div style='margin-top:4px'>%s</div>" % _e(comp["note"]))
    if comp.get("reason") and v is None:
        txt.append("<div style='margin-top:4px'><b>未出分原因：</b>%s</div>"
                   % _e(comp["reason"]))

    return ('<div class="sec"><h2>综合评分</h2><div class="hero">'
            '<div class="big">%s</div><div class="txt">%s</div>'
            '</div></div>' % (big, "".join(txt)))


def _sec_conclusion(snap):
    """
    核心结论（规则驱动的 narrate 层）。这一段与 AI 解读是两回事：
    narrate 不走网络、始终可用；AI 解读要调模型、可能缺失。
    两者都要收进报告，且必须分开标注来源 —— 混在一起会让读者
    以为规则结论也是模型生成的。
    """
    p = (snap or {}).get("portrait") or {}
    nar = p.get("narrative") or p.get("conclusion") or {}
    if isinstance(nar, str):
        nar = {"text": nar}
    txt = (nar.get("text") or nar.get("summary")
           if isinstance(nar, dict) else None)
    pts = (nar.get("points") or nar.get("lines") or []) \
        if isinstance(nar, dict) else []

    body = []
    if txt:
        for para in str(txt).split("\n"):
            para = para.strip()
            if para:
                body.append("<p style='margin:0 0 6px'>%s</p>" % _e(para))
    if pts:
        body.append("<ul style='margin:4px 0 0 18px;padding:0'>%s</ul>"
                    % "".join("<li style='margin:2px 0'>%s</li>" % _e(x)
                              for x in pts))
    if not body:
        return ""
    return ('<div class="sec"><h2>核心结论'
            '<small>由测量数据按既定规则生成，不含模型推断</small></h2>'
            '<div class="note" style="font-size:11.5px">%s</div></div>'
            % "".join(body))


def _sec_ai(snap):
    """
    AI 综合解读全文。页面上它挤在小框里滚动，报告里完整铺开。

    字段口径以 llm_client.interpret() 的真实返回为准：
    summary / points / caveat / model / elapsed_ms / measured。
    （曾误按 text 字段取值 —— 那样这一栏会永远空白。）
    """
    ai = (snap or {}).get("ai_summary") or {}
    if isinstance(ai, str):
        ai = {"summary": ai}
    if not isinstance(ai, dict):
        ai = {}
    summary = ai.get("summary") or ai.get("text")
    points = ai.get("points") or []
    caveat = ai.get("caveat")

    if not summary and not points:
        reason = ai.get("reason") or ai.get("unavailable_reason")
        return ('<div class="sec"><h2>AI 综合解读</h2>'
                '<div class="note" style="color:#7A4E00">'
                '本次报告未包含 AI 综合解读%s。'
                '此处如实留空，不以占位文字代替。'
                '<br>如需解读，请在评估页面生成后重新打开本报告。</div></div>'
                % (("：" + _e(reason)) if reason else "（本次测评尚未生成）"))

    body = []
    # 解读生成之后又补测了新指标：内容照常展示，但必须讲清它的覆盖范围，
    # 否则用户会以为这段结论已经涵盖了后补的数据。
    if ai.get("stale"):
        body.append(
            "<div style='margin:0 0 8px;padding:6px 9px;background:#FFF6E5;"
            "border-left:3px solid #E0A93B;color:#7A4E00'>"
            "<b>注意：</b>本段解读生成于 %s，其后本次测评又新增了测量数据。"
            "以下结论仅基于生成当时已采集的指标，未涵盖新增部分。"
            "如需与全部数据一致的解读，请回到评估页面重新生成。</div>"
            % _ts(ai.get("generated_at") or ai.get("cached_at")))
    if summary:
        for para in str(summary).split("\n"):
            para = para.strip()
            if para:
                body.append("<p style='margin:0 0 6px'>%s</p>" % _e(para))
    if points:
        body.append("<ul style='margin:4px 0 0 18px;padding:0'>%s</ul>"
                    % "".join("<li style='margin:2px 0'>%s</li>" % _e(x)
                              for x in points))
    if caveat:
        body.append("<div style='margin-top:7px;padding:6px 9px;"
                    "background:#FFF6E5;border-left:3px solid #E0A93B;"
                    "color:#7A4E00'><b>解读限制：</b>%s</div>" % _e(caveat))

    bits = []
    if ai.get("model"):
        bits.append("模型 %s" % _e(ai["model"]))
    if ai.get("measured") is not None:
        bits.append("已采集指标 %s 项" % _e(ai["measured"]))
    if ai.get("generated_at") or ai.get("cached_at"):
        bits.append("生成时间 %s"
                    % _ts(ai.get("generated_at") or ai.get("cached_at")))
    meta = ('<div style="margin-top:6px;font-size:10px;color:#6B7480">'
            '%s　本段由语言模型基于上方实测指标生成，'
            '仅对已采集项作出描述。</div>' % "　".join(bits)) if bits else ""

    return ('<div class="sec"><h2>AI 综合解读</h2>'
            '<div class="note" style="font-size:11.5px">%s%s</div></div>'
            % ("".join(body), meta))


def _sec_dims(snap):
    """五维评分表。拍板2：不做 terms/weights 拆解，只给结果与方向。"""
    p = (snap or {}).get("portrait") or {}
    dims = p.get("dimensions") or []
    if not dims:
        return ('<div class="sec"><h2>五维心理生理画像</h2>'
                '<div class="note">五维数据不可用：%s</div></div>'
                % _e(p.get("error") or "计分层未返回结果"))

    rows = []
    for i, d in enumerate(dims):
        v = _num(d.get("value"))
        hw = bool(d.get("higher_is_worse"))
        col = _score_color(v, hw)
        w = 0 if v is None else max(0.0, min(100.0, v))
        tags = []
        if hw:
            tags.append('<span class="tag" style="color:#B25E00">越低越好</span>')
        if d.get("exclude_from_composite"):
            tags.append('<span class="tag" style="color:#6B7480">'
                        '不计入综合分</span>')
        if d.get("exploratory"):
            tags.append('<span class="tag" style="color:#0E7C86">探索性</span>')
        # 缺失原因要写进报告 —— 这是「为什么这维是破折号」的唯一解释
        note = "；".join(str(x) for x in (d.get("missing") or []))
        rows.append(
            '<tr%s><td><b>%s</b></td><td class="n" style="color:%s;'
            'font-weight:700">%s</td><td style="width:32%%">'
            '<div class="bar"><i style="width:%.1f%%;background:%s"></i></div>'
            '</td><td>%s</td><td class="%s">%s</td></tr>'
            % (' class="zebra"' if i % 2 else "",
               _e(d.get("label")), col, _fmt(v, 0), w, col,
               " ".join(tags) or "—",
               "dash" if not note else "", _e(note or "—")))

    return ('<div class="sec"><h2>五维心理生理画像'
            '<small>0–100，分数越高越好（除标注「越低越好」者）</small></h2>'
            '<table class="t"><thead><tr><th>维度</th><th>得分</th>'
            '<th>相对位置</th><th>标注</th><th>数据缺失说明</th></tr></thead>'
            '<tbody>%s</tbody></table></div>' % "".join(rows))


def _kv_table(rows, head=("指标", "数值", "说明")):
    """通用「指标-数值-说明」表。rows: [(名, 值, 备注)]。"""
    tr = []
    for i, (k, v, note) in enumerate(rows):
        dash = (v == "—" or v is None)
        tr.append('<tr%s><td>%s</td><td class="n%s">%s</td><td class="%s">%s</td></tr>'
                  % (' class="zebra"' if i % 2 else "", _e(k),
                     " dash" if dash else "", _e(v),
                     "dash" if not note else "", _e(note or "—")))
    return ('<table class="t"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
            % ("".join("<th>%s</th>" % _e(h) for h in head), "".join(tr)))


def _sec_physio(snap):
    """心率/呼吸 + 情绪 + AU + 头姿视线。这一段是页面上「只显示结论」的重灾区。"""
    rd = (snap or {}).get("readiness") or {}
    # readiness["hr"] 正常是 dict（_norm_hr 的输出）。这里仍做类型防御：
    # 上游若哪天改成裸数值，不做防御会让整节 500 —— 报告是兜底产物，
    # 宁可这一栏显示「未测得」，也不能让用户拿不到报告。
    hr = rd.get("hr")
    if not isinstance(hr, dict):
        hr = {"heart_rate": hr} if isinstance(hr, (int, float)) else {}
    face = (snap or {}).get("face") or {}
    out = []

    # ---- 生理 ----
    rows = [
        ("心率", _fmt(hr.get("heart_rate"), 0, " bpm"),
         "rPPG 非接触测量" if _num(hr.get("heart_rate")) is not None
         else "未测得 —— 请正对摄像头并保持光照稳定"),
        ("呼吸率", _fmt(hr.get("respiration_rate"), 1, " 次/分"),
         "由 rPPG 波形推导" if _num(hr.get("respiration_rate")) is not None
         else "未测得"),
    ]
    out.append('<div class="sec"><h2>生理指标</h2>%s</div>' % _kv_table(rows))

    # ---- 情绪分布 + 稳定性（页面只有分布条，稳定性/切换/时长都没露）----
    dist = face.get("emo_distribution")
    if isinstance(dist, dict) and dist:
        tot = sum(x for x in (_num(v) or 0.0 for v in dist.values()) if x > 0)
        items = sorted(((k, _num(v) or 0.0) for k, v in dist.items()),
                       key=lambda kv: -kv[1])
        tr = []
        for i, (k, v) in enumerate(items):
            r = (v / tot) if tot > 0 else 0.0
            tr.append('<tr%s><td>%s</td><td class="n">%s</td>'
                      '<td style="width:40%%"><div class="bar">'
                      '<i style="width:%.1f%%;background:#0B5FA5"></i></div></td></tr>'
                      % (' class="zebra"' if i % 2 else "", _e(_emo_zh(k)),
                         "%.0f%%" % (r * 100), r * 100))
        emo_tbl = ('<table class="t"><thead><tr><th>情绪</th><th>占比</th>'
                   '<th>分布</th></tr></thead><tbody>%s</tbody></table>'
                   % "".join(tr))
    else:
        emo_tbl = '<div class="note">情绪分布数据缺失。</div>'

    dur = _num(face.get("emo_dominant_duration_sec"))
    win = _num(face.get("window_sec"))
    emo_rows = [
        ("情绪稳定性", _pct1(face.get("emo_stability")),
         "窗口内情绪一致程度，越高越稳定"),
        ("情绪切换次数", _fmt(face.get("emo_switches"), 0, " 次"),
         "采集窗口内主导情绪发生变化的次数"),
        ("主导情绪持续", _fmt(dur, 1, " 秒"),
         ("占窗口 %s" % _pct1((dur / win) if (dur is not None and win) else None))
         if dur is not None else "缺失"),
        ("采集窗口长度", _fmt(win, 0, " 秒"), "本次面部聚合的有效时长"),
    ]
    out.append('<div class="sec alt"><h2>面部情绪'
               '<small>页面仅显示分布，此处补充稳定性与动态指标</small></h2>'
               '<div class="cols"><div>%s</div><div>%s</div></div></div>'
               % (emo_tbl, _kv_table(emo_rows)))

    # ---- AU 完整向量（页面只显示最强项，这里给全 8 维）----
    au = face.get("au_intensity")
    if isinstance(au, dict) and au:
        items = sorted(((k, _num(v)) for k, v in au.items()),
                       key=lambda kv: -(kv[1] if kv[1] is not None else -1))
        tr = []
        for i, (k, v) in enumerate(items):
            w = 0.0 if v is None else max(0.0, min(100.0, v * 100.0))
            tr.append('<tr%s><td><b>%s</b></td><td>%s</td><td class="n">%s</td>'
                      '<td style="width:30%%"><div class="bar">'
                      '<i style="width:%.1f%%;background:#0E7C86"></i></div></td></tr>'
                      % (' class="zebra"' if i % 2 else "", _e(k),
                         _e(AU_ZH.get(str(k).upper(), "—")),
                         _fmt(v, 2), w))
        dom = face.get("au_dominant")
        au_tbl = ('<table class="t"><thead><tr><th>AU</th><th>解剖含义</th>'
                  '<th>强度</th><th>相对强度</th></tr></thead>'
                  '<tbody>%s</tbody></table>'
                  '<div class="note" style="margin-top:5px">最强项：<b>%s</b>。'
                  'AU 强度为快照瞬时值（非窗口聚合），仅表征采集时刻的'
                  '面部动作单元激活程度。</div>'
                  % ("".join(tr), _e(dom or "—")))
    else:
        au_tbl = '<div class="note">面部动作单元（AU）数据缺失。</div>'
    out.append('<div class="sec"><h2>面部动作单元 (AU)'
               '<small>页面仅显示最强项，此处给出完整向量</small></h2>%s</div>'
               % au_tbl)

    # ---- 头姿与视线 ----
    pd = _num(face.get("pose_deviation_60s"))
    pose_rows = [
        ("注视稳定性", _pct1(face.get("gaze_stability")),
         "窗口内注视方向的集中程度"),
        ("头姿偏移占比", _pct1(pd),
         "窗口内视线状态含「偏移」的帧占比，越低越稳定"),
        ("头部偏转 Yaw", _fmt(face.get("pose_yaw"), 1, "°"), "左右转头，已零点校准"),
        ("头部俯仰 Pitch", _fmt(face.get("pose_pitch"), 1, "°"), "上下点头，已零点校准"),
        ("注视状态", _e(face.get("gaze_state") or "—"), "采集时刻的视线判定"),
        ("检测置信度", _fmt(face.get("confidence"), 2),
         "面部检测质量，仅供诊断，不参与计分"),
    ]
    out.append('<div class="sec alt"><h2>头姿与视线</h2>%s</div>'
               % _kv_table(pose_rows))
    return "".join(out)


def _sec_voice(snap):
    """语音分析。页面只露了几个数，这里把三组指标 + 可信度全铺开。"""
    v = (snap or {}).get("voice") or {}
    if not v or v.get("error"):
        return ('<div class="sec"><h2>语音分析</h2><div class="note">'
                '语音数据不可用：%s</div></div>'
                % _e(v.get("error") if isinstance(v, dict) else "未采集"))

    src = v.get("sources") or {}
    vq = v.get("voice_quality") or {}
    rh = v.get("rhythm") or {}
    pr = v.get("prosody") or {}
    em = v.get("emotion") or {}
    out = []

    def _rel(d):
        """可信标记。不可信必须写原因 —— 只标红不给原因等于没说。"""
        r = d.get("reliable")
        if r is None:
            return "—"
        if r:
            return "可信"
        rs = d.get("reasons")
        if isinstance(rs, (list, tuple)):
            rs = "；".join(str(x) for x in rs)
        return "不可信（%s）" % (rs or "未说明原因")

    out.append('<div class="sec"><h2>语音采集来源</h2>%s</div>' % _kv_table([
        ("持续元音段 /a/", "已采集" if src.get("vowel_present") else "未采集",
         "嗓音质量指标的唯一来源"),
        ("固定文本朗读段", "已采集" if src.get("reading_present") else "未采集",
         "节奏、语速、音高、能量、语音情绪的来源"),
    ], head=("采集任务", "状态", "用途")))

    out.append('<div class="sec alt"><h2>嗓音质量'
               '<small>来源：持续元音段</small></h2>%s</div>' % _kv_table([
        ("基频扰动 Jitter", _fmt(vq.get("jitter_local"), 4),
         "声带振动周期的微小不规则性"),
        ("振幅扰动 Shimmer", _fmt(vq.get("shimmer_local"), 4),
         "声波振幅的微小不规则性"),
        ("谐噪比 HNR", _fmt(vq.get("hnr_db"), 2, " dB"),
         "谐波与噪声能量之比，越高嗓音越清亮"),
        ("整体可信度", _rel(vq),
         "HNR ≥ 15 dB 才判定 Jitter/Shimmer 可信"),
    ]))
    out.append('<div class="note" style="margin-top:4px;color:#7A4E00">'
               '说明：Jitter / Shimmer <b>不参与</b>压力值计分。常规环境难以'
               '稳定达到 HNR ≥ 15 dB 的可信门槛，时而纳入时而不纳入会使分数'
               '无法纵向比较，故已按既定结论永久排除，压力值改由自评量表主导。'
               '</div>')

    out.append('<div class="sec"><h2>言语节奏与流畅度'
               '<small>来源：固定文本朗读段</small></h2>%s</div>' % _kv_table([
        ("语速", _fmt(rh.get("speech_rate_cpm"), 1, " 字/分"),
         "按实际发声用时计算"),
        ("发声时长", _fmt(rh.get("speech_sec"), 2, " 秒"),
         "连续发声 ≥ 120 ms 判定为有效发声段"),
        ("发声占比", _pct1(rh.get("speech_ratio")), "发声时长 / 录音总时长"),
        ("停顿次数", _fmt(rh.get("pause_count"), 0, " 次"), "语句间停顿"),
        ("停顿占比", _pct1(rh.get("pause_ratio")), "停顿总时长 / 录音总时长"),
        ("整体可信度", _rel(rh), ""),
    ]))

    out.append('<div class="sec alt"><h2>音高与能量'
               '<small>来源：固定文本朗读段</small></h2>%s</div>' % _kv_table([
        ("基频均值 F0", _fmt(pr.get("f0_mean"), 1, " Hz"), "平均音高"),
        ("基频半音标准差", _fmt(pr.get("f0_semitone_std"), 2, " 半音"),
         "音高起伏程度，活力值主项"),
        ("能量变异系数 RMS", _fmt(pr.get("rms_variation"), 3),
         "音量起伏程度，天然与录音增益无关"),
        ("响度均值", _fmt(pr.get("loudness_db_mean"), 2, " dB"),
         "【不参与计分】峰值归一化后该值与实际音量无关"),
    ]))
    if pr.get("comparability_note"):
        out.append('<div class="note" style="margin-top:4px">跨用户可比性：%s。'
                   '</div>' % _e(pr["comparability_note"]))

    # ---- 语音情绪 ----
    ed = em.get("distribution")
    if isinstance(ed, dict) and ed:
        tot = sum(x for x in (_num(x) or 0.0 for x in ed.values()) if x > 0)
        items = sorted(((k, _num(x) or 0.0) for k, x in ed.items()),
                       key=lambda kv: -kv[1])
        tr = []
        for i, (k, x) in enumerate(items):
            r = (x / tot) if tot > 0 else 0.0
            tr.append('<tr%s><td>%s</td><td class="n">%s</td>'
                      '<td style="width:40%%"><div class="bar">'
                      '<i style="width:%.1f%%;background:#7A4EA8"></i></div>'
                      '</td></tr>'
                      % (' class="zebra"' if i % 2 else "", _e(_emo_zh(k)),
                         "%.0f%%" % (r * 100), r * 100))
        etbl = ('<table class="t"><thead><tr><th>情绪</th><th>占比</th>'
                '<th>分布</th></tr></thead><tbody>%s</tbody></table>'
                % "".join(tr))
    else:
        etbl = '<div class="note">语音情绪分布缺失。</div>'
    out.append('<div class="sec"><h2>语音情绪<small>来源：固定文本朗读段'
               '</small></h2><div class="cols"><div>%s</div><div>%s</div>'
               '</div></div>' % (etbl, _kv_table([
                   ("判定情绪", _e(_emo_zh(em.get("label")) if em.get("label")
                                   else "—"), "模型输出的最可能情绪"),
                   ("置信度", _fmt(em.get("confidence"), 2), ""),
                   ("可信度", _rel(em), ""),
               ])))
    return "".join(out)


def _sec_dass(snap):
    """DASS-21：三分量表汇总 + 逐题作答（拍板3）。"""
    sc = (snap or {}).get("scale") or {}
    scored = sc.get("scored") or {}
    subs = scored.get("subscales") or {}
    out = []

    if not subs:
        return ('<div class="sec"><h2>心理量表评估 (DASS-21)</h2>'
                '<div class="note">量表未完成，无计分结果。</div></div>')

    # ---- 三分量表 ----
    tr = []
    order = [("D", "抑郁"), ("A", "焦虑"), ("S", "压力")]
    for i, (k, zh) in enumerate(order):
        s = subs.get(k) or {}
        raw, score = _num(s.get("raw")), _num(s.get("score"))
        lv = s.get("level")
        pct = _num(s.get("pct"))
        w = 0.0 if pct is None else max(0.0, min(100.0, pct))
        col = _level_color(lv)
        pill = ('<span class="pill" style="background:%s">%s</span>'
                % (col, _e(lv))) if lv else '<span class="dash">—</span>'
        miss = s.get("missing") or []
        tr.append('<tr%s><td><b>%s</b></td><td class="n">%s</td>'
                  '<td class="n">%s</td><td>%s</td>'
                  '<td style="width:26%%"><div class="bar">'
                  '<i style="width:%.1f%%;background:%s"></i></div></td>'
                  '<td class="%s">%s</td></tr>'
                  % (' class="zebra"' if i % 2 else "",
                     _e(s.get("label") or zh),
                     _fmt(raw, 0), _fmt(score, 0), pill, w, col,
                     "dash" if not miss else "",
                     _e("缺第 %s 题" % "、".join(str(x) for x in miss)
                        if miss else "—")))

    out.append('<div class="sec"><h2>心理量表评估 (DASS-21)'
               '<small>原始分 ×2 后对照 DASS-42 临床切点分级</small></h2>'
               '<table class="t"><thead><tr><th>分量表</th><th>原始分</th>'
               '<th>标准分</th><th>严重程度</th><th>占满分比例</th>'
               '<th>备注</th></tr></thead><tbody>%s</tbody></table>'
               '<div class="note" style="margin-top:5px">'
               '「占满分比例」仅用于绘制条形，<b>非常模百分位</b>，且因三个'
               '分量表临床切点不同而<b>不可跨分量表比较严重度</b>。'
               '严重程度分级才是可比的口径。</div></div>' % "".join(tr))

    # ---- 逐题作答（拍板3：进报告）----
    answers = sc.get("answers") or {}
    if answers:
        rows = []
        for n in range(1, 22):
            dim, text = DASS_ITEM_TEXTS.get(n, ("", "（题干缺失）"))
            a = answers.get(n, answers.get(str(n)))
            av = _num(a)
            lbl = DASS_CHOICE_LABELS.get(int(av), str(a)) if av is not None else "未作答"
            zh = {"D": "抑郁", "A": "焦虑", "S": "压力"}.get(dim, dim)
            col = "#B3261E" if (av is not None and av >= 2) else "#3C4450"
            rows.append('<tr%s><td class="n">%d</td><td class="q">%s</td>'
                        '<td>%s</td><td class="ans" style="color:%s">%s</td>'
                        '<td class="n">%s</td></tr>'
                        % (' class="zebra"' if n % 2 == 0 else "", n,
                           _e(text), _e(zh), col, _e(lbl),
                           _fmt(av, 0) if av is not None else "—"))
        out.append('<div class="sec alt pb"><h2>DASS-21 逐题作答明细</h2>'
                   '<div class="privacy">⚠ 本节包含受测者的逐题自评作答，'
                   '属敏感个人信息。请仅在获得受测者同意的前提下留存、传阅'
                   '或打印本页。</div>'
                   '<table class="t qa"><thead><tr><th>题号</th><th>题目</th>'
                   '<th>计入</th><th>作答</th><th>计分</th></tr></thead>'
                   '<tbody>%s</tbody></table>'
                   '<div class="note" style="margin-top:5px">'
                   '作答选项：不符合 = 0，有时 = 1，经常 = 2，总是 = 3。'
                   '评估时间范围为「最近一周」。'
                   '标红者为该题得分 ≥ 2（经常 / 总是）。</div></div>'
                   % "".join(rows))
    else:
        out.append('<div class="sec alt"><h2>DASS-21 逐题作答明细</h2>'
                   '<div class="note">未取得逐题作答数据（仅有汇总计分）。'
                   '</div></div>')
    return "".join(out)


def _sec_appendix(snap):
    """方法学附录 + 免责声明。"""
    rd = (snap or {}).get("readiness") or {}
    face = (snap or {}).get("face") or {}
    p = (snap or {}).get("portrait") or {}

    items = [
        ("面部采集时间", _ts(face.get("captured_at")), ""),
        ("面部聚合窗口", _fmt(face.get("window_sec"), 0, " 秒"),
         "低于 20 秒则注视稳定性无法统计"),
        ("量表提交时间", _ts((snap or {}).get("scale", {}).get("submitted_at")
                             if isinstance((snap or {}).get("scale"), dict)
                             else None), ""),
        ("公式版本", _e(rd.get("formula_status") or "—"), ""),
    ]
    demo = face.get("is_demo")
    if demo:
        items.append(("数据来源", "演示模式",
                      "⚠ 本次面部数据来自演示模式，非真实采集"))

    note = ['<div class="note"><b>指标口径与已知限制</b><ul>']
    note.append("<li>综合评分由四个维度构成；<b>活力值为探索性指标，"
                "不计入综合分</b> —— 中性朗读任务本身不诱发唤醒，"
                "该维度先天缺信号。</li>")
    note.append("<li>缺项时<b>不做权重重分配</b>。重分配会使同一个人在不同"
                "完成度下得到不同分数，纵向比较失去意义。</li>")
    note.append("<li>Jitter / Shimmer 已从压力值永久排除（可信门槛"
                "HNR ≥ 15 dB 在常规环境难以稳定达到）。</li>")
    note.append("<li>响度均值不参与任何计分（峰值归一化后该量与实际音量"
                "无关）。</li>")
    note.append("<li>AU 强度为快照瞬时值，非窗口聚合量。</li>")
    note.append("<li>专注度不使用 attention_score（该值被恒定的"
                "眨眼/闭眼比例污染）。</li>")
    note.append("<li>本报告为<b>单次测评</b>。系统当前不持久化历史测评，"
                "故不提供纵向趋势对比。</li>")
    note.append("</ul></div>")

    disc = ('<div class="disc"><b>免责声明</b>　'
            '本报告由 HiKO 数字生理感知系统基于非接触视觉、语音与自评量表'
            '自动生成，用于<b>一般性心理与生理状态的参考性评估</b>，'
            '<b>不构成医学诊断、治疗建议或任何临床结论</b>，亦不可作为'
            '医疗、司法、人事或保险决策的依据。DASS-21 为筛查性自评工具，'
            '其结果反映最近一周的自我感受，不等同于精神障碍的诊断。'
            '若您感到持续的情绪困扰或身体不适，请及时咨询精神科医师、'
            '临床心理师或其他合格的专业人员。')
    if not rd.get("ready"):
        disc += ('<br><br><b>特别提示：</b>本次数据采集<b>并不完备</b>，'
                 '报告中多项指标缺失，其参考价值显著低于完整测评。')
    disc += "</div>"

    return ('<div class="sec"><h2>方法学附录</h2>%s%s%s</div>'
            % (_kv_table(items, head=("项目", "内容", "说明")),
               "".join(note), disc))


# ---------------------------------------------------------------- 主入口

# 工具条。三个动作：打印/另存 PDF、返回、刷新。
# window.print() 在所有主流浏览器里都会打开原生打印预览，
# 用户在该预览里既能看（浏览），也能「目标: 另存为 PDF」（下载），
# 也能直接选打印机（打印）—— 一个入口同时满足用户的三项要求。
TOOLBAR_HTML = """
<div class="toolbar" id="rptBar">
  <b>最终报告单</b>
  <span class="hint">在打印预览中选择「目标 / 目的地 → 另存为 PDF」即可下载</span>
  <span class="sp"></span>
  <button class="primary" onclick="window.print()">打印 / 另存为 PDF</button>
  <button onclick="location.reload()">刷新数据</button>
  <button onclick="window.close()">关闭</button>
</div>
"""


def render_report_html(snap, session_id=None, now=None):
    """
    渲染完整报告 HTML。
    只读 snap，不写任何状态；任一区块抛错都不应让整页 500 ——
    故每块单独兜异常，坏掉的块显示错误提示，其余照常输出。
    一份报告缺一块仍然有用，整页 500 就完全没用了。
    """
    now = now if now is not None else time.time()
    rd = (snap or {}).get("readiness") or {}
    ready = bool(rd.get("ready"))

    def _safe(fn, title):
        try:
            return fn(snap)
        except Exception as e:                          # pragma: no cover
            return ('<div class="sec"><h2>%s</h2><div class="note" '
                    'style="color:#B3261E">本节渲染失败：%s</div></div>'
                    % (_e(title), _e(str(e)[:200])))

    head = (
        '<div class="rpt-head">'
        '<div><h1>心理与生理状态综合评估报告</h1>'
        '<div class="sub">HiKO 数字生理感知系统　·　'
        'AI 生理感知与数字健康联合实验室</div></div>'
        '<div class="meta">报告生成　<b>%s</b><br>'
        '会话标识　<b>%s</b><br>数据完备性　<b style="color:%s">%s</b></div>'
        '</div>'
        % (_ts(now), _e((session_id or "—")[:16]),
           "#1B5E33" if ready else "#B25E00",
           "完整" if ready else "不完整")
    )

    body = "".join([
        head,
        _safe(_sec_banner, "数据完备性"),
        _safe(_sec_hero, "综合评分"),
        _safe(_sec_conclusion, "核心结论"),
        _safe(_sec_ai, "AI 综合解读"),
        _safe(_sec_dims, "五维心理生理画像"),
        '<div class="pb"></div>',
        _safe(_sec_physio, "生理与面部行为"),
        '<div class="pb"></div>',
        _safe(_sec_voice, "语音分析"),
        '<div class="pb"></div>',
        _safe(_sec_dass, "心理量表评估"),
        _safe(_sec_appendix, "方法学附录"),
        '<div class="foot"><span>HiKO 数字生理感知系统 · 自动生成</span>'
        '<span class="sp"></span><span>本报告不构成医学诊断</span></div>',
    ])

    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head>"
            "<meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,"
            "initial-scale=1\">"
            "<title>心理与生理状态综合评估报告 %s</title>"
            "<style>%s</style></head><body>%s"
            "<div class=\"sheet\">%s</div></body></html>"
            % (time.strftime("%Y-%m-%d", time.localtime(now)),
               REPORT_CSS, TOOLBAR_HTML, body))


def register_routes(app, get_session, snapshot_fn, jsonify=None, request=None,
                    ai_peek_fn=None):
    """
    挂载报告路由。

      GET /portrait/report        报告单 HTML（浏览器内可打印 / 另存为 PDF）
      GET /portrait/report.json   报告所用数据（调试用，等价于 snapshot）

    snapshot_fn(sess) -> dict：由调用方注入，本模块不直接依赖 portrait_state，
    避免循环 import。

    ai_peek_fn(sess, snap) -> dict|None：只读地取【已生成】的 AI 解读，
    不触发模型调用。传 None 则报告不含 AI 解读栏（如实留白）。
    绝不在这里直接调 interpret() —— 那会让「打开报告」变成一次付费的
    模型调用，且刷新即重复扣费。

    之所以把 sess 也交给它：解读的权威副本固化在会话上（不受 LLM
    结果缓存 5 分钟 TTL 影响），只给 snap 就取不到那份，报告会在
    解读生成 5 分钟后重新变成留白。
    """
    if jsonify is None or request is None:
        from flask import jsonify as _j, request as _r
        jsonify, request = _j, _r

    def _sess():
        sid = request.cookies.get("session_id")
        return sid, get_session(sid)

    def _snap_with_ai(sess):
        snap = snapshot_fn(sess)
        if ai_peek_fn is not None and isinstance(snap, dict):
            try:
                ai = ai_peek_fn(sess, snap)
                if ai:
                    snap["ai_summary"] = ai
            except Exception:
                # AI 解读是附加内容，取不到就留白，绝不因此让报告 500
                pass
        return snap

    @app.route("/portrait/report", methods=["GET"])
    def portrait_report():
        sid, sess = _sess()
        if not sess:
            # 报告是给人看的页面，不是 API：返回可读的 HTML 而不是 JSON，
            # 否则用户在浏览器里只会看到一行裸 JSON，不知道该干什么。
            return ("<!DOCTYPE html><html lang=\"zh-CN\"><head>"
                    "<meta charset=\"utf-8\"><title>会话无效</title>"
                    "<style>body{font-family:sans-serif;padding:40px;"
                    "color:#14181F}h1{font-size:18px}a{color:#0B5FA5}"
                    "</style></head><body><h1>会话已失效，无法生成报告</h1>"
                    "<p>报告依赖本次测评的会话数据。请返回评估页面，"
                    "重新完成采集后再生成报告。</p>"
                    "<p><a href=\"/max\">← 返回评估页面</a></p>"
                    "</body></html>", 400,
                    {"Content-Type": "text/html; charset=utf-8"})
        try:
            snap = _snap_with_ai(sess)
        except Exception as e:
            snap = {"readiness": {"ready": False, "steps": [],
                                  "hr_available": False},
                    "portrait": {"error": "快照读取失败: %s" % str(e)[:160],
                                 "dimensions": [], "composite": {"value": None}}}
        html_out = render_report_html(snap, session_id=sid)
        return (html_out, 200,
                {"Content-Type": "text/html; charset=utf-8",
                 # 报告必须反映「当下」数据，缓存会让用户看到旧报告
                 "Cache-Control": "no-store, max-age=0"})

    @app.route("/portrait/report.json", methods=["GET"])
    def portrait_report_json():
        sid, sess = _sess()
        if not sess:
            return jsonify({"status": "error", "message": "Invalid session"}), 400
        return jsonify({"status": "ok", "data": _snap_with_ai(sess)})

    return app
