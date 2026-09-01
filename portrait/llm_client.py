# -*- coding: utf-8 -*-
"""
llm_client —— AI 解读的模型调用层（阿里云百炼 / DashScope，OpenAI 兼容模式）
================================================================================
职责边界（刻意划得很窄）：
  * 只负责「把已采集到的指标交给模型，取回一段中文解读」
  * 不做打分、不做判定、不碰会话状态 —— 那些属于 portrait_score / portrait_state

为什么不用 openai SDK，而是标准库 urllib：
  主应用跑在 rrpg_plus 环境里，那个环境装着 torch / tensorflow / mediapipe，
  是经过多轮验证的生产环境。为了一个 HTTP POST 往里装 openai 及其依赖链
  （httpx / pydantic 等），有实打实的版本冲突风险，收益却几乎为零 ——
  我们只用到 /chat/completions 一个端点。所以这里手写请求，零新增依赖。

凭证来源（按优先级）：
  1. 环境变量 DASHSCOPE_API_KEY
  2. ~/.config/hiko/llm.env（权限 600，仓库之外）
凭证【绝不】写进代码或版本库：仓库要推 GitHub，密钥进库即泄露。

安全底线（这是心理健康场景，不是通用聊天）：
  * 未测量的项一律不送给模型，也禁止它推断 —— 见 build_payload
  * 提示词禁止诊断结论、禁止编造数值、禁止医疗处置建议
  * 模型不可用时抛异常，由调用方回落到规则文案，绝不伪造内容
"""

import os
import json
import time
import threading
import urllib.request
import urllib.error

# ---------------------------------------------------------------- 配置读取

CONF_PATH = os.path.expanduser("~/.config/hiko/llm.env")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# 默认 qwen-plus 而不是 qwen3.8-max：同一份提示词实测 qwen3.8-max 需 67s、
# qwen-plus 仅 5.4s，而本任务（照给定数字做描述性归纳）不需要长链推理，
# 两者输出质量没有可感差别。67s 挂在用户点击的同步路径上是不可接受的。
DEFAULT_MODEL = "qwen-plus"

# 单次调用的墙钟上限。
# 实测：短提示 + enable_thinking=False 时 qwen3.8-max 约 4s 返回；
# 但换成本模块这种长 system 提示 + max_tokens=900 时，若开启深度思考，
# 45s 会超时（首版就踩到了）。因此下面显式关掉 thinking，并把上限提到 90s
# 留足余量 —— 这个请求挂在用户点击的同步路径上，必须有上限，但也不能太紧。
TIMEOUT_SEC = 90


def _load_conf_file(path=CONF_PATH):
    """读 KEY=VALUE 形式的配置。文件不存在不是错误（可能走环境变量）。"""
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except (IOError, OSError):
        pass
    return out


def get_config():
    """
    汇总凭证与模型配置。环境变量优先于配置文件，便于临时覆盖调试。
    返回 (api_key, base_url, model)；api_key 为 None 表示未配置。
    """
    conf = _load_conf_file()

    key = (os.environ.get("DASHSCOPE_API_KEY")
           or conf.get("DASHSCOPE_API_KEY") or "").strip()
    # 防呆：曾经踩到过配置文件里存着未展开的 "${GENSPARK_TOKEN}" 字面量，
    # 长度够、非空，但根本不是密钥，调用时才 403。这里提前识破。
    if not key or key.startswith("${") or len(key) < 20:
        key = None

    base = (os.environ.get("HIKO_LLM_BASE_URL")
            or conf.get("HIKO_LLM_BASE_URL") or DEFAULT_BASE_URL).strip()
    model = (os.environ.get("HIKO_LLM_MODEL")
             or conf.get("HIKO_LLM_MODEL") or DEFAULT_MODEL).strip()
    return key, base, model


def available():
    """凭证是否就绪。只看配置，不发网络请求 —— 供 /health 之类的快速探测用。"""
    return get_config()[0] is not None


# ---------------------------------------------------------------- 提示词

SYSTEM_PROMPT = """你是一个心理生理测评系统的数据解读助手。你的输出会直接展示给被测者。

【你能做什么】
只对下方"已采集数据"中真实存在的数值做描述性归纳：指出哪些指标偏高、哪些偏低、
彼此是否一致，以及在观察层面意味着什么。

【严格禁止（违反即为严重错误）】
1. 禁止给出任何诊断或疑似诊断结论。不得出现"抑郁""焦虑症""障碍""倾向"等判定性表述。
2. 禁止编造数据中不存在的数值。凡数据中标为"未测量"的维度，不得推断、不得脑补，
   如需提及只能说明"该项未采集"。
3. 禁止与常模/基线比较。系统没有常模数据，"高于常模""优于同龄人"这类话一律不许说。
4. 禁止给出医疗或处置建议（用药、就诊、具体训练方案、呼吸法步骤等）。
5. 禁止使用第一人称承诺或安慰性保证（如"你一定会好起来"）。

【语气与体例】
中立、克制、具体。像一份数据说明，而不是一份诊断书或安慰信。
若数据不足以支撑任何归纳，就直接说明数据不足，不要为了凑字数而空泛铺陈。

【输出格式】
严格返回 JSON，不要包裹代码块，不要额外解释：
{"summary": "两到三句话的整体归纳", "points": ["要点1", "要点2", "要点3"], "caveat": "一句话说明本次数据的局限"}
points 给 2-4 条，每条一句话，聚焦已采集到的指标。"""


