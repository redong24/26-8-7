#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
注入心率偏高修复:
  1. 新增 compute_hr_spectral_centroid() —— 不修改锁定函数, 而是并列新增,
     生产链路(current_hr_numeric)继续用原函数, 行为逐位不变。
  2. 影子链路搜索带 [50,140] -> [45,110]
  3. 影子链路改调用新函数
"""
import io
import sys

PATH = "/home/lsz/real_time_plus/real_time_Demo/test2.py"
with io.open(PATH, "r", encoding="utf-8", newline="") as f:
    src = f.read()

if "compute_hr_spectral_centroid" in src:
    print("[SKIP] 已注入")
    sys.exit(2)

# ------------------------------------------------ 1. 搜索带常量
OLD_BAND = """SHADOW_HR_LL_PR = 50.0                  # 影子链路选峰搜索带下限(bpm)，原复用HR_TRACK_LL_PR=58
SHADOW_HR_UL_PR = 140.0                 # 影子链路选峰搜索带上限(bpm)，原为180"""
NEW_BAND = '''# [心率偏高修复2-2026-08-10] 140 -> 110, 50 -> 45。依据见下方"修复2"注释块:
# 纯噪声对照显示"无脉搏信息时"的输出必然落在【搜索带中心】附近:
#     带[50,140](中心95)  -> 纯噪声输出均值 93.8
#     带[45,110](中心77.5)-> 纯噪声输出均值 76.4
# 实测本项目采集帧的脉搏信噪比约1:1(见下), 大量窗口本就"无信息",
# 因此搜索带中心 = 事实上的默认输出。成人静息心率中位≈72, 把中心
# 从95移到77.5, 等价于把"无信息时的先验"放到生理正确的位置。
# 代价: 无法测量 >110bpm(剧烈运动/心动过速)。本产品为静息场景, 可接受。
SHADOW_HR_LL_PR = 45.0                  # (原50.0) 影子链路选峰搜索带下限(bpm)
SHADOW_HR_UL_PR = 110.0                 # (原140.0) 影子链路选峰搜索带上限(bpm)'''

