# -*- coding: utf-8 -*-
"""
features_handcrafted.py 冒烟测试
================================
用「已知真值」的合成信号验证特征提取，而不只是验证「没崩」。

测试用例：
  A. 持续元音 @48kHz  —— 已知 F0=150Hz，jitter/shimmer 应 reliable=True
  B. 带停顿的语音串   —— 已知 3 段发声 + 2 段 0.6s 停顿，验证节奏检测
  C. 持续元音 @16kHz  —— 采样率不足，jitter 应 reliable=False 且给出原因
  D. 0.2s 极短音频    —— 应返回 usable=False，不崩
  E. 纯静音           —— VAD 全 False 的边界
  F. JSON 序列化      —— numpy 类型 / nan 泄漏会在这里暴露
"""
import json
import sys

import numpy as np

sys.path.insert(0, "/home/lsz/audio_service")
import features_handcrafted as F


def synth_vowel(f0=150.0, sr=48000, dur=3.0, jitter_pct=0.0,
                noise_db=-40.0, seed=0):
    """
    合成类元音信号：基频 + 谐波（模拟声带源），可注入真实的周期长度抖动。
    用逐周期拼接生成，这样 jitter 是真实的周期扰动而非频率调制
    （后者 Praat 测不出 jitter）。
    """
    rng = np.random.default_rng(seed)
    out = []
    got = 0
    total = int(sr * dur)
    # 谐波幅度：模拟元音 /a/ 的频谱倾斜
    harm = {1: 1.0, 2: 0.6, 3: 0.4, 4: 0.35, 5: 0.2, 6: 0.12, 7: 0.08}
    while got < total:
        t0 = 1.0 / f0
        if jitter_pct > 0:
            t0 *= 1.0 + rng.normal(0, jitter_pct / 100.0)
        n = max(4, int(round(t0 * sr)))
        t = np.arange(n) / sr
        f_cyc = 1.0 / (n / sr)          # 本周期实际基频
        cyc = np.zeros(n)
        for k, a in harm.items():
            cyc += a * np.sin(2 * np.pi * k * f_cyc * t)
        out.append(cyc)
        got += n
    y = np.concatenate(out)[:total]
    y /= np.max(np.abs(y)) + 1e-12
    amp = 10 ** (noise_db / 20.0)        # 按目标 SNR 加噪
    y = y + rng.normal(0, amp, y.size)
    return (y / (np.max(np.abs(y)) + 1e-12)).astype(np.float64)


def synth_speechlike(sr=48000, seg_dur=1.5, pause_dur=0.6,
                     n_seg=3, f0=140.0, seed=1):
    """发声段 + 静音段交替，用于验证停顿检测。首尾各加 0.4s 静音（应被掐掉）。"""
    rng = np.random.default_rng(seed)
    parts = []
    for i in range(n_seg):
        v = synth_vowel(f0=f0 + i * 5, sr=sr, dur=seg_dur,
                        noise_db=-35.0, seed=seed + i)
        # 幅度包络起伏，让它更像语音而非稳态音
        env = 0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 4.0 *
                                        np.arange(v.size) / sr))
        parts.append(v * env)
        if i < n_seg - 1:
            parts.append(rng.normal(0, 1e-4, int(sr * pause_dur)))
    head = rng.normal(0, 1e-4, int(sr * 0.4))
    tail = rng.normal(0, 1e-4, int(sr * 0.4))
    y = np.concatenate([head] + parts + [tail])
    return (y / (np.max(np.abs(y)) + 1e-12)).astype(np.float64)


# ------------------------------------------------------------------ 断言工具
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name}"
          + (f"  -> {detail}" if detail else ""))


def near(a, b, tol):
    return a is not None and abs(a - b) <= tol