def build_payload(snap):
    """
    从 /portrait/snapshot 的快照里，抽出【只有真实测到】的部分交给模型。

    这是整个接入层最关键的一个函数。原则：
      宁可少给，不可多给。未测量的维度连键都不出现在 payload 里，
      而不是给个 null —— 给 null 等于把"这里有个洞"这件事告诉模型，
      它很容易顺手把洞填上（这正是原先硬编码文案编出
      "疲劳指数 41""高于常模基线"的同类失误）。
    返回 (payload_dict, measured_count)
    """
    out = {}
    measured = 0

    pt = (snap or {}).get("portrait") or {}

    # ---- 五维画像：只收 value 不为 None 的维度 ----
    dims = []
    for d in (pt.get("dimensions") or []):
        if d.get("value") is None:
            continue
        item = {"名称": d.get("label"), "分值": d.get("value")}
        if d.get("higher_is_worse"):
            item["说明"] = "该指标越低越好"
        if d.get("low_term"):
            item["主要拉低项"] = d["low_term"]
        dims.append(item)
        measured += 1
    if dims:
        out["五维画像"] = dims

    comp = ((pt.get("narrative") or {}).get("facts") or {}).get("composite")
    if comp is not None:
        out["综合分"] = comp

    # ---- 情绪构成（面部）----
    # 键名以 portrait_state.put_face 实际写入的为准：emo_distribution，
    # 不是 emotions/emotion。此处曾按直觉猜错，核对固化后的快照结构才定下来。
    face = (snap or {}).get("face") or {}
    emo = face.get("emo_distribution")
    if isinstance(emo, dict) and emo:
        # 只保留数值型，按占比降序，避免把一堆 0 也送过去
        pairs = [(k, v) for k, v in emo.items()
                 if isinstance(v, (int, float))]
        if pairs:
            pairs.sort(key=lambda kv: -kv[1])
            out["情绪构成"] = {k: round(float(v), 3) for k, v in pairs[:8]}
            out["主导情绪"] = pairs[0][0]
            measured += 1

    if isinstance(face.get("emo_stability"), (int, float)):
        out["情绪稳定性"] = round(float(face["emo_stability"]), 3)

    # ---- DASS-21 自评 ----
    # 结构为 scale["scored"]["subscales"][D|A|S]，每项含 label/score/level。
    # 只有 complete 且 score 非 None 的子量表才送 —— 未答完的分数没有意义。
    scale = (snap or {}).get("scale") or {}
    subs = ((scale.get("scored") or {}).get("subscales") or {})
    keep = {}
    for _k, sub in subs.items():
        if not isinstance(sub, dict) or sub.get("score") is None:
            continue
        label = sub.get("label") or _k
        keep[label] = {"分值": sub.get("score")}
        if sub.get("level"):
            keep[label]["程度"] = sub["level"]
    if keep:
        out["量表自评"] = keep
        measured += 1

    # ---- 心率（来自 readiness）----
    hr = ((snap or {}).get("readiness") or {}).get("hr")
    if isinstance(hr, (int, float)):
        out["心率"] = hr
        measured += 1

    # ---- 明确告知哪些没测，堵住"脑补"的口子 ----
    missing = [d.get("label") for d in (pt.get("dimensions") or [])
               if d.get("value") is None and d.get("label")]
    if missing:
        out["未测量维度"] = missing

    return out, measured


def _extract_json(text):
    """
    模型偶尔会用 ```json 包裹，或在 JSON 前后带一句寒暄。
    这里做宽容解析：先直接试，失败再截取最外层花括号。
    仍失败则返回 None，由调用方决定回落。
    """
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except (ValueError, TypeError):
            return None
    return None