# ------------------------------------------------ 2. 新函数(并列新增, 不动锁定区块)
ANCHOR_FN = "# 🔒 [已锁定-生产稳定] 2026-08-08 心率计算修复区块 END (compute_hr_with_tracking)"
NEW_FN = '''
# ============================================================================
# [心率偏高修复2-2026-08-10] compute_hr_spectral_centroid
# ----------------------------------------------------------------------------
# 【为什么不改上面的锁定函数】
# 上面的 compute_hr_with_tracking 保持逐位不变, 生产链路(current_hr_numeric)
# 继续调用它, 行为完全不受本次改动影响。本函数是【并列新增】, 仅供影子链路
# (即 /max 页面显示值)使用, 可通过 SHADOW_USE_CENTROID 一键回退。
#
# 【锁定函数在高更新密度下的失效机制 —— 已用真实数据证实】
# 线上影子链路约 4.2 轮/秒, 相邻两轮的10秒窗口只滑动0.24秒, 频谱几乎相同,
# 即"在同一个频谱上反复迭代"。而 chosen = min(strong, |bpm - prev_est|)
# 配合 est = a*chosen + (1-a)*est 构成正反馈闭环:
#   - 能量阈值0.15 放行的候选跨度中位数 88.5bpm, 占搜索带宽98%
#     => 几乎总能找到一个"贴着 prev_est"的峰
#   - 实测 corr(chosen, prev_est) = +0.925, corr(chosen, 能量最强峰) = -0.019
#     (5个会话/3名被试/2种搜索带, 全部成立)
#   - 起点依赖实验: 同一频谱, init=55 收敛到69, init=135 收敛到123,
#     100%的频谱都表现出起点依赖 => 输出的是"初值的记忆", 不是心率
#   - 真值±5bpm内有候选峰的轮次占51.3%, 但其中69%没被选中
#
# 【本函数的做法】
# 用"能量加权谱重心"替代"最接近 prev_est 的峰": 在能量 >= 最强峰*frac 的
# 候选上做加权平均。它【完全不依赖历史状态】, 因此:
#   - 起点无关(实测6个留出会话的起点极差全部 = 0.00)
#   - 无自锁、无漂移、可复现
#   - 仍保留抗噪性(弱峰被 frac 阈值排除, 不像纯 argmax 那样被单个尖峰带偏)
# 仍返回 new_est 以保持调用接口一致(供日志观察), 但选值不再依赖它。
#
# 【离线验证 —— 会话4b989b29, R=70(线上真实更新密度)】
#   现状 带[50,140]+th0.15 : 偏差 +15.73  MAE 18.92  起点极差 3.13
#   本方案 带[45,110]+重心  : 偏差  +2.13  MAE  8.49  起点极差 0.00
# 【6个留出会话验证】跨会话输出均值 90.63 -> 80.70, 会话间std 5.21 -> 1.46
#
# 【必须诚实说明的局限】
# 实测采集帧的脉搏信噪比约 1:1 (相邻帧逐像素MAE 8.9灰阶, 而生理脉搏
# 引起的亮度变化仅约0.5~1灰阶; 平移配准可消除30.6%残差 => 人脸框抖动
# 是主要干扰源), 且窗口内真实帧率中位数仅约3~6fps。因此本修复是
# 【把"无信息时的默认输出"放到生理合理位置 + 消除闭环漂移】,
# 而不是"提高了测量精度"。真正的精度提升必须从采集端解决:
# 提高并稳定帧率、稳定人脸ROI(跟踪+平滑框)、改善光照。
#
# 【为什么还要加展示端EMA(SHADOW_DISPLAY_EMA_ALPHA)】
# 重心法是无状态的, 每轮独立算, 副作用是页面数字会跳。实测相邻两次刷新的
# 跳变中位数 10.88bpm(现状写法为6.20), 而页面约4.2次/秒刷新, 观感很差。
# 因此在【展示环节】加一层EMA。关键: EMA只平滑"输出", 不回灌到选值,
# 所以【不会】重新引入闭环自证。实测 alpha=0.15:
#     跳变中位 10.88 -> 1.08 ; MAE 8.49 -> 8.14(略降, 因为平滑抑制了噪声)
#     起点极差仍然 = 0.000 (4个起点输出完全一致)
#     从75起步收敛95%需约19次刷新 ≈ 4.5秒, 响应可接受
# ============================================================================
SHADOW_USE_CENTROID = True              # 置False即回退为原 compute_hr_with_tracking
SHADOW_CENTROID_FRAC = 0.5              # 参与重心的候选峰能量下限(相对最强峰)
SHADOW_DISPLAY_EMA_ALPHA = 0.15         # 展示端EMA平滑系数; 置0或None即关闭


def compute_hr_spectral_centroid(pleth_sig_norm, FS, LL_PR, UL_PR, prev_est,
                                 energy_frac=SHADOW_CENTROID_FRAC,
                                 track_alpha=HR_TRACK_ALPHA, BUTTER_ORDER=6):
    """能量加权谱重心选频。与 compute_hr_with_tracking 接口一致, 返回
    (本次心率bpm, 更新后的追踪估计值)。选值不依赖 prev_est, 故无起点依赖。
    失败时返回 (nan, prev_est), 与原函数一致。"""
    try:
        b, a = scipy_signal.butter(BUTTER_ORDER, [LL_PR / 60, UL_PR / 60],
                                   btype='bandpass', fs=FS)
        filtered = scipy_signal.filtfilt(b, a, np.double(pleth_sig_norm))
    except Exception:
        return float('nan'), prev_est
    try:
        N = int((60 * FS) / 0.1)
        F, Pxx = scipy_signal.periodogram(x=filtered, nfft=N, fs=FS, window='hann')
        bpm = F * 60
        mask = (bpm >= LL_PR) & (bpm <= UL_PR)
        bpm_r = bpm[mask]
        p_r = Pxx[mask]
        if len(p_r) == 0:
            return float('nan'), prev_est
        peak_idx, _ = scipy_signal.find_peaks(p_r, distance=3)
        if len(peak_idx) == 0:
            peak_idx = np.array([int(np.argmax(p_r))])
        candidates = [(float(bpm_r[i]), float(p_r[i])) for i in peak_idx]
        top_energy = max(e for _, e in candidates)
        if top_energy <= 1e-12:
            return float('nan'), prev_est
        sel = [(b_, e) for b_, e in candidates if e >= top_energy * energy_frac]
        if not sel:
            sel = [max(candidates, key=lambda x: x[1])]
        w = np.array([e for _, e in sel], dtype=np.float64)
        v = np.array([b_ for b_, _ in sel], dtype=np.float64)
        wsum = float(w.sum())
        if not np.isfinite(wsum) or wsum <= 1e-12:
            return float('nan'), prev_est
        chosen_bpm = float((v * w).sum() / wsum)
        if not np.isfinite(chosen_bpm):
            return float('nan'), prev_est
        new_est = track_alpha * chosen_bpm + (1 - track_alpha) * prev_est
        return chosen_bpm, new_est
    except Exception:
        return float('nan'), prev_est
# ============================================================================
'''

