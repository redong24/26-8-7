#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_panels.py —— shell_panels.js 的针对性结构与数据校验

jscheck.py 只做括号/引号配对，抓不到两类会真实炸掉页面的问题：

【1】对象字面量属性间漏逗号
    render: () => `...`        <-- 少了逗号
    mount: (root) => {...}
    括号完全平衡，但这是 SyntaxError。本次改动正是在 render 之后插入
    mount，是最可能踩的坑。检查方式：每个属性键前面最近的有效字符
    必须是 '{' 或 ','。

【2】DASS-21 数据自身不一致
    shell_panels.js 顶部的 verifyDASS21() 是**模块级立即执行**的，
    一旦 throw，整个文件加载失败 —— 不是只坏掉量表卡片，而是 5 个面板
    全部打不开。所以这个自检必须在部署前于**离线**先跑一遍通过，
    不能指望到浏览器里才发现。这里用 Python 重新实现同一套断言，
    数据从 JS 源码里正则抽取，独立复算一遍。

【3】渲染模板与 mount 脚本之间的 data-* 契约
    mount() 靠 querySelector('[data-xxx]') 找元素；如果 render 里的
    属性名拼错，mount 会静默什么都不做（我特意写了 if(!listEl) return，
    这让它更安静）。所以两侧的钩子名必须逐一对上。