def _post(url, body, headers, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def interpret(snap, timeout=TIMEOUT_SEC):
    """
    生成 AI 解读。

    返回 dict：
      {"summary": str, "points": [str], "caveat": str,
       "source": "llm", "model": str, "elapsed_ms": int, "measured": int}

    抛 RuntimeError 的情形（调用方须捕获并回落到规则文案）：
      凭证缺失 / 采集数据为空 / 网络或接口失败 / 返回无法解析
    绝不返回伪造内容 —— 宁可让上层显示"暂不可用"，也不给假结论。
    """
    key, base, model = get_config()
    if key is None:
        raise RuntimeError("未配置模型 API 凭证")

    payload, measured = build_payload(snap)
    if measured == 0:
        # 一项都没测到就调用模型，只会逼它凭空写作文。
        raise RuntimeError("尚无任何已采集指标，不生成解读")

    user_msg = (
        "以下是本次测评【已实际采集】到的数据（JSON）。"
        "未出现在其中的指标即为未测量，不得推断：\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        # 温度压低：这是数据解读，不需要创造力，需要稳定复现。
        "temperature": 0.3,
        "max_tokens": 900,
        # 关掉深度思考链。原因有三：
        #   ① 本任务是"照着给定数字做描述性归纳"，不需要长链推理；
        #   ② 开启后实测会把单次调用拖到 45s+ 而超时（首版实测踩到）；
        #   ③ 推理链本身不会展示给用户，纯属白等和白花 token。
        "extra_body": {"enable_thinking": False},
    }
    headers = {
        "Authorization": "Bearer %s" % key,
        "Content-Type": "application/json",
    }
    url = base.rstrip("/") + "/chat/completions"

    t0 = time.time()
    try:
        data = _post(url, body, headers, timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        raise RuntimeError("模型接口返回 %s %s" % (e.code, detail))
    except urllib.error.URLError as e:
        raise RuntimeError("模型接口连接失败: %s" % str(e.reason)[:120])
    except Exception as e:
        raise RuntimeError("模型调用异常: %s" % str(e)[:120])

    elapsed = int((time.time() - t0) * 1000)

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("模型返回结构异常")

    parsed = _extract_json(content)
    if not isinstance(parsed, dict) or not parsed.get("summary"):
        raise RuntimeError("模型返回无法解析为预期 JSON")

    pts = parsed.get("points") or []
    if not isinstance(pts, list):
        pts = [str(pts)]
    pts = [str(p).strip() for p in pts if str(p).strip()][:4]

    return {
        "summary": str(parsed["summary"]).strip(),
        "points": pts,
        "caveat": str(parsed.get("caveat") or "").strip(),
        "source": "llm",
        "model": model,
        "elapsed_ms": elapsed,
        "measured": measured,
    }


# ---------------------------------------------------------------- 结果缓存
# 同一份数据被反复点"生成"时，没必要每次都真调模型（既慢又花钱）。
# 以 payload 的内容做键：数据没变就复用，数据一变立刻失效。

_cache_lock = threading.Lock()
_cache = {}          # key -> (ts, result)
_CACHE_TTL = 300     # 5 分钟


def interpret_cached(snap, timeout=TIMEOUT_SEC):
    payload, measured = build_payload(snap)
    if measured == 0:
        raise RuntimeError("尚无任何已采集指标，不生成解读")

    ckey = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    now = time.time()

    with _cache_lock:
        hit = _cache.get(ckey)
        if hit and (now - hit[0]) < _CACHE_TTL:
            out = dict(hit[1])
            out["cached"] = True
            return out

    res = interpret(snap, timeout=timeout)

    with _cache_lock:
        _cache[ckey] = (now, res)
        # 顺手清理过期项，避免长跑进程里无界增长
        for k in [k for k, v in _cache.items() if (now - v[0]) > _CACHE_TTL]:
            _cache.pop(k, None)

    out = dict(res)
    out["cached"] = False
    return out


def payload_fingerprint(snap):
    """
    快照中【实际参与解读】那部分数据的指纹。

    刻意复用 build_payload 而不是对整个 snap 做哈希：snap 里含
    captured_at / elapsed 之类每次都变的字段，拿它做指纹会让指纹
    永远不相等，判定「数据变没变」就完全失效了。
    build_payload 的输出只含真实测到的指标，正是解读的输入本身。

    返回 (fingerprint_str|None, measured_count)。measured==0 时返回
    None —— 没有任何指标就谈不上「解读依据」。
    """
    try:
        payload, measured = build_payload(snap)
    except Exception:
        return None, 0
    if measured == 0:
        return None, 0
    return json.dumps(payload, ensure_ascii=False, sort_keys=True), measured


def peek_cached(snap):
    """
    只查缓存，【绝不】调用模型 —— 无网络、无费用、无副作用。

    [2026-08-21 报告单] 报告页要展示已生成的 AI 解读，但「生成解读」是
    POST 且真的花钱；报告页是 GET。若报告现场补一次调用，用户每刷新
    一次报告就扣一次费，且违反 GET 无副作用的约定。
    故报告只「取用」前端已生成并缓存的结果：命中则展示，未命中则如实
    留白 —— 留白比悄悄花钱好，也比编一段占位文案好。

    [2026-08-22] 本函数受 _CACHE_TTL(5min) 限制，且进程重启即失效。
    它只是「刚生成完」的快路径；跨 TTL 的可靠来源是会话里固化的副本
    （见 portrait_state.PortraitState.put_ai）。调用方应先查会话固化，
    再回落到这里。

    返回 dict（命中）或 None（未命中 / 无法构造 payload）。
    """
    try:
        payload, measured = build_payload(snap)
        if measured == 0:
            return None
        ckey = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        return None
    now = time.time()
    with _cache_lock:
        hit = _cache.get(ckey)
        if hit and (now - hit[0]) < _CACHE_TTL:
            out = dict(hit[1])
            out["cached"] = True
            out["cached_at"] = hit[0]
            return out
    return None
