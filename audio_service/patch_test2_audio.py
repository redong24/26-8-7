# -*- coding: utf-8 -*-
"""
patch_test2_audio.py —— 把音频输入层挂载到生产 test2.py
==============================================================================
为什么用脚本而不是手改
----------------------
test2.py 是 2477 行的生产文件，承载已通过 15 轮验证的 rPPG 主链路。
手工编辑不可复现、不可回滚、也无法核对"到底改了哪几行"。
本脚本把改动固化为 3 个精确锚点替换，具备：

  * 幂等      —— 已打过补丁则原样退出，重复执行安全
  * 锚点唯一性校验 —— 锚点缺失或出现多次立即中止，绝不猜位置
  * 语法校验  —— 写盘前用 ast.parse 验证结果，语法错误则不落盘
  * 自动备份  —— 落盘前留带时间戳的备份
  * 首页保护  —— 校验 templates/index.html md5，与用户"不动首页"的约束一致
  * --check / --revert 模式

用法
----
  python patch_test2_audio.py --check     # 只报告当前状态，不改动
  python patch_test2_audio.py             # 打补丁
  python patch_test2_audio.py --revert    # 回滚（移除补丁）
==============================================================================
"""
from __future__ import annotations

import ast
import hashlib
import os
import shutil
import sys
import time

APP_DIR = "/home/lsz/real_time_plus/real_time_Demo"
TARGET = os.path.join(APP_DIR, "test2.py")
HOME_TPL = os.path.join(APP_DIR, "templates", "index.html")
HOME_MD5_EXPECT = "a6f582c049f1a5e86662d36e2184983d"

MARK = "[音频输入层 2026-08-12]"      # 幂等判定标记


# ------------------------------------------------------------------ 补丁定义
# 每项：(说明, 锚点原文, 替换后文本)
# 锚点都选取足够长、在文件中唯一的片段，避免误匹配。

P1_ANCHOR = """from scipy.interpolate import interp1d
import tensorflow as tf
"""

P1_NEW = """from scipy.interpolate import interp1d
import tensorflow as tf

# ==================================================================
# {mark} 心理综合评估的第 4 路原始输入（音频分析）。
# 实现全部在 audio_client.py 中，本文件只做挂载，不改动 rPPG 主链路。
# 用 try 包裹的原因：音频是新增的旁路功能，无论它 import 失败还是
# 5003 微服务没起来，都不允许影响 rPPG 主链路的启动 —— 主链路已经过
# 多轮验证，不能因为一个附加模块而变得更容易起不来。
# ==================================================================
try:
    import audio_client
    _AUDIO_IMPORT_OK = True
    _AUDIO_IMPORT_ERR = None
except Exception as _e_audio_import:
    audio_client = None
    _AUDIO_IMPORT_OK = False
    _AUDIO_IMPORT_ERR = str(_e_audio_import)
    print("[AUDIO] audio_client 导入失败，音频功能不可用（不影响rPPG）: "
          + _AUDIO_IMPORT_ERR, flush=True)
""".format(mark=MARK)


P2_ANCHOR = """rppg_app = RPGGApplication(argparse.Namespace(**config))
"""

P2_NEW = """rppg_app = RPGGApplication(argparse.Namespace(**config))

# ------------------------------------------------------------------
# {mark} 注册音频路由。
# 必须放在 rppg_app 创建之后 —— 音频路由要用 rppg_app.get_session
# 复用既有会话（cookie session_id），不另建一套会话体系。
# 新增：/audio/task_spec  /audio/health  /audio/upload  /audio/result
# 既有路由（/ /jc /max /max_inner /v2 /upload_frame ...）零改动。
# ------------------------------------------------------------------
if _AUDIO_IMPORT_OK:
    try:
        audio_client.register_routes(app, rppg_app.get_session)
        print("[AUDIO] 音频路由已注册: /audio/task_spec /audio/health "
              "/audio/upload /audio/result", flush=True)
    except Exception as _e_audio_route:
        print("[AUDIO] 音频路由注册失败，音频功能不可用（不影响rPPG）: "
              + str(_e_audio_route), flush=True)
""".format(mark=MARK)


# 第 3 处：ClientSession.__init__ 里显式声明 audio_state。
# audio_client 本身是懒创建的，这行严格来说不是必需的；
# 加上是为了让"会话上挂了什么"在类定义处可见，而不是散落在别处。
P3_ANCHOR = """    def __init__(self, session_id, args, shared_fonts, model, detector):
        self.session_id = session_id
"""

P3_NEW = """    def __init__(self, session_id, args, shared_fonts, model, detector):
        self.session_id = session_id
        # {mark} 音频分析状态，由 audio_client 懒创建后挂在此处。
        # 这里显式置 None，只为让"会话上挂了哪些东西"在类定义处一目了然。
        self.audio_state = None
""".format(mark=MARK)


PATCHES = [
    ("1/3 导入 audio_client", P1_ANCHOR, P1_NEW),
    ("2/3 注册音频路由", P2_ANCHOR, P2_NEW),
    ("3/3 会话声明 audio_state", P3_ANCHOR, P3_NEW),
]


