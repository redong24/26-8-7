#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_speech_rate.py —— 朗读语速裁定逻辑的验证

背景（用户决策 2026-08-12）
--------------------------
用户提问：「55s 到点但没读完时语速怎么处理？是否可以不锁定55秒，
比如说我读完用时1分23秒，就按照实际时长来计算？」
结论：不锁定时长，按实际用时计算。

本测试要证明的三件事
--------------------
1) 【锁 55s 是错的】截断会让语速系统性高估，且偏差方向与目标信号相反
   （读得慢的人 = 最该被观察的人，却被高估回正常区间）
2) 【新逻辑正确】按实际跨度计算时，1分23秒读完 139 字 = 100.5 字/分
3) 【未读完不静默出错数】覆盖率不足时分子降级为 ASR 字数并标记不可信；
   拿不到 ASR 时返回 None（宁缺勿猜）

为什么不直接 import audio_service
---------------------------------
audio_service 顶部会 import features_handcrafted 与 models_deep（torch/funasr），
本沙箱未装且会拉起 GPU 依赖。这里用 AST 从源码中提取 _speech_rate /
_han_count / READING_COVERAGE_MIN 三者的定义并 exec，
既避免重依赖，又保证测的是【真实实现】而非副本（实现改了测试就会随之改变）。
"""
import ast
import os
import sys
import textwrap

SVC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "audio_service", "audio_service.py")
CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "audio_service", "audio_client.py")

fails = []


def check(cond, msg, detail=""):
    if cond:
        print(f"  ✅ {msg}")
    else:
        fails.append(msg)
        print(f"  ❌ {msg}{('  ← ' + detail) if detail else ''}")


# ---------------------------------------------------------------- 提取实现
def load_impl():
    """从 audio_service.py 中抽出待测函数，不触发模块级重依赖。"""
    src = open(SVC, encoding="utf-8").read()
    tree = ast.parse(src)
    want_fn = {"_han_count", "_speech_rate"}
    want_const = {"READING_COVERAGE_MIN"}
    picked = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want_fn:
            picked.append(ast.get_source_segment(src, node))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in want_const:
                    # 原式含 os.environ.get，这里固定为默认值 0.90，
                    # 避免测试结果被环境变量影响（测试必须可复现）
                    picked.append("READING_COVERAGE_MIN = 0.90")
    missing = want_fn - {ast.parse(p).body[0].name for p in picked
                         if isinstance(ast.parse(p).body[0], ast.FunctionDef)}
    if missing:
        print(f"❌ 未能从 audio_service.py 提取到：{missing}")
        sys.exit(1)
    ns = {}
    exec("\n\n".join(picked), ns)
    return ns


ns = load_impl()
_speech_rate = ns["_speech_rate"]
_han_count = ns["_han_count"]
COV_MIN = ns["READING_COVERAGE_MIN"]


def feats(span_sec):
    return {"rhythm": {"span_sec": span_sec}}


def deep(asr_text):
    return {"asr": {"text": asr_text}} if asr_text is not None else {}


# ---------------------------------------------------------------- 文本口径
print("=== 1. 字数口径：只数汉字 ===")
TEXT = ("有一次，北风和太阳正在争论谁比较有本事。"
        "他们正好看到有个穿着大衣的人走过来，"
        "他们就说，谁可以让那个人脱掉那件大衣，就算谁比较有本事。"
        "于是北风开始拼命地吹。怎知，他吹得越厉害，"
        "那个人就越是用大衣包裹自己。最后，北风没办法，就放弃了。"
        "接着，太阳出来晒了一会儿，那个人感觉变得很热，"
        "立刻把大衣脱掉了。于是，北风只好认输了。")
han = _han_count(TEXT)
total = len(TEXT)
print(f"  《北风与太阳》汉字 {han} / 总字符 {total}"
      f"（标点 {total - han}，占 {(total-han)/total:.1%}）")
check(han == 139, f"汉字数 = 139（文档核对值），实得 {han}")
check(_han_count("abc123，。！") == 0, "纯标点与拉丁字符计 0 字")

# 与 audio_client 的口径必须一致，否则覆盖率失真
cli_src = open(CLI, encoding="utf-8").read()
cli_tree = ast.parse(cli_src)
cli_ns = {}
for node in cli_tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "han_count":
        exec(ast.get_source_segment(cli_src, node), cli_ns)
check("han_count" in cli_ns, "audio_client 侧存在 han_count")
if "han_count" in cli_ns:
    check(cli_ns["han_count"](TEXT) == han,
          "客户端与服务端字数口径一致（否则覆盖率失真、误判未读完）")
# 客户端 TASK_SPEC 里必须真的用了 han_count 而不是 len()
check("han_count(FIXED_READING_TEXT)" in cli_src,
      "TASK_SPEC.text_char_count 使用 han_count（不是 len）")
check("\"duration_sec\": None" in cli_src or "'duration_sec': None" in cli_src,
      "TASK_SPEC 朗读段 duration_sec = None（不锁定时长）")

# ---------------------------------------------------------------- 核心用例
print()
print("=== 2. 用户场景：读完用时 1 分 23 秒 ===")
r = _speech_rate(feats(83.0), deep(TEXT), expected_chars=han)
print(f"  → cpm={r['cpm']} verdict={r['verdict']} "
      f"reliable={r['reliable']} coverage={r['coverage']}")
expect = round(139 / (83 / 60.0), 1)
check(r["cpm"] == expect, f"语速 = {expect} 字/分（139字 ÷ 83s）")
check(r["verdict"] == "complete", "判定为读完全文")
check(r["reliable"] is True, "标记为可信")
check(r["denominator"] == "span_sec", "分母使用 span_sec（首字起→末字止）")

print()
print("=== 3. 证明「锁 55s」会算错 ===")
# 场景：用户语速 100 字/分，需 83s 读完；55s 到点时只读了 55/83 ≈ 66% ≈ 92 字
read_ratio = 55.0 / 83.0
partial_chars = int(han * read_ratio)
partial_text = TEXT[:int(len(TEXT) * read_ratio)]
locked_wrong = round(han / (55 / 60.0), 1)          # 旧逻辑：分子仍用全文字数
truth = expect                                      # 真实语速
print(f"  真实语速          : {truth} 字/分")
print(f"  锁55s+全文字数分子: {locked_wrong} 字/分  ← 旧实现的输出")
print(f"  高估幅度          : {(locked_wrong/truth - 1):.1%}")
check(locked_wrong > truth * 1.4,
      f"旧实现把 {truth} 高估为 {locked_wrong}（+{(locked_wrong/truth-1):.0%}），"
      "证明锁定时长会产出「看起来正常的错数」")
# 新逻辑面对同一段截断录音：必须识别出没读完
r2 = _speech_rate(feats(55.0), deep(partial_text), expected_chars=han)
print(f"  新实现对同一段截断: cpm={r2['cpm']} verdict={r2['verdict']} "
      f"reliable={r2['reliable']} coverage={r2['coverage']}")
check(r2["verdict"] == "incomplete", "新实现识别出「未读完」")
check(r2["reliable"] is False, "未读完 → 标记不可信（不冒充有效测量）")
check(r2["numerator"] == "asr_chars", "分子降级为实际识别字数，而非全文字数")
check(r2["cpm"] < locked_wrong,
      f"新实现 {r2['cpm']} 显著低于旧实现的虚高值 {locked_wrong}")
check(abs(r2["cpm"] - truth) < truth * 0.15,
      f"降级后的语速 {r2['cpm']} 仍接近真值 {truth}（误差 <15%）")

print()
print("=== 4. 慢读者不再被截断（偏差方向问题）===")
# 语速 70 字/分的慢读者，需 119s。锁 55s 只能读到 64 字
slow_span = round(139 / 70.0 * 60, 1)
r3 = _speech_rate(feats(slow_span), deep(TEXT), expected_chars=han)
print(f"  慢读者实际用时 {slow_span}s → cpm={r3['cpm']} "
      f"verdict={r3['verdict']}")
check(abs(r3["cpm"] - 70.0) < 1.0,
      f"慢读者语速被正确测为 {r3['cpm']} 字/分（真值 70）")
locked_slow = round(han / (55 / 60.0), 1)
check(locked_slow > 100,
      f"若锁 55s，该慢读者会被算成 {locked_slow} 字/分 —— "
      "从「偏慢」被高估进「正常/偏快」区间，信号被抹掉")

print()
print("=== 5. 边界与拒绝行为 ===")
# 阈值边界必须按【字数】而非百分比构造：字数是整数，
# 139×0.90=125.1 取整成 125 字后覆盖率 0.8993，本就低于阈值，
# 用百分比构造会误以为实现有 off-by-one。
# 正确做法：算出「恰好达标的最小字数」n_min，测 n_min 与 n_min-1。
import math
n_min = math.ceil(han * COV_MIN)                 # 125.1 → 126
check(n_min / han >= COV_MIN, f"n_min={n_min} 确实达标（{n_min/han:.4f}）")
check((n_min - 1) / han < COV_MIN,
      f"n_min-1={n_min-1} 确实不达标（{(n_min-1)/han:.4f}）")
for n, want in ((n_min, "complete"), (n_min - 1, "incomplete"),
                (han, "complete"), (han // 2, "incomplete")):
    rr = _speech_rate(feats(83.0), deep("啊" * n), expected_chars=han)
    check(rr["verdict"] == want,
          f"识别 {n}/{han} 字（覆盖率 {n/han:.1%}）→ {want}"
          f"（实得 {rr['verdict']}）")
# 超过全文字数（ASR 重复识别/口误重读）不应判为未读完
rr = _speech_rate(feats(83.0), deep("啊" * (han + 20)), expected_chars=han)
check(rr["verdict"] == "complete",
      f"ASR 多识别（{han+20}/{han}，覆盖率 {rr['coverage']}）仍判 complete")

# 一个字都没识别出来：必须区分「没测到」与「语速为 0」。
# 若按公式算会得到 cpm=0.0，下游很容易把 0 当真实测量值（画趋势图、算均值），
# 比返回 None 危险得多。真实场景：麦克风拾到噪声/音乐、用户全程没出声。
for txt, desc in ((None, "ASR 文本为 None"), ("", "ASR 文本为空串"),
                  ("，。！?abc123", "只有标点与拉丁字符")):
    rr = _speech_rate(feats(83.0), {"asr": {"text": txt}}, expected_chars=han)
    check(rr["cpm"] is None and rr["reliable"] is False,
          f"{desc} → cpm=None（不是 0.0），verdict={rr['verdict']}")
rr = _speech_rate(feats(83.0), deep("，。！"), expected_chars=han)
check(rr["verdict"] == "no_speech",
      f"零汉字 → verdict=no_speech（实得 {rr['verdict']}），"
      "明确区分「没测到」与「语速为 0」")

# 拿不到 ASR：必须返回 None 而不是猜
r4 = _speech_rate(feats(83.0), {}, expected_chars=han)
check(r4["cpm"] is None and r4["verdict"] == "unknown",
      "无 ASR → cpm=None + verdict=unknown（宁缺勿猜）")
r5 = _speech_rate(feats(83.0), {"skipped": "x"}, expected_chars=han)
check(r5["cpm"] is None, "深度模型被跳过 → 同样不猜")

# 缺分母 / 缺文本
check(_speech_rate(feats(0), deep(TEXT), expected_chars=han) is None,
      "span_sec=0 → 返回 None（无法计算）")
check(_speech_rate(feats(83.0), deep(TEXT), expected_chars=None) is None,
      "无预期字数（如元音段）→ 返回 None")

print()
print("=== 6. 提前/延迟点「完成」不影响结果（用真实 VAD 帧序列验证）===")
# 这一节直接检验 span 的【计算过程】，而不是把同一个数字比较两次
# —— 后者是同义反复，恰好漏掉了下面这个真实发生过的缺陷：
#
# 缺陷（2026-08-12 实测发现）：span 的端点原先取 idx[0] / idx[-1]，
# 即【单帧】决定端点。19s 合成音（真实发声 3.0→14.6s，真值 span=11.60s）
# 头部静音里出现 3 个连续 VAD 误判帧（0.03~0.09s），
# 于是 span 被撑到 14.73s —— 虚高 27%，语速被系统性低估 21%。
# 这等于把「按下录音后的犹豫」重新放回分母，正是本次改动要消除的东西。
#
# 修复：端点要求【连续 >=120ms 发声】(_voiced_edge)。依据是汉语最短单音节
# 约 150~200ms，120ms 留安全余量。
_FH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "audio_service", "features_handcrafted.py")
_fh_tree = ast.parse(open(_FH, encoding="utf-8").read())
_ns = {}
exec("import numpy as np\n"
     "def _safe(v, nd=2):\n"
     "    return None if v is None else round(float(v), nd)", _ns)
for _n in _fh_tree.body:
    if isinstance(_n, ast.FunctionDef) and _n.name in ("_voiced_edge",
                                                       "rhythm_features"):
        exec(compile(ast.Module(body=[_n], type_ignores=[]),
                     "<fh>", "exec"), _ns)
rhythm_features = _ns.get("rhythm_features")

if rhythm_features is None:
    check(False, "能从 features_handcrafted.py 提取 rhythm_features")
else:
    import numpy as _np
    FS = 0.03                      # webrtcvad 30ms 帧

    def vflags(*runs):
        """runs = [(秒, 是否语音), ...] -> bool 帧数组"""
        out = []
        for dur, voiced in runs:
            out += [voiced] * int(round(dur / FS))
        return _np.asarray(out, dtype=bool)

    # 6.1 复现缺陷场景：头部静音中有 90ms 孤立误判帧。
    #     朗读段内部含停顿（6 段发声 + 5 段 0.4s 停顿 = 11.60s），
    #     这样 speech_sec < span_sec 才有意义 —— 全程连续发声时
    #     两者必然相等，那种构造无法检验分母的区分度。
    _read = []
    for _i in range(6):
        _read.append((1.6, True))
        if _i < 5:
            _read.append((0.4, False))
    r = rhythm_features(vflags((0.03, False), (0.09, True), (2.88, False),
                               *_read,
                               (4.40, False)), FS)
    check(abs(r["span_sec"] - 11.60) < 0.35,
          f"头部孤立误判帧不再撑大 span（得 {r['span_sec']}，真值 11.60）",
          "单帧端点会得到约 14.7s")
    check(r["duration_sec"] > r["span_sec"] > r["speech_sec"],
          f"三者关系成立 duration({r['duration_sec']}) > "
          f"span({r['span_sec']}) > speech({r['speech_sec']})")

    # 6.2 手速无关性：同一段朗读，前后静音长度不同 -> span 必须一致
    s1 = rhythm_features(vflags((0.5, False), (11.60, True), (0.5, False)),
                         FS)["span_sec"]
    s2 = rhythm_features(vflags((6.0, False), (11.60, True), (9.0, False)),
                         FS)["span_sec"]
    check(abs(s1 - s2) < 1e-9,
          f"首尾静音长度不影响 span（{s1} == {s2}）—— 手速不污染测量")

    # 6.3 真实短起始音不能被 120ms 阈值切掉（汉语最短音节 150~200ms）
    r = rhythm_features(vflags((1.0, False), (0.15, True), (0.30, False),
                               (3.0, True), (1.0, False)), FS)
    check(abs(r["span_sec"] - 3.45) < 0.35,
          f"150ms 真实起始音被保留（span={r['span_sec']}，真值 3.45）")

    # 6.4 只有零星杂音 -> span=0 且 reliable=False
    r = rhythm_features(vflags((1.0, False), (0.03, True), (1.0, False),
                               (0.03, True), (1.0, False)), FS)
    check(r["span_sec"] == 0.0 and r["reliable"] is False,
          f"零星杂音 -> span=0 且 reliable=False"
          f"（得 span={r['span_sec']}, reliable={r['reliable']}）")
    # 上层据此必须整体返回 None（不是 {"cpm": 0.0}），
    # 否则 0 会作为「语速为零」这个真实数据点进入均值与趋势图。
    r0 = _speech_rate({"rhythm": {"span_sec": 0.0}}, deep(TEXT),
                      expected_chars=han)
    check(r0 is None,
          f"span=0 时 _speech_rate 整体返回 None，不产出 0 字/分（得 {r0!r}）")

    # 6.5 正常连续朗读不受修复影响
    r = rhythm_features(vflags((0.2, False), (6.0, True), (0.2, False)), FS)
    check(abs(r["span_sec"] - 6.0) < 0.35,
          f"连续 6s 朗读 span 正常（{r['span_sec']}）")

print()
if fails:
    print(f"❌ {len(fails)} 项失败")
    for f in fails:
        print("   - " + f)
    sys.exit(1)
print("✅ 语速裁定逻辑全部通过")
print("   已证明：锁定 55s 会产出系统性高估的假数据；按实际用时为真值；")
print("   未读完时降级并标记不可信；无 ASR 时返回 None 不猜测。")
print("   注：本测试覆盖裁定逻辑，不含真实音频的 VAD/ASR 精度验证。")