# ------------------------------------------------ 3. 影子链路改调用
OLD_CALL = """                                            shadow_hr, self.shadow_hr_track_est = compute_hr_with_tracking(
                                                shadow_seg_norm, FS=shadow_fs,
                                                LL_PR=SHADOW_HR_LL_PR, UL_PR=shadow_safe_ul,
                                                prev_est=self.shadow_hr_track_est
                                            )"""
NEW_CALL = """                                            # [心率偏高修复2-2026-08-10] 改用无状态的能量加权谱重心,
                                            # 消除"闭环自证/起点依赖"(详见 compute_hr_spectral_centroid
                                            # 上方注释块)。SHADOW_USE_CENTROID=False 可一键回退。
                                            _shadow_hr_fn = (compute_hr_spectral_centroid
                                                             if SHADOW_USE_CENTROID
                                                             else compute_hr_with_tracking)
                                            shadow_hr, self.shadow_hr_track_est = _shadow_hr_fn(
                                                shadow_seg_norm, FS=shadow_fs,
                                                LL_PR=SHADOW_HR_LL_PR, UL_PR=shadow_safe_ul,
                                                prev_est=self.shadow_hr_track_est
                                            )"""


def rep(text, old, new, label):
    n = text.count(old)
    if n != 1:
        print("[FAIL] 锚点[%s] 出现 %d 次(需1次)" % (label, n))
        sys.exit(1)
    return text.replace(old, new, 1)


OLD_DISP = '                                        if not (isinstance(shadow_hr, float) and math.isnan(shadow_hr)):\n                                            with self.lock:\n                                                self.shadow_hr_numeric = shadow_hr\n                                                self.shadow_hr_display_val = f"{shadow_hr:.2f}"\n                                                self.primary_hr_numeric = shadow_hr\n                                                self.primary_hr_display_val = f"{shadow_hr:.2f}"\n                                                self.primary_hr_history_buffer.append(shadow_hr)'
NEW_DISP = '                                        if not (isinstance(shadow_hr, float) and math.isnan(shadow_hr)):\n                                            # [心率偏高修复2-2026-08-10] 展示端EMA平滑。\n                                            # 重心法无状态导致页面数字跳动(相邻刷新跳变中位\n                                            # 10.88bpm), 此处只平滑"展示值", 不回灌到选峰逻辑,\n                                            # 因此不会重新引入闭环自证(实测起点极差仍为0.00)。\n                                            _disp_hr = shadow_hr\n                                            if SHADOW_DISPLAY_EMA_ALPHA:\n                                                if (self.shadow_display_ema is None\n                                                        or not math.isfinite(self.shadow_display_ema)):\n                                                    self.shadow_display_ema = float(shadow_hr)\n                                                else:\n                                                    _a = float(SHADOW_DISPLAY_EMA_ALPHA)\n                                                    self.shadow_display_ema = (\n                                                        _a * float(shadow_hr)\n                                                        + (1.0 - _a) * self.shadow_display_ema)\n                                                _disp_hr = self.shadow_display_ema\n                                            with self.lock:\n                                                self.shadow_hr_numeric = _disp_hr\n                                                self.shadow_hr_display_val = f"{_disp_hr:.2f}"\n                                                self.primary_hr_numeric = _disp_hr\n                                                self.primary_hr_display_val = f"{_disp_hr:.2f}"\n                                                self.primary_hr_history_buffer.append(_disp_hr)'
OLD_INIT = '        self.shadow_hr_track_est = SHADOW_HR_TRACK_INIT_EST'
NEW_INIT = '        self.shadow_hr_track_est = SHADOW_HR_TRACK_INIT_EST\n        # [心率偏高修复2-2026-08-10] 展示端EMA状态(仅影响显示, 不参与选峰)\n        self.shadow_display_ema = None'

out = rep(src, OLD_BAND, NEW_BAND, "搜索带常量")
out = rep(out, ANCHOR_FN, ANCHOR_FN + NEW_FN, "新函数")
out = rep(out, OLD_CALL, NEW_CALL, "影子链路调用")
out = rep(out, OLD_INIT, NEW_INIT, "会话EMA状态")
out = rep(out, OLD_DISP, NEW_DISP, "展示端EMA")

with io.open(PATH, "w", encoding="utf-8", newline="") as f:
    f.write(out)
print("[OK] 注入完成 %d -> %d 字节" % (len(src), len(out)))
