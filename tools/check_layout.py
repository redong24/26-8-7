#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_layout.py —— 校验"内部滚动"所依赖的 CSS 高度链

本次事故的根因：.psy-v3 用了 min-height:100% 而不是 height:100%。
min-height 只设下限、不设上限，容器会随内容无限增高；一旦祖先高度不确定，
后代的 flex:1 / min-height:0 / overflow-y:auto 这套内部滚动机制就整体失效
（flex 项目会退化为按内容撑开）。表现就是"页面被严重撑高变形"。

这类问题的特点是：CSS 语法完全正确、括号完全平衡、类名全部存在，
所有静态检查都通过，但**布局在运行时崩掉**。所以必须单独校验高度链。

检查项：
  1. 滚动容器（overflow-y:auto）必须同时有 min-height:0
     —— flex 项目默认 min-height:auto，不改成 0 则永不收缩，滚动条不出现
  2. 面板根容器必须锁定高度（height/max-height），不能只有 min-height
  3. 承载本面板的 .p-body 必须关掉自身滚动，避免"整页滚动"与"卡内滚动"打架
  4. 声明了 flex:N 的卡片，其父级必须是 flex 容器且 min-height:0
  5. overflow:hidden 的卡片内部，凡是会长内容的区域都应有滚动出口，
     否则内容会被静默裁切（比录音按钮消失更难发现）
