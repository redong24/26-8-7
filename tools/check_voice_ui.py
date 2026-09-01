#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_voice_ui.py —— 语音录音 UI 与后端契约的一致性检查

为什么需要这个检查
------------------
沙箱内没有浏览器，无法运行 voice_recorder.js；而这个模块最容易犯的错
恰好都是「静态可查」的契约类错误，一旦犯了在浏览器里表现为难定位的
静默失败：

  1) 用 FormData/multipart 上传 —— 后端是
     request.get_data(parse_form_data=False) 读裸 body，
     multipart 边界会被当成 WAV 内容，报错信息还指向 5003，极易误判。
  2) 采样率写死成与 task_spec.sample_rate 不一致的值（历史上卡片写的是
     16kHz，实际是 48000），后端按 48000 估时长做 413 前置拦截，
     会导致时长判断错位。
  3) 把 null 渲染成 0：后端对「没测到」明确返回 null
     （零识别时 cpm=null），若前端退化成 0，就把「未测量」
     伪装成了一个真实数据点，会进入均值与趋势。
  4) 忘记停 MediaStream track / 关 AudioContext，麦克风一直被占用。
  5) 朗读段做倒计时：与 duration_mode=until_user_done 契约相反。

用法：python3 tools/check_voice_ui.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "voice_recorder.js")
PANELS = os.path.join(ROOT, "static", "shell_panels.js")
CSS = os.path.join(ROOT, "static", "psy_v3.css")
HTML = os.path.join(ROOT, "templates", "shell.html")
CLIENT = os.path.join(ROOT, "audio_service", "audio_client.py")

fails, warns = [], []


def ok(msg):
    print(f"  ✅ {msg}")


def bad(msg, detail=""):
    fails.append(msg)
    print(f"  ❌ {msg}{('  ← ' + detail) if detail else ''}")


def warn(msg):
    warns.append(msg)
    print(f"  ⚠️  {msg}")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def strip_comments(text):
    """把 /* */、// 以及 <!-- --> 注释替换成等长空白，
    避免注释里的字样被当成代码/标记。行号保持不变（换行保留）。

    必须同时剥离 HTML 注释：面板结构是写在 JS 模板字符串里的 HTML，
    其中的 <!-- ... --> 改造说明会原文引用被废弃的文案
    （例如「不上传原始音频」），只剥 JS 注释会把这些说明误判成
    「代码里仍保留该文案」。这正是本检查器第一版的误报来源。
    """
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join("\n" if c == "\n" else " " for c in text[i:j]))
            i = j
        elif text.startswith("<!--", i):
            j = text.find("-->", i + 4)
            j = n if j == -1 else j + 3
            out.append("".join("\n" if c == "\n" else " " for c in text[i:j]))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


for p in (JS, PANELS, CSS, HTML, CLIENT):
    if not os.path.exists(p):
        print(f"❌ 缺少文件: {p}")
        sys.exit(1)

js_raw = read(JS)
js = strip_comments(js_raw)
panels = strip_comments(read(PANELS))
css = read(CSS)
html = strip_comments(read(HTML))
client = read(CLIENT)

print("=== 1. 上传契约：裸 body，不能用 multipart ===")
if re.search(r"\bnew\s+FormData\b", js):
    bad("使用了 FormData（后端读裸 body，multipart 会被当成 WAV 内容）")
else:
    ok("未使用 FormData")

if "application/octet-stream" in js:
    ok("Content-Type 为 application/octet-stream")
else:
    bad("未设置 Content-Type: application/octet-stream")

m = re.search(r"fetch\(\s*['\"]/audio/upload\?stage=", js)
if m:
    ok("上传地址带 ?stage= 参数")
else:
    bad("未找到 /audio/upload?stage= 的调用")

# 后端 upload 路由确认仍是裸 body 读法（若后端改成 multipart，本检查需同步改）
if "parse_form_data=False" in client:
    ok("后端仍以 get_data(parse_form_data=False) 读裸 body（契约未变）")
else:
    warn("后端读取方式似有变化，请复核前端上传方式是否仍匹配")