def json_safe(obj, path="root"):
    """递归找出不可序列化 / numpy 残留 / nan 的字段。"""
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                bad.append(f"{path}.{k} 键非字符串({type(k).__name__})")
            bad += json_safe(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            bad += json_safe(v, f"{path}[{i}]")
    elif obj is None or isinstance(obj, (str, bool, int, float)):
        if type(obj).__module__ == "numpy":
            bad.append(f"{path} 是 numpy 类型 {type(obj).__name__}")
        elif isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
            bad.append(f"{path} = {obj}（nan/inf 泄漏）")
    else:
        bad.append(f"{path} 类型不可序列化: "
                   f"{type(obj).__module__}.{type(obj).__name__}")
    return bad


def main():
    print("=" * 68)
    print("features_handcrafted.py 冒烟测试")
    print("=" * 68)

    # ---------------------------------------------------------- A
    print("\n【A】持续元音 48kHz / F0=150Hz / 3s / jitter 0.3%")
    ya = synth_vowel(f0=150.0, sr=48000, dur=3.0, jitter_pct=0.3, noise_db=-45)
    ra = F.extract_all(ya, 48000, is_sustained_vowel=True)
    print(json.dumps(ra.get("meta", {}), ensure_ascii=False))
    print("pitch:", json.dumps(ra.get("pitch", {}), ensure_ascii=False))
    print("vq   :", json.dumps(ra.get("voice_quality", {}), ensure_ascii=False))
    check("A.usable", ra["meta"].get("usable") is True)
    f0m = ra.get("pitch", {}).get("f0_mean")
    check("A.F0 恢复到 150+-8Hz", near(f0m, 150.0, 8.0), f"f0_mean={f0m}")
    vq = ra.get("voice_quality", {})
    check("A.jitter reliable=True", vq.get("reliable") is True,
          f"reasons={vq.get('unreliable_reasons')}")
    check("A.jitter 有数值", vq.get("jitter_local") is not None,
          f"jitter_local={vq.get('jitter_local')}")
    hnr_keys = [k for k in vq if "hnr" in k.lower()]
    check("A.HNR 有数值",
          any(vq.get(k) is not None for k in hnr_keys), f"hnr_keys={hnr_keys}")
    # 持续元音上 VAD-SNR 结构上不可测，必须拒给值并附说明，
    # 而不是返回 0dB 这种看似精确实际无意义的数字。
    check("A.稳态信号上拒给 VAD-SNR",
          ra["rhythm"].get("snr_db") is None
          and bool(ra["rhythm"].get("snr_note")),
          f"snr_db={ra['rhythm'].get('snr_db')} "
          f"note={ra['rhythm'].get('snr_note')}")
    n_mfcc = len([k for k in ra.get("spectral", {}) if k.startswith("mfcc")])
    check("A.MFCC 13 维齐全(26 个键)", n_mfcc == 26, f"mfcc 键数={n_mfcc}")

    # ---------------------------------------------------------- A2
    print("\n【A2】强噪持续元音 48kHz（HNR 门限应拦下）")
    ya2 = synth_vowel(f0=150.0, sr=48000, dur=3.0, jitter_pct=0.3, noise_db=-6)
    ra2 = F.extract_all(ya2, 48000, is_sustained_vowel=True)
    vq2 = ra2.get("voice_quality", {})
    print("vq:", json.dumps(vq2, ensure_ascii=False))
    check("A2.强噪下 reliable=False", vq2.get("reliable") is False,
          f"hnr={vq2.get('hnr_db')} reasons={vq2.get('unreliable_reasons')}")
    check("A2.原因含谐噪比",
          any("谐噪比" in r for r in vq2.get("unreliable_reasons", [])),
          f"reasons={vq2.get('unreliable_reasons')}")

    # ---------------------------------------------------------- B
    print("\n【B】3 段发声(1.5s) + 2 段停顿(0.6s) + 首尾静音(0.4s)")
    yb = synth_speechlike(sr=48000, seg_dur=1.5, pause_dur=0.6, n_seg=3)
    rb = F.extract_all(yb, 48000, is_sustained_vowel=False, text_char_count=60)
    print("rhythm:", json.dumps(rb.get("rhythm", {}), ensure_ascii=False, indent=2))
    rh = rb.get("rhythm", {})
    check("B.总时长约 6.3s", near(rh.get("duration_sec"), 6.3, 0.4),
          f"duration={rh.get('duration_sec')}")
    check("B.检出 2 段停顿", rh.get("pause_count") == 2,
          f"pause_count={rh.get('pause_count')}")
    check("B.停顿总长约 1.2s", near(rh.get("pause_total_sec"), 1.2, 0.35),
          f"pause_total={rh.get('pause_total_sec')}")
    check("B.有效语音>=3s 故 reliable", rh.get("reliable") is True,
          f"speech_sec={rh.get('speech_sec')}")
    check("B.语速已计算(给了字数)", rh.get("speech_rate_cpm") is not None,
          f"cpm={rh.get('speech_rate_cpm')}")
    check("B.jitter reliable=False(非持续元音)",
          rb["voice_quality"].get("reliable") is False,
          f"reasons={rb['voice_quality'].get('unreliable_reasons')}")

    rb2 = F.extract_all(yb, 48000, text_char_count=None)
    check("B.不给字数则语速为 None",
          rb2["rhythm"].get("speech_rate_cpm") is None)

    # ---------------------------------------------------------- C
    print("\n【C】持续元音 16kHz（采样率不足 44100）")
    yc = synth_vowel(f0=150.0, sr=16000, dur=3.0, jitter_pct=0.3, noise_db=-45)
    rc = F.extract_all(yc, 16000, is_sustained_vowel=True)
    vqc = rc.get("voice_quality", {})
    print("vq:", json.dumps(vqc, ensure_ascii=False, indent=2))
    check("C.jitter reliable=False", vqc.get("reliable") is False)
    check("C.原因含采样率",
          any("采样率" in r for r in vqc.get("unreliable_reasons", [])),
          f"reasons={vqc.get('unreliable_reasons')}")
    # 注意：按模块设计契约，不可靠时【仍返回数值】+ reliable=False，
    # 由上层决定是否采用（"由上层决定是否采用"）。故此处断言的是
    # 「数值与标记同时存在」，而不是「数值被抹成 None」。
    check("C.不可靠但仍给出数值(契约要求)",
          vqc.get("jitter_local") is not None and vqc.get("reliable") is False,
          f"jitter_local={vqc.get('jitter_local')}")
    check("C.16k 下 F0 仍恢复", near(rc["pitch"].get("f0_mean"), 150.0, 8.0),
          f"f0_mean={rc['pitch'].get('f0_mean')}")

    # ---------------------------------------------------------- D
    print("\n【D】0.2s 极短音频")
    yd = synth_vowel(f0=150.0, sr=48000, dur=0.2)
    rd = F.extract_all(yd, 48000)
    print(json.dumps(rd, ensure_ascii=False, indent=2))
    check("D.usable=False", rd["meta"].get("usable") is False)
    check("D.给出原因", bool(rd["meta"].get("reason")))
    check("D.不含特征组", "pitch" not in rd and "rhythm" not in rd)

    # ---------------------------------------------------------- E
    # 回归测试：麦克风静音 / 未拾音。
    # 曾有缺陷——峰值归一化把 -86dBFS 底噪放大到满幅，VAD 把纯噪声
    # 判成 100/100 语音帧，输出一整套「看起来正常」的特征。
    # 这是最危险的失败模式：界面上会显示完整结果，用户无从察觉。
    print("\n【E】纯静音 3s（麦克风未拾音，回归测试）")
    ye = (np.random.default_rng(9).normal(0, 1e-5, 48000 * 3)).astype(np.float64)
    re_ = F.extract_all(ye, 48000)
    print(json.dumps(re_.get("meta", {}), ensure_ascii=False, indent=2))
    check("E.usable=False（不得产出假数据）",
          re_["meta"].get("usable") is False,
          f"peak={re_['meta'].get('peak_dbfs')}dBFS")
    check("E.原因提示麦克风",
          "麦克风" in (re_["meta"].get("reason") or ""),
          f"reason={re_['meta'].get('reason')}")
    check("E.不返回特征组",
          all(k not in re_ for k in ("pitch", "rhythm", "spectral")),
          f"keys={list(re_.keys())}")
    check("E.记录了真实电平", re_["meta"].get("peak_dbfs") is not None)

    # ---------------------------------------------------------- E2
    print("\n【E2】正常电平语音必须仍然通过电平门限（防误杀）")
    y_quiet = synth_speechlike(sr=48000) * 0.05      # -26dBFS 左右，偏轻但正常
    re2 = F.extract_all(y_quiet, 48000, text_char_count=60)
    print("meta:", json.dumps(re2.get("meta", {}), ensure_ascii=False))
    check("E2.轻声语音未被误杀", re2["meta"].get("usable") is True,
          f"peak={re2['meta'].get('peak_dbfs')}dBFS "
          f"reason={re2['meta'].get('reason')}")
    check("E2.仍检出停顿", re2.get("rhythm", {}).get("pause_count") == 2,
          f"pause_count={re2.get('rhythm', {}).get('pause_count')}")

    # ---------------------------------------------------------- F
    print("\n【F】JSON 序列化 / numpy 泄漏检查")
    for tag, r in (("A", ra), ("B", rb), ("C", rc), ("D", rd),
                   ("E", re_), ("E2", re2), ("A2", ra2)):
        bad = json_safe(r, tag)
        check(f"F.{tag} 无 numpy/nan 泄漏", not bad,
              "; ".join(bad[:4]) if bad else "")
        try:
            json.dumps(r, ensure_ascii=False, allow_nan=False)
            check(f"F.{tag} json.dumps(allow_nan=False)", True)
        except Exception as e:
            check(f"F.{tag} json.dumps(allow_nan=False)", False, str(e)[:120])

    # ---------------------------------------------------------- 汇总
    print("\n" + "=" * 68)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