"""
import re
import sys

CSS = sys.argv[1] if len(sys.argv) > 1 else "/home/lsz/webapp/static/psy_v3.css"
HTML = "/home/lsz/webapp/templates/shell.html"
raw = open(CSS, encoding="utf-8").read()


def strip_comments(text):
    """把 /* ... */ 替换成等长空白（换行保留），使行号不变。

    必须做这一步：本文件的注释量很大，而选择器正则里的 [^{]*? 会把
    规则前面的整段注释一起吞进"选择器"，导致报告里打印出中文注释散文
    而不是选择器名 —— 上一版就是这个 bug。注释里还可能出现
    height:100% / overflow-y:auto 这类词（正是本文件解释原因时写的），
    会造成假阳性/假阴性，所以必须先剥离再匹配。
    """
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join("\n" if c == "\n" else " " for c in text[i:j]))
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


src = strip_comments(raw)
assert len(src) == len(raw), "剥离注释后长度变化，行号会错位"


def iter_rules(text):
    """遍历所有 `选择器 { 声明 }`。

    声明块用 [^{}]* 限定，因此 @media{...} 这类嵌套块中的【内层规则】
    会被正确取出（外层 @media 的前导部分自然被跳过）。
    选择器也用 [^{}]+ 限定，保证不会跨过上一条规则的 `}` 去吞前文。
    """
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        sel = m.group(1).strip()
        # 只保留最后一行作为选择器（多行前导空白/上一条规则残留已被 } 截断）
        yield " ".join(sel.split()), m.group(2), text[:m.start()].count("\n") + 1


checks, fails = [], []
ok = lambda m: checks.append("  ✅ " + m)
def bad(m):
    fails.append(m); checks.append("  ❌ " + m)
warn = lambda m: checks.append("  ⚠️  " + m)


def rule_body(selector):
    """取某个选择器的声明块（原样，含换行）"""
    pat = re.escape(selector) + r"\s*\{(.*?)\}"
    m = re.search(pat, src, re.S)
    return m.group(1) if m else None


def has(body, prop_re):
    return body is not None and re.search(prop_re, body) is not None


# ---------------- 1. 根容器必须锁定高度 ----------------
root = rule_body(".psy-v3")
if root is None:
    bad(".psy-v3 规则缺失")
else:
    locked = has(root, r"(?<!min-)height\s*:\s*100%") or has(root, r"max-height\s*:\s*100%")
    only_min = has(root, r"min-height\s*:\s*100%")
    if only_min and not locked:
        bad(".psy-v3 只有 min-height:100%（仅下限，容器会随内容无限增高）"
            "→ 后代的内部滚动全部失效，面板被撑高。应改为 height:100%")
    elif locked:
        ok(".psy-v3 高度已锁定（height/max-height:100%），内部滚动机制可生效")
    else:
        warn(".psy-v3 未见 100% 高度声明，请人工确认高度来源")
    if has(root, r"display\s*:\s*flex"):
        ok(".psy-v3 是 flex 容器，子级可参与高度分配")
    else:
        bad(".psy-v3 不是 flex 容器，子级 flex:1 无效")

# ---------------- 2. .p-body 必须让出滚动权 ----------------
pb = rule_body(".p-body:has(> .psy-v3)")
if pb is None:
    bad("缺少 .p-body:has(> .psy-v3) 规则（无法关闭外壳的整页滚动）")
else:
    if has(pb, r"overflow\s*:\s*hidden"):
        ok(".p-body（承载本面板）已关闭整页滚动，改由卡片内部各自滚动")
    else:
        bad(".p-body:has(> .psy-v3) 未设 overflow:hidden —— "
            "外层整页滚动会与卡内滚动打架，用户滚动时整个面板一起动")
    if has(pb, r"min-height\s*:\s*0"):
        ok(".p-body 有 min-height:0，能正确收缩到弹窗可用高度")
    else:
        warn(".p-body 未设 min-height:0，作为 flex 项目可能不收缩")
# .p-body 自身在 shell.html 里是 flex:1 —— 高度链的源头
try:
    sh = open(HTML, encoding="utf-8").read()
    if re.search(r"\.p-body\{[^}]*flex\s*:\s*1", sh):
        ok(".p-body 在 shell.html 中为 flex:1（高度由 .popup 确定，高度链完整）")
    else:
        bad(".p-body 在 shell.html 中不是 flex:1，height:100% 无法解析")
    if re.search(r"\.popup\{[^}]*display\s*:\s*flex", sh, re.S):
        ok(".popup 是 flex 纵向容器，为 .p-body 提供确定高度")
    else:
        warn("未确认 .popup 的 flex 布局")
except FileNotFoundError:
    bad("找不到 shell.html")

# ---------------- 3. 所有滚动容器都要有 min-height:0 ----------------
# 走 iter_rules（注释已剥离、选择器不会跨规则吞前文），避免上一版
# "把注释当选择器打印" 的报告污染。
scrollers = []
for sel, body, ln in iter_rules(src):
    if ".psy-v3" not in sel:
        continue
    if not re.search(r"overflow-y\s*:\s*auto", body):
        continue
    scrollers.append((sel, ln))
    if not re.search(r"min-height\s*:\s*0", body):
        bad(f"L{ln} {sel} 是滚动容器但缺 min-height:0 —— "
            "flex 项目默认 min-height:auto，不会收缩，滚动条永不出现")
if scrollers:
    ok(f"检出 {len(scrollers)} 个滚动容器，均已配 min-height:0：\n       "
       + "\n       ".join(f"L{ln:<4} {s.replace('.psy-v3 ', '')}" for s, ln in scrollers))
else:
    bad("未检出任何滚动容器 —— 21 道题必然把面板撑高")

# ---------------- 4. 右栏两卡的高度分配 ----------------
dass = rule_body(".psy-v3 .pv3-col-in > .pv3-card.pv3-dass-card")
voice = rule_body(".psy-v3 .pv3-col-in > .pv3-card.pv3-voice-card")
if dass and voice:
    fd = re.search(r"flex\s*:\s*(\d+)", dass)
    fv = re.search(r"flex\s*:\s*(\d+)", voice)
    if fd and fv:
        a, b = int(fd.group(1)), int(fv.group(1))
        ok(f"右栏高度分配 量表卡:{a} / 语音卡:{b}（合计 {a+b}，量表卡占 "
           f"{a*100//(a+b)}%）")
        if a < b:
            warn(f"量表卡({a}) 小于语音卡({b})，21 道题的可视区可能过小")
    else:
        bad("右栏两卡未显式声明 flex 比例，会被通用规则 flex:1 均分")
    for nm, bd in (("量表卡", dass), ("语音卡", voice)):
        if not re.search(r"min-height\s*:\s*0", bd):
            bad(f"{nm} 缺 min-height:0")
else:
    bad(f"右栏卡片规则缺失：dass={bool(dass)} voice={bool(voice)}")

# 语音卡的 class 必须真的加到了 HTML 上，否则规则是死的
try:
    js = open("/home/lsz/webapp/static/shell_panels.js", encoding="utf-8").read()
    for cls in ("pv3-voice-card", "pv3-dass-card"):
        if f'pv3-card {cls}"' in js or f'{cls}"' in js:
            ok(f".{cls} 已实际标注在 DOM 上（CSS 规则生效）")
        else:
            bad(f".{cls} 在 CSS 里有规则，但 shell_panels.js 的 DOM 上没有这个类 "
                "→ 规则完全不生效（静默失效，最难发现）")
except FileNotFoundError:
    bad("找不到 shell_panels.js")

# ---------------- 5. overflow:hidden 的卡内需有滚动出口 ----------------
# 语音卡 hidden，其内部 prompt 应可滚动，否则录音按钮会被挤出
vp = rule_body(".psy-v3 .pv3-voice-prompt")
if vp is None:
    warn("未找到 .pv3-voice-prompt")
elif has(vp, r"overflow-y\s*:\s*auto"):
    ok(".pv3-voice-prompt 可滚动，长朗读文本不会把录音按钮挤出卡片")
else:
    bad(".pv3-voice-prompt 不可滚动，而语音卡是 overflow:hidden —— "
        "长文本会把波形和录音按钮静默裁掉，等于功能消失")
ab = rule_body(".psy-v3 .pv3-ai-body")
if ab and has(ab, r"overflow-y\s*:\s*auto"):
    ok(".pv3-ai-body 可滚动（LLM 生成文本长度不可控）")
elif ab:
    bad(".pv3-ai-body 不可滚动，AI 解读文本会挤压左栏其它卡片")

# ---------------- 6. 不该出现的写法 ----------------
if re.search(r"\.psy-v3[^{]*\{[^}]*height\s*:\s*\d{3,}px", src):
    warn("存在写死的三位数以上 px 高度，可能在不同屏高下错版")
# flex-shrink:0 逐条列出归属选择器（注释已剥离，不会把解释性文字算进去）。
# 判定标准：只要声明块里同时写死了小尺寸（width/height < 100px）或它是
# 装饰性元素（.dot/.bar/i/svg），拒绝压缩是安全的；反之需人工确认。
shrink = [(sel, ln, body) for sel, body, ln in iter_rules(src)
          if re.search(r"flex-shrink\s*:\s*0", body)]
if shrink:
    small = re.compile(r"(?:width|height)\s*:\s*\d{1,2}(?:\.\d+)?px")
    risky = [(s, l) for s, l, b in shrink if not small.search(b)]
    warn(f"共 {len(shrink)} 处 flex-shrink:0（拒绝压缩）；其中 "
         f"{len(shrink)-len(risky)} 处带 <100px 的固定尺寸，属安全的装饰/图标元素")
    if risky:
        warn("需人工确认的 flex-shrink:0（无小尺寸约束，可能挤走同级卡片）：\n       "
             + "\n       ".join(f"L{l:<4} {s.replace('.psy-v3 ', '')}" for s, l in risky))

print(f"=== check_layout.py：{CSS} ===")
for c in checks:
    print(c)
print()
if fails:
    print(f"❌ {len(fails)} 项失败，禁止部署")
    sys.exit(1)
print("✅ 高度链与内部滚动机制检查通过")
print("   注：静态规则检查，无法替代真实浏览器渲染验证。")