print()
print("=== 2. 采样率必须取自 task_spec，不能写死 ===")
if "task_spec" in js:
    ok("会请求 /audio/task_spec")
else:
    bad("未请求 task_spec（阶段/时长/文本都应由后端给出）")

if re.search(r"sample_rate", js):
    ok("读取了 spec.sample_rate")
else:
    bad("未读取 sample_rate")

# 16000 是历史错误值；出现即为回归
if re.search(r"\b16000\b", js) or re.search(r"16\s*kHz", js_raw):
    bad("出现 16kHz/16000 —— 后端 task_spec.sample_rate 是 48000")
else:
    ok("未出现错误的 16kHz 标注")

# 卡片上的采样率标注应是动态的
if re.search(r"data-vt-sr", panels):
    ok("卡片采样率为动态占位（data-vt-sr）")
else:
    bad("卡片采样率未做成动态占位")

print()
print("=== 3. null 不能渲染成 0 ===")
# num() 必须显式处理 null/undefined
mnum = re.search(r"function num\s*\([^)]*\)\s*\{(.{0,400}?)\}", js, re.S)
if mnum and "null" in mnum.group(1) and "undefined" in mnum.group(1):
    ok("num() 显式处理 null/undefined")
else:
    bad("num() 未显式处理 null/undefined（会把未测到渲染成 0）")

if re.search(r"['\"]—['\"]", js):
    ok("未测到时渲染占位符「—」")
else:
    bad("未找到占位符渲染")

# 四种裁定都要有文案，否则用户无法区分「没读完」与「正常」
for v in ("incomplete", "no_speech", "unknown"):
    if v in js:
        ok(f"处理了 speech_rate.verdict = {v}")
    else:
        bad(f"未处理 verdict = {v}")

# usable=false 时不得展示指标
if re.search(r"usable\s*===\s*false", js):
    ok("usable=false 时不展示指标")
else:
    bad("未处理 usable=false（会把后端判定不可用的音频当成结论展示）")

print()
print("=== 4. 朗读段不得倒计时（until_user_done）===")
if "until_user_done" in js or "duration_mode" in js:
    ok("读取了 duration_mode")
else:
    bad("未读取 duration_mode")

# 锁定判定必须基于 duration_sec 是否为 null，而不是硬编码 55
if re.search(r"\b55\b", js):
    warn("出现字面量 55 —— 确认它不是被当作硬性时长使用"
         "（duration_hint_sec 仅用于提示）")
else:
    ok("未出现硬编码的 55 秒")

if re.search(r"duration_sec\s*!==\s*null", js):
    ok("以 duration_sec 是否为 null 判断是否锁定时长")
else:
    bad("未按 duration_sec === null 判断锁定与否")

if "max_duration_sec" in js:
    ok("处理了 max_duration_sec 硬上限（避免录完才吃 413）")
else:
    bad("未处理 max_duration_sec")

print()
print("=== 5. 资源释放（麦克风不能一直占用）===")
if re.search(r"getTracks\(\)[\s\S]{0,80}?\.stop\(\)", js):
    ok("停止了 MediaStream 的所有 track")
else:
    bad("未停止 MediaStream track（浏览器会一直显示录音指示灯）")

if re.search(r"ctx\.close\(\)", js):
    ok("关闭了 AudioContext")
else:
    bad("未关闭 AudioContext")

if "psy-panel-close" in js and "psy-panel-close" in html:
    ok("面板关闭事件已在 JS 与 shell.html 两侧对接")
else:
    bad("psy-panel-close 事件未在两侧对接（关闭面板后麦克风不释放）")

# 切换面板走的是 openPopup 里的 remove 分支，也必须派发
mopen = re.search(r"function openPopup\([\s\S]{0,500}?\n\}", html)
if mopen and "psy-panel-close" in mopen.group(0):
    ok("切换面板时也派发了关闭事件")
else:
    bad("切换面板时未派发关闭事件（从心理切到其他面板后麦克风仍占用)")

