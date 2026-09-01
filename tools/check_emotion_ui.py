#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪构成（B）与「编造数值占位化」（E）的契约校验。

为什么需要这个脚本
------------------
沙箱内没有 node / 浏览器 / 摄像头，无法运行时验证。而这两项改造的失败
模式恰好都是「静默地显示一个看起来合理的假数字」——页面不报错，用户
也无从察觉。因此必须用静态契约把关键约束钉住：

  B. 情绪构成必须来自 /get_openface 的 emo_distribution，且：
     - 8 类全展示（后端 EMO_LABELS 有 8 类，原前端只写死 6 类）
     - 顺序沿用后端固定顺序（后端注释明确：不排序，保证横条位置固定）
     - status=idle / error / 无人脸 时显示占位，绝不显示示例百分比
  E. 综合评分、核心结论、主导情绪、疲劳指数、AI 解读里的编造数字
     必须已被移除或占位化。

用法: python3 tools/check_emotion_ui.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []
PASS = []


def strip_comments(text):
    """去掉 /* */、//、<!-- --> 注释，保留长度与行数。

    面板标记是「JS 模板字符串里的 HTML」，改造说明里会引用被废弃的旧文案
    （例如「72%」「疲劳指数 41」）。若不剥离注释，这些引用会让检查误报
    ——这个坑在 check_voice_ui.py 上真实踩过一次。
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join("\n" if c == "\n" else " " for c in text[i:j]))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("<!--", i):
            j = text.find("-->", i + 4)
            j = n if j == -1 else j + 3
            out.append("".join("\n" if c == "\n" else " " for c in text[i:j]))
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  -> " + detail) if detail else ""))


def main():
    panels_raw = read("static/shell_panels.js")
    panels = strip_comments(panels_raw)
    css = read("static/psy_v3.css")
    emo_js_raw = read("static/emotion_view.js")
    emo_js = strip_comments(emo_js_raw)

    # 后端权威定义（openface_parser.EMO_LABELS，顺序即渲染顺序）
    LABELS = ["neutral", "happy", "sad", "surprise",
              "fear", "disgust", "angry", "contempt"]
    CN = ["平静", "愉快", "悲伤", "惊讶", "恐惧", "厌恶", "愤怒", "轻蔑"]

    print("\n=== 1. 数据来源：必须真的去打 /get_openface ===")
    check("1.1 请求 /get_openface", "/get_openface" in emo_js)
    check("1.2 消费 emo_distribution 字段", "emo_distribution" in emo_js)
    check("1.3 带 same-origin 凭证（接口依赖 session cookie）",
          "same-origin" in emo_js)
    # /get_openface 无会话时返回 400，必须显式处理，否则会走进 catch 并把
    # 「没开摄像头」误报成网络异常
    check("1.4 处理非 200（无会话返回 400）",
          re.search(r"\.ok\b|status\s*===?\s*400|res\.status", emo_js) is not None)

    print("\n=== 2. 八类情绪：不得沿用写死的 6 类 ===")
    for lbl in LABELS:
        check("2.1 含类别 " + lbl, ("'" + lbl + "'") in emo_js or ('"' + lbl + '"') in emo_js)
    for cn in ("厌恶", "轻蔑"):
        check("2.2 新增中文名 " + cn, cn in emo_js)
    # 后端 parse_emotion_distribution 明确「按固定顺序排列（不排序）」，
    # 前端若再按概率排序，横条位置会每帧跳动，与后端设计意图相反
    check("2.3 未按概率重排序（后端要求固定顺序）",
          not re.search(r"\.sort\s*\(", emo_js), "禁止 sort")

    print("\n=== 3. 占位语义：没有数据时绝不显示数字 ===")
    check("3.1 处理 status=idle", "idle" in emo_js)
    check("3.2 处理 status=error", "error" in emo_js)
    # face_count=0 时分布是上一帧残留或全 0，不能当成当前情绪展示
    check("3.3 处理无人脸 face_count", "face_count" in emo_js)
    check("3.4 有占位符 —", "—" in emo_js)
    # 这条曾写成「文件里出现 null 字样」，恒为真 —— 变异自检把它揪出来了
    # （把 return null 改成 return 0 仍然通过）。改为检查 pct() 的实际返回：
    # 概率缺失时必须回 null，绝不能回 0（0% 会被当成「概率为零」这个真实值）。
    m = re.search(r"function pct\s*\([^)]*\)\s*\{(.*?)\n  \}", emo_js, re.S)
    check("3.5 pct() 缺失值返回 null 而非 0",
          m is not None
          and re.search(r"undefined\s*\)\s*return\s+null", m.group(1)) is not None
          and not re.search(r"undefined\s*\)\s*return\s+0", m.group(1)),
          "缺失概率必须是占位符，不能是 0%")
    check("3.6 非有限值也返回 null",
          m is not None and re.search(r"isFinite\s*\([^)]*\)\s*\)\s*return\s+null",
                                      m.group(1)) is not None)

    # ---- 心理面板作用域 ----------------------------------------------
    # shell_panels.js 里有多个面板（心理/皮肤/生理…），其它面板同样含
    # 设计稿示例值，但本轮范围仅限心理模块。若用整文件匹配，会把皮肤
    # 面板的「色斑指数 79 / width:79%」误报成本项遗漏 —— 这类越界误报
    # 会诱导去改用户没要求改的地方。
    psy_start = panels.index('<div class="psy-v3">')
    psy_end = panels.index("data-dass-list")
    psy = panels[psy_start:psy_end]

    print("\n=== 4. B：情绪构成卡改为动态渲染 ===")
    check("4.1 标记含 data-emo-list 挂载点", "data-emo-list" in panels)
    check("4.2 移除写死的 72% 示例宽度",
          "width:72%" not in panels, "写死示例值")
    check("4.3 移除写死的 15%/6%/4%/2%/1% 示例行",
          not re.search(r"pv3-e-fill\s+\w+\"\s+style=\"width:\d+%", panels))
    check("4.4 EmotionView 已挂载", "EmotionView" in panels)
    check("4.5 挂载在 DASS early-return 之前",
          panels.index("EmotionView") < panels.index("[data-dass-list]"),
          "否则 DASS 结构缺失时会被静默跳过")
    check("4.6 独立 try/catch（与录音/DASS 互不牵连)",
          re.search(r"try\s*\{[^}]*EmotionView", panels, re.S) is not None)

    print("\n=== 5. B：主导情绪块同样接真实数据 ===")
    check("5.1 主导情绪有挂载点", "data-emo-dominant" in panels)
    check("5.2 移除写死的 01:24 持续时长", "01:24" not in panels)
    check("5.3 移除写死的置信度 77%", "77%" not in panels)
    check("5.4 消费 emo_dominant_duration_sec",
          "emo_dominant_duration_sec" in emo_js)

    print("\n=== 6. E：移除编造的结论性数字 ===")
    # 这些数字出现在「AI 综合解读」「核心结论」「综合评分」里，会被当成
    # 真实测量结论阅读。LLM 尚未接入，任何数字都是设计稿示例值。
    for bad, where in [("疲劳指数处于中等区间", "AI 解读"),
                       ("呼吸率略偏高", "AI 解读"),
                       ("4-7-8 呼吸放松法", "AI 解读"),
                       ("高于常模基线", "结论/AI"),
                       ("未观察到", "结论")]:
        check("6.1 移除编造表述「%s」(%s)" % (bad, where), bad not in psy)
    check("6.2 移除写死综合评分 81",
          not re.search(r'class="n">\s*81\s*<', psy))
    check("6.3 移除写死疲劳指数 41",
          not re.search(r'class="n">\s*41\s*<', psy))
    check("6.4 移除疲劳条写死宽度 41%", "width:41%" not in psy)
    check("6.5 AI 卡有占位挂载点", "data-ai-body" in panels)
    check("6.6 AI 卡说明未接入 LLM",
          re.search(r"未接入|待接入|尚未接入", panels) is not None)
    # CTA 按钮：LLM 未接入时点了不会有任何结果。一个「看着能点、点了
    # 没反应」的主按钮比灰掉更糟，因此必须 disabled 且 hover 不发光。
    check("6.7 生成报告 CTA 已 disabled",
          re.search(r'pv3-ai-cta\s+disabled', panels) is not None)
    check("6.8 CSS hover 排除 disabled",
          ".pv3-ai-cta:not(.disabled):hover" in css,
          "否则灰按钮悬停仍上浮发光")

    print("\n=== 6b. E：五维心理画像的示例分数同样是编造结论 ===")
    # 五维公式尚未定义（设计稿不存在），原 5 条分数 86/88/79/72/33 均为
    # 设计稿示例值；且配了「常模基线」对比条，而我们没有常模数据。
    # 作用域限定在心理面板（psy-v3 的 render 模板）内。
    # 整个 shell_panels.js 还含皮肤/生理等其它面板，它们同样有示例值，
    # 但不在本轮范围内（用户限定为心理模块），不能用全文件匹配 ——
    # 否则会把皮肤面板的「色斑指数 79 / width:79%」误报成本项遗漏。
    for bad in ("width:72%", "width:86%", "width:88%", "width:79%", "width:33%"):
        check("6b.1 心理面板内移除写死维度宽度 %s" % bad, bad not in psy)
    check("6b.2 移除常模基线条 pv3-p-norm",
          "pv3-p-norm" not in panels, "无常模数据，不得画基线")
    check("6b.3 图例不再标注「常模基线」",
          "常模基线" not in panels, "基线条已移除，图例须同步")
    check("6b.4 维度名与公式说明保留（C 项的输入依据）",
          all(k in panels for k in ("情绪稳定", "放松度", "专注度", "活力值", "压力值")))
    check("6b.5 维度分数占位", "data-portrait-list" in panels)

    print("\n=== 7. CSS：新增两类需有配色，且不得复用误导色 ===")
    for cls in ("disgust", "contempt"):
        check("7.1 .pv3-e-fill.%s 存在" % cls,
              ".pv3-e-fill." + cls in css)
    check("7.2 情绪占位行样式存在", "pv3-e-empty" in css)

    print("\n=== 8. 资源释放与轮询节流 ===")
    # 面板关闭后若继续轮询，会持续打后端并保持 session 活跃
    check("8.1 监听 psy-panel-close 停止轮询",
          "psy-panel-close" in emo_js)
    check("8.2 使用 clearInterval/clearTimeout 停止",
          re.search(r"clearInterval|clearTimeout", emo_js) is not None)
    # 参考 _preview 的经验：openface 轮询过密曾压垮微服务
    check("8.3 轮询间隔 >= 1000ms（避免压垮 5002）",
          re.search(r"(1000|1500|2000|2500|3000)\b", emo_js) is not None)
    # 同样是被自检揪出的无效检查：原来只查「出现 inFlight 字样」，
    # 把守卫判断删掉后变量声明仍在，检查照旧通过。
    # 改为要求 tick 入口真的用它做 early-return，并在请求发出前置位。
    check("8.4 tick() 入口用 inFlight 做 early-return",
          re.search(r"if\s*\(\s*this\.inFlight[^)]*\)\s*return", emo_js) is not None,
          "否则上一次未返回就发下一个请求，会堆积压垮微服务")
    check("8.5 请求前置位 inFlight=true",
          re.search(r"this\.inFlight\s*=\s*true", emo_js) is not None)
    # 注意作用域：构造函数里也有 this.inFlight = false（初始化），
    # 若全文件匹配则这条恒为真（自检已证实）。必须限定在 tick() 内部：
    # 复位漏在 tick 里 → 首次请求后 inFlight 永为 true → 轮询彻底停摆，
    # 页面停在「正在读取…」且不报错。
    tick = re.search(r"EmotionView\.prototype\.tick\s*=\s*function.*?\n  \};",
                     emo_js, re.S)
    check("8.6 tick() 内复位 inFlight=false",
          tick is not None
          and re.search(r"inFlight\s*=\s*false", tick.group(0)) is not None,
          "漏复位会导致轮询只跑一次就永久卡住")

    print("\n=== 9. 部署与语法 ===")
    deploy = read("deploy_psy_v3.sh")
    check("9.1 emotion_view.js 已加入部署清单",
          "static/emotion_view.js" in deploy,
          "否则新文件永远不会被部署")
    shell = read("templates/shell.html")
    check("9.2 shell.html 引入 emotion_view.js",
          "emotion_view.js" in shell)

    print("\n" + "=" * 68)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    print("=" * 68)
    if FAIL:
        for f in FAIL:
            print("  ❌ " + f)
        return 1
    print("✅ 情绪构成与占位化契约全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