def md5_of(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def fail(msg: str) -> None:
    print("❌ " + msg)
    sys.exit(1)


def guard_homepage() -> None:
    """首页保护闸门：用户的硬约束是"不要动现在的首页布局和所有元素"。"""
    if not os.path.isfile(HOME_TPL):
        fail("找不到首页模板: %s" % HOME_TPL)
    got = md5_of(HOME_TPL)
    if got != HOME_MD5_EXPECT:
        fail("首页模板 md5 与预期不符，已中止。\n"
             "  期望: %s\n  实际: %s\n"
             "本补丁不应改动首页；md5 不符说明环境与预期不一致，"
             "需先人工确认再继续。" % (HOME_MD5_EXPECT, got))
    print("✅ 闸门1 首页模板 md5 一致（未被改动）: %s" % got)


def load() -> str:
    if not os.path.isfile(TARGET):
        fail("找不到目标文件: %s" % TARGET)
    with open(TARGET, "r", encoding="utf-8") as f:
        return f.read()


def already_patched(src: str) -> bool:
    return MARK in src


def check_anchors(src: str) -> None:
    """锚点唯一性校验：不唯一就说明位置有歧义，宁可中止也不猜。"""
    for name, anchor, _ in PATCHES:
        n = src.count(anchor)
        if n == 0:
            fail("锚点缺失（%s）。生产文件结构与预期不符，已中止。" % name)
        if n > 1:
            fail("锚点出现 %d 次（%s），位置有歧义，已中止。" % (n, name))
    print("✅ 闸门2 三处锚点均唯一存在")


def apply_patches(src: str) -> str:
    out = src
    for name, anchor, new in PATCHES:
        out = out.replace(anchor, new, 1)
        print("   ✓ %s" % name)
    return out


def verify_syntax(code: str) -> None:
    try:
        ast.parse(code)
    except SyntaxError as e:
        fail("补丁结果语法错误（未写盘）: 第%s行 %s" % (e.lineno, e.msg))
    print("✅ 闸门3 补丁结果 ast.parse 通过")


def verify_untouched(old: str, new: str) -> None:
    """
    确认只做了"新增"，没有删除任何既有代码行。
    做法：原文件的每一行都应仍出现在新文件中（按多重集计数不减少）。
    这能挡住"锚点替换时不小心吃掉原有内容"这类错误。
    """
    from collections import Counter
    co, cn = Counter(old.splitlines()), Counter(new.splitlines())
    lost = {ln: co[ln] - cn[ln] for ln in co if cn[ln] < co[ln]}
    if lost:
        sample = list(lost.items())[:5]
        fail("检测到原有代码行减少，补丁可能吃掉了既有逻辑，已中止。\n"
             "  示例: %s" % sample)
    added = sum(cn.values()) - sum(co.values())
    print("✅ 闸门4 原有代码行零丢失，净新增 %d 行" % added)


def backup() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = "%s.bak_audiopatch_%s" % (TARGET, ts)
    shutil.copy2(TARGET, dst)
    print("✅ 闸门5 已备份: %s" % dst)
    return dst


def write(code: str) -> None:
    tmp = TARGET + ".tmp_audiopatch"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(code)
    os.replace(tmp, TARGET)      # 原子替换，避免写一半被 gunicorn 读到
    print("✅ 已写入: %s (md5 %s)" % (TARGET, md5_of(TARGET)))


def do_check() -> None:
    src = load()
    print("目标文件: %s" % TARGET)
    print("当前 md5: %s   行数: %d" % (md5_of(TARGET), len(src.splitlines())))
    if already_patched(src):
        print("状态: ✅ 已打补丁")
        for name, _, _ in PATCHES:
            pass
        print("音频路由应存在: /audio/task_spec /audio/health "
              "/audio/upload /audio/result")
    else:
        print("状态: ⬜ 未打补丁")
        for name, anchor, _ in PATCHES:
            print("   锚点 %s: 出现 %d 次" % (name, src.count(anchor)))
    guard_homepage()


def do_revert() -> None:
    src = load()
    if not already_patched(src):
        print("未打补丁，无需回滚。")
        return
    guard_homepage()
    out = src
    for name, anchor, new in PATCHES:
        if new not in out:
            fail("回滚失败：补丁块 %s 已被人工改动，请用备份文件手工恢复。" % name)
        out = out.replace(new, anchor, 1)
        print("   ✓ 回滚 %s" % name)
    verify_syntax(out)
    if MARK in out:
        fail("回滚后仍残留补丁标记，已中止。")
    backup()
    write(out)
    print("\n回滚完成。需重启 gunicorn 生效。")


def do_patch() -> None:
    src = load()
    if already_patched(src):
        print("✅ 已打过补丁（幂等），无需重复执行。")
        print("   当前 md5: %s" % md5_of(TARGET))
        return

    guard_homepage()
    check_anchors(src)

    # 补丁前先确认 audio_client.py 已在同目录，否则打完补丁也 import 不到
    cli = os.path.join(APP_DIR, "audio_client.py")
    if not os.path.isfile(cli):
        fail("audio_client.py 不在 %s。\n"
             "  请先执行: bash /home/lsz/webapp/audio_service/"
             "sync_audio_service.sh push" % APP_DIR)
    print("✅ 闸门0 audio_client.py 已就位 (md5 %s)" % md5_of(cli))

    out = apply_patches(src)
    verify_syntax(out)
    verify_untouched(src, out)
    backup()
    write(out)

    print("\n补丁完成。新增路由：")
    print("  GET  /audio/task_spec")
    print("  GET  /audio/health")
    print("  POST /audio/upload?stage=vowel|reading")
    print("  GET  /audio/result")
    print("\n⚠ 需重启 gunicorn 才会生效（Python 不热加载已 import 的模块）。")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--check":
        do_check()
    elif arg == "--revert":
        do_revert()
    elif arg in ("", "--patch"):
        do_patch()
    else:
        print(__doc__)
        sys.exit(2)