print()
print("=== 6. DOM 钩子与 JS 选择器一一对应 ===")
hooks = sorted(set(re.findall(r"data-vt-([a-z0-9]+)", panels)))
used = sorted(set(re.findall(r"data-vt-([a-z0-9]+)", js)))
missing = [h for h in used if h not in hooks]
unused = [h for h in hooks if h not in used]
print(f"     markup 提供: {hooks}")
print(f"     JS   使用  : {used}")
if missing:
    bad(f"JS 引用了 markup 中不存在的钩子: {missing}")
else:
    ok("JS 引用的钩子都在 markup 中存在")
if unused:
    warn(f"markup 中有未被使用的钩子: {unused}")

print()
print("=== 7. mount 顺序：录音必须在 DASS early-return 之前 ===")
mp = re.search(r"mount:\s*\(root\)\s*=>\s*\{", panels)
if not mp:
    bad("未找到 psy 面板的 mount")
else:
    body = panels[mp.end():mp.end() + 2000]
    i_voice = body.find("VoiceRecorder")
    i_ret = body.find("if (!listEl) return")
    if i_voice == -1:
        bad("mount 中未调用 VoiceRecorder")
    elif i_ret != -1 and i_voice > i_ret:
        bad("VoiceRecorder 调用在 DASS 的 early-return 之后",
            "DASS 结构缺失时录音会被静默跳过")
    else:
        ok("VoiceRecorder 在 early-return 之前调用")
    if re.search(r"try\s*\{[\s\S]{0,200}?VoiceRecorder", body):
        ok("VoiceRecorder 调用包在 try 中（录音失败不影响 DASS）")
    else:
        bad("VoiceRecorder 调用未包 try")

print()
print("=== 8. CSS：新增元素不得破坏语音卡高度预算 ===")
if ".pv3-vt-status" in css:
    ok("状态行样式存在")
else:
    bad("缺少 .pv3-vt-status 样式")

mm = re.search(r"\.psy-v3 \.pv3-vt-metrics\{([^}]*)\}", css)
if mm:
    blk = mm.group(1)
    if "min-height:0" in blk and "overflow-y:auto" in blk:
        ok("指标区可内部滚动（min-height:0 + overflow-y:auto）")
    else:
        bad("指标区缺少 min-height:0 / overflow-y:auto",
            "内容变多会把录音按钮挤出 overflow:hidden 的卡片")
else:
    bad("缺少 .pv3-vt-metrics 样式")

# 脉冲光环只能在录音时亮，否则视觉状态与真实状态不符
if re.search(r"\.pv3-mic-btn\.rec::before", css):
    ok("脉冲光环仅在 .rec 时显示")
else:
    bad("脉冲光环未限定在 .rec（待录音状态会看起来像正在录音）")

if re.search(r"\.pv3-mic-btn\.disabled\{", css):
    ok("存在明确的不可用态样式")
else:
    bad("缺少 .disabled 样式（用户会反复点一个不响应的按钮）")

print()
print("=== 9. 不得保留与事实相反的表述 ===")
if "不上传原始音频" in panels:
    bad("仍写着「不上传原始音频」，但实现确实上传了 WAV",
        "这是对用户的不成立承诺")
else:
    ok("已移除「不上传原始音频」的错误表述")

# 自由叙述后端已禁用
if re.search(r">自由叙述<", panels):
    bad("仍保留「自由叙述」页签，但后端 disabled_stages 已禁用该段")
else:
    ok("未保留已禁用的自由叙述入口")

if "录音功能开发中" in panels:
    bad("仍保留「录音功能开发中」提示")
else:
    ok("已移除「开发中」提示")

print()
if warns:
    print(f"⚠️  {len(warns)} 条提醒：")
    for w in warns:
        print("   - " + w)
    print()
if fails:
    print(f"❌ {len(fails)} 项失败")
    for f in fails:
        print("   - " + f)
    sys.exit(1)
print("✅ 语音录音 UI 与后端契约一致性检查全部通过")
print("   注：静态检查，无法替代真实浏览器中的录音/权限/上传验证。")