"""
import re
import sys
from collections import Counter

PATH = sys.argv[1] if len(sys.argv) > 1 else "/home/lsz/webapp/static/shell_panels.js"
src = open(PATH, encoding="utf-8").read()
lines = src.split("\n")
fails, checks = [], []


def ok(msg):
    checks.append("  ✅ " + msg)


def bad(msg):
    fails.append(msg)
    checks.append("  ❌ " + msg)


# ---------------------------------------------------------------- 去注释/字符串
def strip_noncode(s):
    """把注释、字符串、模板串内容替换成等长空白，保留换行以便定位行号"""
    out = []
    i, n = 0, len(s)
    tpl_depth = 0
    while i < n:
        c = s[i]
        if tpl_depth > 0:
            if c == "\\":
                out.append("  "); i += 2; continue
            if c == "`":
                tpl_depth -= 1; out.append(" "); i += 1; continue
            if c == "$" and i + 1 < n and s[i + 1] == "{":
                # 插值内部是代码，但为了本脚本目的（找顶层属性键）直接抹掉
                depth, j = 1, i + 2
                while j < n and depth:
                    if s[j] == "{":
                        depth += 1
                    elif s[j] == "}":
                        depth -= 1
                    elif s[j] == "`":
                        pass
                    j += 1
                seg = s[i:j]
                out.append(re.sub(r"[^\n]", " ", seg))
                i = j
                continue
            out.append("\n" if c == "\n" else " "); i += 1; continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i); j = n if j < 0 else j
            out.append(" " * (j - i)); i = j; continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2); j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", s[i:j])); i = j; continue
        if c in "'\"":
            q, j = c, i + 1
            while j < n and s[j] != q and s[j] != "\n":
                j += 2 if s[j] == "\\" else 1
            out.append(" " * (min(j, n - 1) - i + 1)); i = min(j, n - 1) + 1; continue
        if c == "`":
            tpl_depth += 1; out.append(" "); i += 1; continue
        out.append(c); i += 1
    return "".join(out)


code = strip_noncode(src)

# ---------------------------------------------------------------- 【1】漏逗号
PROP_RE = re.compile(r"^(\s*)(render|mount|icon|title|sub)\s*:", re.M)
comma_problems = 0
for m in PROP_RE.finditer(code):
    start = m.start(2)
    k = start - 1
    while k >= 0 and code[k] in " \t\r\n":
        k -= 1
    prev = code[k] if k >= 0 else ""
    ln = code[:start].count("\n") + 1
    if prev not in "{,":
        comma_problems += 1
        bad(f"{PATH}:{ln}: 属性 '{m.group(2)}:' 前的有效字符是 '{prev}'，"
            f"应为 '{{' 或 ','（对象属性间漏逗号 → SyntaxError）")
if not comma_problems:
    n_props = len(PROP_RE.findall(code))
    ok(f"对象属性分隔正确（检查 {n_props} 个 render/mount/icon/title/sub 键，前置字符均为 '{{' 或 ','）")

# ---------------------------------------------------------------- 面板与 mount
# 面板键写在第 0 列（psy: { / bio: { ...），不是 2 空格缩进；
# 第一版用 "^  " 匹配，结果把 groups/cutoffs/answers 当成了面板，
# 连带下面的 psy<mount<bio 位置检查被静默跳过（假通过）。
panel_keys = re.findall(r"^(\w+):\s*\{", code, re.M)
if len(panel_keys) != 5:
    bad(f"预期 5 个面板，实际检出 {len(panel_keys)} 个：{panel_keys}")
else:
    ok(f"检出 5 个面板：{', '.join(panel_keys)}")
n_render = len(re.findall(r"^    render:|^  render:", code, re.M))
n_mount = len(re.findall(r"^\s*mount:", code, re.M))
if n_mount != 1:
    bad(f"预期恰好 1 个 mount 钩子（psy 面板），实际 {n_mount} 个")
else:
    ok("mount 钩子数量 = 1（仅 psy 面板）")

# mount 必须在 psy 面板的花括号范围内
psy_m = re.search(r"^psy:\s*\{", code, re.M)
bio_m = re.search(r"^bio:\s*\{", code, re.M)
mnt_m = re.search(r"^\s*mount:", code, re.M)
if not (psy_m and bio_m and mnt_m):
    bad(f"定位失败：psy={bool(psy_m)} bio={bool(bio_m)} mount={bool(mnt_m)}"
        "（正则与源码结构不符，不能视为通过）")
else:
    if psy_m.start() < mnt_m.start() < bio_m.start():
        ok("mount 位于 psy 面板对象内（psy 起始 < mount < bio 起始）")
    else:
        bad("mount 不在 psy 面板对象内，位置异常")

# ---------------------------------------------------------------- 【2】DASS-21
def grab_group(key):
    m = re.search(key + r":\s*\{[^}]*items:\s*\[([0-9,\s]+)\]", src)
    if not m:
        return None
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


G = {k: grab_group(k) for k in ("D", "A", "S")}
if any(v is None for v in G.values()):
    bad(f"无法从源码抽取 groups 分组：{ {k: v for k, v in G.items()} }")
else:
    allitems = sorted(G["D"] + G["A"] + G["S"])
    if allitems != list(range(1, 22)):
        dup = [k for k, v in Counter(allitems).items() if v > 1]
        miss = [x for x in range(1, 22) if x not in allitems]
        bad(f"DASS-21 分组未恰好覆盖 1..21：重复={dup} 缺失={miss}")
    else:
        ok("DASS-21 三分量表恰好覆盖 1..21 各一次")
    for k, v in G.items():
        if len(v) != 7:
            bad(f"DASS-21 {k} 分量表题数应为 7，实为 {len(v)}")
    if all(len(v) == 7 for v in G.values()):
        ok("D/A/S 各 7 题：D=%s A=%s S=%s" % (G["D"], G["A"], G["S"]))

# items 里的 n / dim 与 groups 必须一致（对应 JS 里 verifyDASS21 的第二条断言）
items = re.findall(r"\{\s*n:\s*(\d+)\s*,\s*dim:\s*'([DAS])'\s*,\s*text:\s*'([^']*)'\s*\}", src)
if len(items) != 21:
    bad(f"抽取到 {len(items)} 条 items（预期 21）——正则与源码格式可能不符，需人工确认")
else:
    ok("items 共 21 条，且 n/dim/text 三字段格式规整")
    ns = sorted(int(a) for a, _, _ in items)
    if ns != list(range(1, 22)):
        bad(f"items 的 n 不是 1..21：{ns}")
    else:
        ok("items 的 n 覆盖 1..21")
    mism = [(int(a), b) for a, b, _ in items if G.get(b) and int(a) not in G[b]]
    if mism:
        bad(f"items 的 dim 与 groups 不一致：{mism}")
    else:
        ok("items.dim 与 groups.items 完全一致（冗余编码互相印证）")
    empty = [int(a) for a, _, t in items if not t.strip()]
    if empty:
        bad(f"题目文本为空：第 {empty} 题")
    else:
        ok("21 条题目文本均非空")

# 倍数与满分
mult = re.search(r"RAW_MULTIPLIER:\s*(\d+)", src)
smax = re.search(r"SCORE_MAX:\s*(\d+)", src)
if not mult or int(mult.group(1)) != 2:
    bad("RAW_MULTIPLIER 应为 2（DASS-21 是 DASS-42 的半长版，缺 ×2 则极重度永不可达）")
else:
    ok("RAW_MULTIPLIER = 2")
if not smax or int(smax.group(1)) != 42:
    bad(f"SCORE_MAX 应为 42（7题×3分×2），实为 {smax.group(1) if smax else '缺失'}")
else:
    ok("SCORE_MAX = 42 = 7 × 3 × 2，与 RAW_MULTIPLIER 自洽")

# 分级阈值单调递增，且与文档一致
DOC = {"D": [9, 13, 20, 27], "A": [7, 9, 14, 19], "S": [14, 18, 25, 33]}
for k, expect in DOC.items():
    m = re.search(k + r":\s*\[([^\]]*)\]", src[src.find("cutoffs"):])
    seg = re.search(r"cutoffs:\s*\{(.*?)\n  \}", src, re.S)
    if not seg:
        bad("未找到 cutoffs 段")
        break
    # 末行 (S) 既无尾逗号也无后续换行，故用 \] 收尾即可，不要求行尾换行
    mk = re.search(r"\b" + k + r":\s*\[(.*?)\]", seg.group(1), re.S)
    if not mk:
        bad(f"cutoffs 缺少 {k} 分量表")
        continue
    maxes = re.findall(r"max:\s*([0-9]+|Infinity)", mk.group(1))
    nums = [x for x in maxes if x != "Infinity"]
    got = [int(x) for x in nums]
    if got != expect:
        bad(f"cutoffs {k} 阈值 {got} 与量表文档 {expect} 不一致")
    elif maxes[-1] != "Infinity":
        bad(f"cutoffs {k} 最后一档 max 应为 Infinity（否则极重度落空返回 undefined）")
    else:
        ok(f"cutoffs {k} = {got} + Infinity，与量表文档一致")

# ---------------------------------------------------------------- 【3】data-* 契约
# render 侧：HTML 属性既可能带值 data-x="1"，也可能是裸属性 data-x>
# （裸属性是本文件的主要写法，第一版正则只认 '=' 导致 7 个钩子被误报为缺失）
render_hooks = set(re.findall(r'data-(dass-[a-z]+|est-[a-z]+)(?==|>|\s)', src))
# mount 侧：只统计出现在 CSS 选择器 [data-x] / [data-x="v"] 里的
query_hooks = set(re.findall(r'\[data-(dass-[a-z]+|est-[a-z]+)[=\]]', src))
only_q = sorted(query_hooks - render_hooks)
only_r = sorted(render_hooks - query_hooks)
if only_q:
    bad(f"mount 里查询了但 render 从未产出的钩子（会静默失效）：{only_q}")
else:
    ok(f"mount 查询的 {len(query_hooks)} 个 data-* 钩子在 render 中均有产出")
if only_r:
    checks.append(f"  ℹ️  render 产出但 mount 未查询（可能是预留）：{only_r}")

# 关键钩子必须存在
for need in ("dass-list", "dass-progress", "dass-scores", "dass-item", "dass-set",
             "dass-val", "dass-reset", "dass-note", "est-src", "est-row"):
    if need not in render_hooks:
        bad(f"render 缺少关键钩子 data-{need}")

# CSS 类名闭环：JS 会加的 tone 类必须在 CSS 里有定义
css_path = "/home/lsz/webapp/static/psy_v3.css"
try:
    css = open(css_path, encoding="utf-8").read()
    for cls in ("pv3-s-fill.green", "pv3-s-fill.amber", "pv3-s-fill.red", "pv3-s-fill.gray"):
        if cls not in css.replace(" ", ""):
            bad(f"CSS 缺少 .{cls}（JS 会加这个类，缺样式则进度条不可见）")
    for cls in (".lvl.red", ".lvl.gray", ".pv3-dass-list", ".pv3-dass-item",
                ".pv3-dass-opts", ".pv3-dass-scores", ".pv3-chip.done"):
        if cls not in css:
            bad(f"CSS 缺少 {cls}")
    ok("CSS 侧 tone 类（green/amber/red/gray）与 DASS 样式块齐备")
except FileNotFoundError:
    bad(f"找不到 {css_path}")

# shell.html 必须真的调用 mount
try:
    sh = open("/home/lsz/webapp/templates/shell.html", encoding="utf-8").read()
    if "cfg.mount" not in sh:
        bad("shell.html 的 buildPopup() 没有调用 cfg.mount()，mount 钩子永远不会执行")
    else:
        ok("shell.html buildPopup() 已调用 cfg.mount()")
    if "appendChild" in sh and sh.find("cfg.mount") < sh.find("popupContainer.appendChild"):
        bad("cfg.mount() 出现在 appendChild 之前，querySelector 可能拿不到已布局的节点")
    else:
        ok("cfg.mount() 在 appendChild 之后调用")
    if "catch" not in sh[sh.find("cfg.mount") - 200: sh.find("cfg.mount") + 400]:
        bad("cfg.mount() 调用未包 try/catch，面板脚本报错会导致弹窗打不开")
    else:
        ok("cfg.mount() 包在 try/catch 中，单面板出错不影响弹窗打开")
except FileNotFoundError:
    bad("找不到 shell.html")

print(f"=== check_panels.py：{PATH} ===")
for c in checks:
    print(c)
print()
if fails:
    print(f"❌ 共 {len(fails)} 项失败，禁止部署")
    sys.exit(1)
print("✅ 全部结构与数据检查通过")
