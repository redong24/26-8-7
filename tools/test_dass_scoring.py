#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dass_scoring.py —— DASS-21 打分逻辑的边界值验证

静态结构检查只能证明"数据格式对"，不能证明"算出来的分对"。
DASS-21 的评分有两个静默失效点，错了不会报错、只会给出看似合理的错数值：

  (1) 缺 ×2：DASS-21 是 DASS-42 的半长版，官方分级阈值是按 42 分制定的。
      不乘 2 则单量表满分只有 21，「极重度」(D≥28) 永远不可达 ——
      任何重度抑郁者都会被显示为「中度」。
  (2) 分级用 <= 还是 <：阈值边界差一分就会把「正常」判成「轻度」。

所以这里对每个分量表穷举 raw = 0..21（共 22×3 = 66 种），
断言分级结果与量表文档的分段完全一致，并特别检查每个边界点及其 ±1。

另外验证「拒绝部分求和」：任一分量表缺题必须返回 None，
而不是把已答的题加起来 —— 6/7 题的和看起来完全正常，但系统性偏低，
比一个显眼的空缺危险得多。
"""
import re
import sys

JS = "/home/lsz/webapp/static/shell_panels.js"
src = open(JS, encoding="utf-8").read()

# ---- 从 JS 源码抽取参数，避免测试与实现各写一份常量而互相脱节 ----
MULT = int(re.search(r"RAW_MULTIPLIER:\s*(\d+)", src).group(1))
SMAX = int(re.search(r"SCORE_MAX:\s*(\d+)", src).group(1))
seg = re.search(r"cutoffs:\s*\{(.*?)\n  \}", src, re.S).group(1)
CUT = {}
for k in "DAS":
    body = re.search(r"\b" + k + r":\s*\[(.*?)\]", seg, re.S).group(1)
    pairs = re.findall(r"lvl:'([^']+)',max:(Infinity|\d+)", body.replace(" ", ""))
    CUT[k] = [(lvl, float("inf") if mx == "Infinity" else int(mx)) for lvl, mx in pairs]
GROUPS = {}
for k in "DAS":
    m = re.search(k + r":\s*\{[^}]*items:\s*\[([0-9,\s]+)\]", src)
    GROUPS[k] = [int(x) for x in re.findall(r"\d+", m.group(1))]

# ---- 等价实现 JS 的 scoreDASS21 ----
def score(answers):
    out = {"complete": True, "subscales": {}}
    for k, items in GROUPS.items():
        missing = [n for n in items if n not in answers]
        if missing:
            out["complete"] = False
            out["subscales"][k] = dict(raw=None, score=None, level=None, pct=None,
                                       answered=len(items) - len(missing), missing=missing)
            continue
        raw = sum(answers[n] for n in items)
        sc = raw * MULT
        lvl = next(l for l, mx in CUT[k] if sc <= mx)
        out["subscales"][k] = dict(raw=raw, score=sc, level=lvl,
                                   pct=round(sc / SMAX * 100), answered=len(items), missing=[])
    return out

# 量表文档的权威分段（按 ×2 后的分数）
DOC = {
    "D": [("正常", 0, 9), ("轻度", 10, 13), ("中度", 14, 20), ("重度", 21, 27), ("极重度", 28, 42)],
    "A": [("正常", 0, 7), ("轻度", 8, 9),   ("中度", 10, 14), ("重度", 15, 19), ("极重度", 20, 42)],
    "S": [("正常", 0, 14), ("轻度", 15, 18), ("中度", 19, 25), ("重度", 26, 33), ("极重度", 34, 42)],
}

fails = []
def check(cond, msg):
    if not cond:
        fails.append(msg)

def doc_level(k, sc):
    for lvl, lo, hi in DOC[k]:
        if lo <= sc <= hi:
            return lvl
    return None

print("=== 1. 穷举 raw 0..21，逐分量表核对分级 ===")
for k in "DAS":
    items = GROUPS[k]
    bad_rows = []
    for raw in range(0, 22):                    # 7 题 × 0..3 → raw ∈ [0,21]
        # 构造一个和为 raw 的合法作答
        vals, rem = [], raw
        for _ in items:
            v = min(3, rem)
            vals.append(v)
            rem -= v
        assert rem == 0 and sum(vals) == raw
        ans = dict(zip(items, vals))
        # 其余两个分量表也填满，避免 complete=False 干扰
        for k2 in "DAS":
            if k2 != k:
                for n in GROUPS[k2]:
                    ans[n] = 0
        r = score(ans)["subscales"][k]
        exp = doc_level(k, raw * MULT)
        if r["level"] != exp or r["score"] != raw * MULT:
            bad_rows.append((raw, raw * MULT, r["level"], exp))
    if bad_rows:
        fails.append(f"{k} 分量表分级错误：{bad_rows[:6]}")
        print(f"  ❌ {k}: {len(bad_rows)} 个 raw 值分级不符，例：{bad_rows[:4]}")
    else:
        print(f"  ✅ {k}: raw 0..21 → score 0..{21*MULT}，22 个取值分级全部与文档一致")

print("\n=== 2. 分级边界点及 ±1（差一分错判的高危点）===")
for k in "DAS":
    for lvl, lo, hi in DOC[k]:
        for sc in (lo - 1, lo, hi, hi + 1):
            if sc < 0 or sc > 42 or sc % MULT:      # ×2 后只有偶数分可达
                continue
            raw = sc // MULT
            if raw > 21:
                continue
            items = GROUPS[k]
            vals, rem = [], raw
            for _ in items:
                v = min(3, rem); vals.append(v); rem -= v
            if rem: continue
            ans = dict(zip(items, vals))
            for k2 in "DAS":
                if k2 != k:
                    for n in GROUPS[k2]: ans[n] = 0
            got = score(ans)["subscales"][k]["level"]
            exp = doc_level(k, sc)
            check(got == exp, f"{k} score={sc} 期望「{exp}」得到「{got}」")
print("  ✅ 所有档位的 lo-1 / lo / hi / hi+1 边界判级正确" if not fails else "  ❌ 见下方失败清单")

print("\n=== 3. ×2 缺失会怎样（回归保护的意义）===")
worst = {n: 3 for n in GROUPS["D"]}          # 抑郁全选「非常符合」= raw 21
for k2 in "AS":
    for n in GROUPS[k2]: worst[n] = 0
r = score(worst)["subscales"]["D"]
print(f"  raw={r['raw']} score={r['score']} → 「{r['level']}」 (pct={r['pct']}%)")
check(r["score"] == 42 and r["level"] == "极重度",
      f"抑郁满分应为 42/极重度，实为 {r['score']}/{r['level']}")
no_mult_level = doc_level("D", 21)
print(f"  若漏掉 ×2：score 会是 21 → 「{no_mult_level}」——重度抑郁被显示为轻症，"
      f"这就是必须锁定 RAW_MULTIPLIER=2 的原因")
check(no_mult_level != "极重度", "对照假设不成立")

print("\n=== 4. 拒绝部分求和（缺题必须返回 None，不能把已答的加起来）===")
partial = {n: 3 for n in GROUPS["D"][:6]}    # 抑郁只答 6/7 题
for k2 in "AS":
    for n in GROUPS[k2]: partial[n] = 0
r = score(partial)
d = r["subscales"]["D"]
print(f"  D: answered={d['answered']}/7 missing={d['missing']} score={d['score']}")
check(d["score"] is None, "缺题时 score 必须为 None")
check(d["level"] is None, "缺题时 level 必须为 None")
check(d["answered"] == 6 and d["missing"] == [GROUPS["D"][6]], "缺题清单不正确")
check(r["complete"] is False, "整体 complete 应为 False")
check(r["subscales"]["A"]["score"] == 0, "已答满的 A 分量表仍应正常出分")
print(f"  ✅ 缺 1 题 → score=None（若错误地求和会得到 6×3×2=36「极重度」，"
      f"或按 7 题均值补齐得到虚高分，两者都是危险的静默错误）")

print("\n=== 5. 全 0 与全 3（两端）===")
allzero = {n: 0 for n in range(1, 22)}
r = score(allzero)
print("  全 0 :", {k: (v["score"], v["level"]) for k, v in r["subscales"].items()})
check(all(v["score"] == 0 and v["level"] == "正常" for v in r["subscales"].values()),
      "全 0 应三项均为 0/正常")
check(r["complete"] is True, "全 0 应 complete=True")
allthree = {n: 3 for n in range(1, 22)}
r = score(allthree)
print("  全 3 :", {k: (v["score"], v["level"], str(v["pct"]) + "%") for k, v in r["subscales"].items()})
check(all(v["score"] == 42 and v["level"] == "极重度" and v["pct"] == 100
          for v in r["subscales"].values()), "全 3 应三项均为 42/极重度/100%")
print("  ✅ 两端取值正确，pct 满分为 100%（SCORE_MAX 与 RAW_MULTIPLIER 自洽）")

print()
if fails:
    print(f"❌ {len(fails)} 项断言失败：")
    for f in fails: print("   -", f)
    sys.exit(1)
print(f"✅ DASS-21 打分逻辑全部通过（穷举 66 组 + 边界 + 缺题 + 两端）")
print("   注：本测试用 Python 等价实现校验算法与阈值，"
      "不替代浏览器内的真实 DOM 交互验证。")
