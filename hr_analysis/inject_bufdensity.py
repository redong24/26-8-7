#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复: 推理节流(MODEL_INFER_MIN_INTERVAL_SEC=2.0)引入的 rppg_plot_buffer 时间密度坍缩。

现象(实测):
  HR-DEBUG time_span 中位数 290.2s  (节流前 18.3s)
  => 135点窗口跨越 4.8 分钟, 真实点率 0.46/s
  effective_fs 兜底恒为 30.0 (真实0.46 < 下限1.0, 被 line1009 兜底改写)
  => 用 30fps 解释 0.46fps 的信号, 频率被放大约 65 倍并混叠
  生产链 raw_hr 均值 108.27, 仅 36.9% 落在 [60,100]

根因:
  每次推理只把模型输出的【最后1点】追加进 rppg_plot_buffer。
  节流前每帧推理一次 => 点率≈帧率(7.4/s), 窗口≈18s, 合理。
  节流后每2s推理一次 => 点率降到0.5/s, 但窗口长度(135点)没变,
  于是窗口在时间上被拉长 16 倍。节流只该降低"心率刷新频率",
  不该降低"波形采样密度"——这是我上一轮修改的疏漏。

修复:
  模型输出与输入帧 1:1 对齐(实测 输入(1,3,160,72,72) -> 输出(1,160))。
  改为把"自上次追加以来真正新到达的那些帧"对应的输出点全部追加,
  时间戳用 frame_buffer_timestamps 里的真实帧时刻(而非 time.time())。
  长度不对齐时回退旧行为, 保证不会比现状更差。

离线验证(tb17, 33份连续真实采集, 647.9s):
  规则A(现状,每次1点): n=63  fs=0.098  真实fs下无法计算(nan);
                        被兜底成FS=30后 HR=68.60 (虚假)
  规则B(本修复)      : n=7959 fs=12.28  末135点 span=10.46s HR=77.31
  => 窗口从 647.9s/63点 恢复为 10.5s/135点, effective_fs 恢复真实值,
     不再触发 FS=30 兜底。
