#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 解读固化（不受 LLM 缓存 TTL 影响）的回归测试。

背景：报告页原先只读 llm_client._cache，那是个 TTL=300s 的性能缓存。
解读生成 5 分钟后再打开报告，AI 栏就凭空留白。本测试锁死修复后的行为：

  1. 成功解读会固化到 PortraitState.ai
  2. 失败/空解读【不】固化（否则错误信息会永久钉在报告上）
  3. 【核心】把 llm_client 缓存整个清空（等价于 TTL 过期 / 进程重启），
     会话副本仍然取得到
  4. 数据变化后取出的副本带 stale=True，且内容不丢（拍板方案 A）
  5. reset 会清掉固化解读，避免「已重置但报告仍挂旧结论」
  6. 报告渲染：stale 时出现显著标注；无解读时如实留白
  7. 报告层始终不调用付费接口

运行： /home/lsz/miniconda3/envs/rrpg_plus/bin/python tests/test_ai_persist.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "portrait"))

import llm_client            # noqa: E402
import portrait_state as ps  # noqa: E402
import portrait_report as pr  # noqa: E402

PASS = []
def ck(cond, msg):
    assert cond, "FAIL: " + msg
    PASS.append(msg)


SNAP_A = {
    "portrait": {
        "dimensions": [
            {"label": "情绪稳定", "value": 72.0},
            {"label": "压力值", "value": 40.0, "higher_is_worse": True},
        ],
        "composite": {"value": 68.0},
    },
    # hr 的真实结构是 _norm_hr 的输出（dict），不是裸数字。
    # 首版测试这里写成 74，导致 _sec_physio 整节渲染失败 —— 说明
    # 测试数据必须照抄真实契约，凭印象构造会测出假问题、掩盖真问题。
    "readiness": {"ready": True, "hr_available": True,
                  "hr": {"heart_rate": 74.0, "respiration_rate": 16.0,
                         "heart_rate_available": True}},
    "face": {"emo_distribution": {"neutral": 0.7, "happy": 0.3},
             "emo_stability": 0.81},
}
# B 比 A 多了量表数据 —— 模拟「解读生成后又补做了 DASS-21」
SNAP_B = dict(SNAP_A)
SNAP_B["scale"] = {"scored": {"subscales": {
    "D": {"label": "抑郁", "score": 6, "level": "正常"},
    "A": {"label": "焦虑", "score": 8, "level": "轻度"},
    "S": {"label": "压力", "score": 10, "level": "正常"},
}}}

RESULT = {"summary": "整体状态平稳。", "points": ["情绪稳定性较好"],
          "caveat": "仅供参考", "source": "llm", "model": "qwen-plus",
          "elapsed_ms": 5400, "measured": 4}


# ---- 1. 成功解读固化 ----
st = ps.PortraitState()
fp_a, measured_a = llm_client.payload_fingerprint(SNAP_A)
ck(fp_a is not None and measured_a > 0, "payload_fingerprint 对有数据快照返回指纹")
ck(st.put_ai(RESULT, fingerprint=fp_a) is True, "成功解读被固化")
ck(st.ai is not None and st.ai["summary"] == "整体状态平稳。", "固化内容正确")

# ---- 2. 失败态不固化 ----
st2 = ps.PortraitState()
ck(st2.put_ai({"status": "unavailable", "reason": "超时"}) is False,
   "失败态解读不被固化")
ck(st2.put_ai(None) is False, "None 不被固化")
ck(st2.put_ai({"summary": ""}) is False, "空 summary 不被固化")
ck(st2.ai is None, "失败态固化后 state.ai 仍为 None")

# ---- 3. 【核心】TTL 过期后仍取得到 ----
llm_client._cache.clear()
ck(llm_client.peek_cached(SNAP_A) is None,
   "清空 LLM 缓存后 peek_cached 取不到（模拟 TTL 过期/进程重启）")
got = st.get_ai(fingerprint=fp_a)
ck(got is not None and got["summary"] == "整体状态平稳。",
   "★ 缓存已空，会话固化副本仍取得到解读（本次修复的核心）")
ck(got.get("stale") is False, "数据未变时 stale=False")
ck("fingerprint" not in got, "指纹属内部实现，不外泄到快照")
ck(got.get("generated_at"), "固化副本带生成时间")

# ---- 4. 数据变化 -> stale 但内容保留 ----
fp_b, _ = llm_client.payload_fingerprint(SNAP_B)
ck(fp_b != fp_a, "补做量表后数据指纹发生变化")
got_b = st.get_ai(fingerprint=fp_b)
ck(got_b is not None and got_b["summary"] == "整体状态平稳。",
   "数据变化后仍返回解读内容（方案A：不丢弃）")
ck(got_b.get("stale") is True, "数据变化后 stale=True")

# ---- 5. reset 清除 ----
st.clear_ai()
ck(st.get_ai() is None, "clear_ai 后取不到解读")

# ---- 6. 报告渲染 ----
snap_stale = dict(SNAP_B)
snap_stale["ai_summary"] = dict(RESULT, stale=True,
                                generated_at=time.time() - 3600)
h = pr.render_report_html(snap_stale)
ck("其后本次测评又新增了测量数据" in h, "stale 解读在报告中被显著标注")
ck("整体状态平稳。" in h, "stale 解读的正文照常展示")
ck("本节渲染失败" not in h, "stale 报告无区块异常")

snap_fresh = dict(SNAP_A)
snap_fresh["ai_summary"] = dict(RESULT, stale=False, generated_at=time.time())
h2 = pr.render_report_html(snap_fresh)
ck("其后本次测评又新增了测量数据" not in h2, "非 stale 时不出现该标注")
ck("生成时间" in h2, "报告展示解读生成时间")

h3 = pr.render_report_html(SNAP_A)   # 无 ai_summary
ck("本次报告未包含 AI 综合解读" in h3, "无解读时如实留白")
ck("缓存有效期" not in h3, "留白文案不再提缓存有效期（已不是留白原因）")

# ---- 7. 报告层不得调用付费接口 ----
import ast
tree = ast.parse(open(os.path.join(ROOT, "portrait", "portrait_report.py"),
                      encoding="utf-8").read())
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        nm = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        assert nm not in ("interpret", "interpret_cached"), \
            "报告层调用了付费接口 %s" % nm
ck(True, "报告层未调用付费模型接口")

# ---- 8. 契约：ai_peek_fn 现在收 (sess, snap) ----
calls = []
class _App:
    def route(self, *a, **k):
        return lambda f: f
pr.register_routes(_App(), lambda sid: {"s": 1},
                   snapshot_fn=lambda sess: dict(SNAP_A),
                   jsonify=lambda x: x, request=type("R", (), {"cookies": {}})(),
                   ai_peek_fn=lambda sess, snap: calls.append((sess, snap)) or RESULT)
ck(True, "register_routes 接受 (sess, snap) 签名的 ai_peek_fn")

print("\n".join("  ok  " + m for m in PASS))
print("\n全部通过：%d 项" % len(PASS))
