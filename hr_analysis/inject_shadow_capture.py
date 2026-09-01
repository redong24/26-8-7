#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
向 test2.py 注入影子链路输入采集代码(严格只读诊断)。
按字节读写, 保留原文件混合的 CRLF/LF 行尾, 不做任何规范化。
"""
import io
import sys

PATH = "/home/lsz/real_time_plus/real_time_Demo/test2.py"

with io.open(PATH, "r", encoding="utf-8", newline="") as f:
    src = f.read()

# ---------------------------------------------------------------- 锚点 1: 计数器
ANCHOR_INIT = "        self.last_frame_capture_time = 0.0"
ADD_INIT = """
        # [临时诊断-只读, 2026-08-10] 影子链路输入采集计数器与节流时刻。
        # 用于定位"离线重放83.8 vs 线上实时93.9"的10bpm差异来源。
        # 纯统计用途, 不参与任何心率计算, 不影响 shadow_hr / current_hr_numeric。
        self.shadow_capture_count = 0
        self.last_shadow_capture_time = 0.0"""

# ---------------------------------------------------------------- 锚点 2: 采集块
ANCHOR_CAP = "                                    if shadow_points.size >= 10:"
ADD_CAP = '''                                    # ====================================================
                                    # [临时诊断-严格只读, 2026-08-10] 影子链路输入落盘
                                    # 目的: 终结"离线83.8 vs 线上93.9"这10bpm的推测。
                                    # 落盘内容足以在离线【完全确定性】复现本轮计算:
                                    #   frames   : 喂给模型的301帧(重采样后)张量
                                    #   points   : 本轮模型实际输出的rPPG波形
                                    #   prev_est : 调用compute_hr_with_tracking【之前】的
                                    #              追踪状态(该函数唯一的隐藏输入)
                                    #   ts_win   : 窗口内真实帧时间戳(未重采样)
                                    # 只读保证: 不修改 shadow_frames_u8 / shadow_points /
                                    # shadow_hr_track_est / 任何生产或影子状态; 异常全吞。
                                    # ====================================================
                                    if SHADOW_CAPTURE_ENABLED:
                                        _sc_now = time.time()
                                        if (self.shadow_capture_count < SHADOW_CAPTURE_MAX_SAMPLES
                                                and (_sc_now - self.last_shadow_capture_time)
                                                >= SHADOW_CAPTURE_MIN_INTERVAL_SEC):
                                            self.last_shadow_capture_time = _sc_now
                                            try:
                                                _sc_dir = os.path.join(
                                                    os.path.dirname(os.path.abspath(__file__)),
                                                    SHADOW_CAPTURE_DIR)
                                                os.makedirs(_sc_dir, exist_ok=True)
                                                # _ts_win 仅在门控开启时定义; 门控关闭时
                                                # 回退为空数组, 避免 NameError
                                                try:
                                                    _sc_ts = np.asarray(_ts_win,
                                                                        dtype=np.float64)
                                                except NameError:
                                                    _sc_ts = np.zeros(0, dtype=np.float64)
                                                _sc_path = os.path.join(
                                                    _sc_dir,
                                                    "shadow_%s_%03d_%d.npz" % (
                                                        self.session_id[:8],
                                                        self.shadow_capture_count,
                                                        int(_sc_now)))
                                                np.savez_compressed(
                                                    _sc_path,
                                                    frames=shadow_frames_u8.astype(np.uint8),
                                                    points=np.asarray(shadow_points,
                                                                      dtype=np.float64),
                                                    prev_est=np.float64(
                                                        self.shadow_hr_track_est),
                                                    ts_win=_sc_ts,
                                                    shadow_T=np.int64(shadow_T),
                                                    gate_n=np.int64(_gate_n),
                                                    gate_span=np.float64(_gate_span),
                                                    wall_time=np.float64(_sc_now))
                                                self.shadow_capture_count += 1
                                                print("[SHADOW-CAPTURE] session=%s 第%d/%d份 "
                                                      "T=%s 真实帧=%s prev_est=%.2f -> %s" % (
                                                          self.session_id,
                                                          self.shadow_capture_count,
                                                          SHADOW_CAPTURE_MAX_SAMPLES,
                                                          shadow_T, _gate_n,
                                                          self.shadow_hr_track_est, _sc_path),
                                                      flush=True)
                                            except Exception as _e_sc:
                                                print("[SHADOW-CAPTURE] session=%s 落盘出错"
                                                      "(不影响正常运行): %s" % (
                                                          self.session_id, _e_sc), flush=True)
'''


def inject(text, anchor, addition, after=True, label=""):
    n = text.count(anchor)
    if n != 1:
        print("[FAIL] 锚点 %s 出现 %d 次(要求恰好1次), 中止" % (label, n))
        sys.exit(1)
    if after:
        return text.replace(anchor, anchor + addition, 1)
    return text.replace(anchor, addition + anchor, 1)


# 幂等保护: 采集块已存在则拒绝重复注入
if "SHADOW-CAPTURE" in src:
    print("[SKIP] 采集块已存在, 不重复注入")
    sys.exit(2)

out = src
# 计数器可能已由先前的编辑注入, 存在则跳过
if "shadow_capture_count" in out:
    print("[SKIP] init计数器已存在, 跳过该锚点")
else:
    out = inject(out, ANCHOR_INIT, ADD_INIT, after=True, label="init计数器")

out = inject(out, ANCHOR_CAP, ADD_CAP, after=False, label="采集块")

with io.open(PATH, "w", encoding="utf-8", newline="") as f:
    f.write(out)

print("[OK] 注入完成")
print("     原始大小 %d 字节 -> 新大小 %d 字节 (+%d)" % (
    len(src), len(out), len(out) - len(src)))