"""
import re, shutil, time, hashlib, sys

SRC = '/home/lsz/real_time_plus/real_time_Demo/test2.py'
bak = SRC + '.before_bufdensity_' + time.strftime('%Y%m%d_%H%M%S')

src = open(SRC, 'rb').read().decode('utf-8')  # 二进制读, 保留CRLF/LF混排原样
shutil.copy2(SRC, bak)
print('备份 ->', bak)


def locked_blocks(text):
    """提取两个🔒锁定区块的正文, 用于前后比对必须逐字节一致"""
    out = []
    for m in re.finditer(r'# \U0001f512 \[已锁定-生产稳定\].*?START.*?\n', text):
        start = m.end()
        e = text.find('已锁定-生产稳定', start)
        e2 = text.find('\n', e) if e != -1 else -1
        out.append(text[start:e2])
    return out


before_locked = locked_blocks(src)
print('锁定区块数:', len(before_locked), '长度:', [len(x) for x in before_locked])

# ---------------------------------------------------------------- 1) 新常量
anchor_const = 'MODEL_INFER_MIN_INTERVAL_SEC = 2.0'
assert src.count(anchor_const) == 1, '常量锚点不唯一'
new_const = anchor_const + '''

# [采集帧率修复-v2-波形密度 2026-08-10] RPPG_APPEND_MAX_PER_INFER
# 每次推理最多向 rppg_plot_buffer 追加多少个新点的上限(纯安全阀)。
# 正常情况下 = 本轮真正新到达的帧数 ≈ 帧率 × 推理间隔 ≈ 12 × 2 = 24。
# 设上限是为了防止"长时间卡顿后一次性灌入整段160点"把窗口瞬间冲掉。
RPPG_APPEND_MAX_PER_INFER = 60'''
src = src.replace(anchor_const, new_const, 1)

# ---------------------------------------------------------------- 2) 会话状态
anchor_state = '        self.last_model_infer_time = 0.0'
assert src.count(anchor_state) == 1, '会话状态锚点不唯一'
src = src.replace(
    anchor_state,
    anchor_state + '''
        # [采集帧率修复-v2-波形密度] 已追加进 rppg_plot_buffer 的最后一帧的真实时刻,
        # 用于在下一次推理时只挑出"此刻之后新到达"的帧对应的输出点, 避免重复追加。
        self.last_appended_rppg_ts = 0.0''',
    1)

# ---------------------------------------------------------------- 3) 追加逻辑
old_append = """                        self._last_rppg_point_diag = new_point_val
                        self.rppg_plot_buffer.append(new_rppg_segment_points[-1])
                        self.rppg_plot_timestamps.append(time.time())"""
assert src.count(old_append) == 1, '追加逻辑锚点不唯一'

new_append = """                        self._last_rppg_point_diag = new_point_val
                        # ========================================================
                        # [采集帧率修复-v2-波形密度 2026-08-10]
                        # 原实现: 每次推理只追加输出的最后1点。在未节流时点率≈帧率,
                        # 135点窗口≈18秒, 是合理的。加上2秒推理节流后点率骤降到
                        # 0.5/s, 而窗口长度仍是135点 => 窗口在时间上被拉成约290秒
                        # (实测中位数290.2s), effective_fs 真实值0.46fps 低于
                        # 下方合理性兜底的下限1.0, 被强制改写成 CAMERA_FPS=30,
                        # 于是用30fps去解释0.46fps的信号, 频率被放大并混叠,
                        # 生产链 raw_hr 均值飙到108.27、仅36.9%落在[60,100]。
                        # 这是"节流"这一改动的副作用: 它本应只降低心率【刷新频率】,
                        # 不应降低波形【采样密度】。
                        # 修复: 模型输入输出严格1:1对齐(实测(1,3,160,72,72)->(1,160)),
                        # 因此把"自上次追加以来真正新到达的帧"所对应的输出点全部
                        # 追加进去, 时间戳直接取 frame_buffer_timestamps 中记录的
                        # 真实帧到达时刻(而不是 time.time(), 后者是推理【结束】时刻,
                        # 会把整批点错误地压在同一时刻附近)。
                        # 离线验证(tb17, 33份连续真实采集/647.9s):
                        #   旧: 63点/647.9s, fs=0.098(真实fs下算不出, 兜底成30才有值)
                        #   新: 7959点/647.9s, fs=12.28, 末135点跨度10.46s HR=77.31
                        # 长度不对齐时(理论上不会发生)回退旧行为, 保证不劣于现状。
                        # ========================================================
                        _ts_aligned = list(self.frame_buffer_timestamps)
                        if len(_ts_aligned) == len(new_rppg_segment_points):
                            _last_ts = self.last_appended_rppg_ts
                            _sel = [_i for _i, _t in enumerate(_ts_aligned) if _t > _last_ts]
                            if len(_sel) > RPPG_APPEND_MAX_PER_INFER:
                                _sel = _sel[-RPPG_APPEND_MAX_PER_INFER:]
                            for _i in _sel:
                                self.rppg_plot_buffer.append(new_rppg_segment_points[_i])
                                self.rppg_plot_timestamps.append(_ts_aligned[_i])
                            if _sel:
                                self.last_appended_rppg_ts = _ts_aligned[_sel[-1]]
                            self.rppg_appended_last = len(_sel)
                        else:
                            # 回退路径: 与修复前完全一致
                            self.rppg_plot_buffer.append(new_rppg_segment_points[-1])
                            self.rppg_plot_timestamps.append(time.time())
                            self.rppg_appended_last = -1"""
src = src.replace(old_append, new_append, 1)

# ---------------------------------------------------------------- 4) 日志可观测
old_log = ('f"raw_hr={calculated_hr} rppg_buf_len={len(self.rppg_plot_buffer)}", flush=True)')
assert src.count(old_log) == 1, '日志锚点不唯一'
src = src.replace(
    old_log,
    'f"raw_hr={calculated_hr} rppg_buf_len={len(self.rppg_plot_buffer)} "\n'
    '                              f"appended_last={getattr(self, \'rppg_appended_last\', None)}", flush=True)',
    1)

after_locked = locked_blocks(src)
assert len(before_locked) == len(after_locked), '锁定区块数量变化!'
for i, (a, b) in enumerate(zip(before_locked, after_locked)):
    if a != b:
        print(f'!! 锁定区块 {i} 被改动, 中止')
        sys.exit(1)
print('锁定区块逐字节一致 ✓', [len(x) for x in after_locked])

open(SRC, 'wb').write(src.encode('utf-8'))  # 二进制写, 不做换行符转换
print('写入完成, 新长度', len(src))
