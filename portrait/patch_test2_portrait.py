# -*- coding: utf-8 -*-
"""
patch_test2_portrait.py —— 把画像快照层挂载到生产 test2.py
==============================================================================
沿用 patch_test2_audio.py 的安全范式（幂等 / 锚点唯一性 / ast 校验 /
自动备份 / 首页 md5 保护 / --check / --revert）。

为什么必须改 test2.py：portrait_state 需要 rppg_app.get_session 和
Flask app 实例，这两个只存在于 test2.py 里。改动被压到 3 处纯增量插入，
不修改任何既有语句 —— rPPG 主链路（upload_frame / video_feed / 心率计算）
零改动。
==============================================================================
"""
from __future__ import annotations
import ast, hashlib, os, shutil, sys, time

APP_DIR = "/home/lsz/real_time_plus/real_time_Demo"
TARGET = os.path.join(APP_DIR, "test2.py")
HOME_TPL = os.path.join(APP_DIR, "templates", "index.html")
HOME_MD5_EXPECT = "a6f582c049f1a5e86662d36e2184983d"
MARK = "[画像快照层 2026-08-13]"

SRC = "/home/lsz/webapp/portrait/portrait_state.py"
DST = os.path.join(APP_DIR, "portrait_state.py")

P1_ANCHOR = """try:
    import audio_client
    _AUDIO_IMPORT_OK = True"""
P1_NEW = """try:
    import portrait_state as _portrait_state
    _PORTRAIT_IMPORT_OK = True
    _PORTRAIT_IMPORT_ERR = None
except Exception as _e_portrait_import:
    _portrait_state = None
    _PORTRAIT_IMPORT_OK = False
    _PORTRAIT_IMPORT_ERR = str(_e_portrait_import)
    print("[PORTRAIT] portrait_state 导入失败，画像功能不可用"
          "（不影响rPPG/音频）: " + _PORTRAIT_IMPORT_ERR, flush=True)

try:
    import audio_client
    _AUDIO_IMPORT_OK = True"""

P2_ANCHOR = """        audio_client.register_routes(app, rppg_app.get_session)"""
P2_NEW = """        audio_client.register_routes(app, rppg_app.get_session)
        pass  # __PORTRAIT_ROUTES_BELOW__"""

P3_ANCHOR = """        # {mark_audio} 音频分析状态，由 audio_client 懒创建后挂在此处。""".format(
    mark_audio="[音频输入层 2026-08-12]")
P3_NEW = """        # {mark} 画像快照仓，由 portrait_state 懒创建后挂在此处。
        # 存在意义：面部数据是 60s 滚动窗口（deque maxlen=900），
        # 用户去答量表期间会被挤掉；量表答案原本只在浏览器内存里。
        # 三份数据不共存，故需要这一层把易失的实时值固化成快照。
        self.portrait_state = None

        # {mark_audio} 音频分析状态，由 audio_client 懒创建后挂在此处。""".format(
    mark=MARK, mark_audio="[音频输入层 2026-08-12]")

PATCHES = [
    ("1/3 导入 portrait_state", P1_ANCHOR, P1_NEW),
    ("2/3 注册画像路由占位", P2_ANCHOR, P2_NEW),
    ("3/3 会话声明 portrait_state", P3_ANCHOR, P3_NEW),
]


def md5_of(p):
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def fail(m):
    print("❌ " + m)
    sys.exit(1)


def guard_homepage():
    if not os.path.isfile(HOME_TPL):
        fail("找不到首页模板: %s" % HOME_TPL)
    got = md5_of(HOME_TPL)
    if got != HOME_MD5_EXPECT:
        fail("首页模板 md5 与预期不符，已中止。\n  期望: %s\n  实际: %s"
             % (HOME_MD5_EXPECT, got))
    print("✅ 闸门1 首页模板 md5 一致（未被改动）: %s" % got)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    guard_homepage()
    src = open(TARGET, encoding="utf-8").read()

    if mode == "--check":
        print("✅ 闸门2 目标文件: %s (%d 字节)" % (TARGET, len(src)))
        print("   已打补丁: %s" % ("是" if MARK in src else "否"))
        for name, anchor, _ in PATCHES:
            print("   锚点 %-26s 出现 %d 次" % (name, src.count(anchor)))
        print("   portrait_state.py 已就位: %s" % os.path.isfile(DST))
        return

    if mode == "--revert":
        if MARK not in src:
            print("ℹ️  未打过补丁，无需回滚")
            return
        new = src
        for name, anchor, patched in PATCHES:
            if patched in new:
                new = new.replace(patched, anchor)
        new = new.replace("\n        pass  # __PORTRAIT_ROUTES_BELOW__", "")
        ast.parse(new)
        bak = TARGET + ".bak_portrait_revert_" + time.strftime("%Y-%m-%d_%H%M%S")
        shutil.copy2(TARGET, bak)
        open(TARGET, "w", encoding="utf-8").write(new)
        print("✅ 已回滚，备份: %s" % bak)
        return

    if MARK in src:
        print("ℹ️  已打过补丁（幂等），无改动")
        return

    # 闸门3：锚点唯一性
    for name, anchor, _ in PATCHES:
        c = src.count(anchor)
        if c != 1:
            fail("锚点【%s】出现 %d 次（应为 1 次），拒绝猜测位置" % (name, c))
    print("✅ 闸门3 三个锚点均唯一")

    new = src
    for name, anchor, patched in PATCHES:
        new = new.replace(anchor, patched, 1)

    # 把占位替换成真正的注册代码（放在 audio 注册之后、同一 try 内）
    reg = """        if _PORTRAIT_IMPORT_OK:
            _portrait_state.register_routes(app, rppg_app.get_session)
            print("[PORTRAIT] 画像路由已注册: /portrait/face /portrait/scale "
                  "/portrait/readiness /portrait/snapshot /portrait/reset",
                  flush=True)"""
    new = new.replace("        pass  # __PORTRAIT_ROUTES_BELOW__", reg, 1)

    # 闸门4：语法校验
    try:
        ast.parse(new)
    except SyntaxError as e:
        fail("补丁后语法错误，未落盘: %s" % e)
    print("✅ 闸门4 ast 语法校验通过")

    # 闸门5：复制模块到生产目录
    shutil.copy2(SRC, DST)
    ast.parse(open(DST, encoding="utf-8").read())
    print("✅ 闸门5 portrait_state.py 已复制到生产目录并通过语法校验")

    bak = TARGET + ".bak_portrait_" + time.strftime("%Y-%m-%d_%H%M%S")
    shutil.copy2(TARGET, bak)
    open(TARGET, "w", encoding="utf-8").write(new)
    print("✅ 补丁已落盘。备份: %s" % bak)
    print("   新增路由需重载 gunicorn 生效（kill -HUP <master>）")


if __name__ == "__main__":
    main()
