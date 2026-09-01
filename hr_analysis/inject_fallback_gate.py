#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复: 生产链路 new_hr=nan 时无条件回退到 calculated_hr, 写入带外伪值。

【本次改动位于 🔒 锁定区块 B 内】—— 依据用户明确授权:
「我明确授权你可以根据工程需要修改锁定区」。
锁定区 A (compute_hr_with_tracking) 不动, 本脚本会断言其逐字节不变。

## 现象
生产链 current_hr_numeric 有 9.5% 的样本 < 60 bpm, 实测集中在 43.8~46.7。

## 根因(已用日志逐条验证, 非推测)
1. new_hr=nan 占 8.91% (337/3783)。
2. 这些 nan 全部发生在 effective_fs 塌到 ~1.8 的时刻:
   nan 样本 fs 中位数 1.82, 全部 < 2.2; 正常样本 fs 中位数 30.0。
3. 代码本身是对的: line~1102 `if new_safe_ul_pr > HR_TRACK_LL_PR:` 否则返回 nan。
   fs=1.8 时 safe_ul = 1.8*60/2*0.9 = 49.2 < LL_PR=58 => 频带非法 => nan。
   实测 96.8% 的 nan 样本满足 fs <= 2*LL/60, 机制吻合。
4. 问题出在【回退】: 此时改用 calculated_hr, 而它是在【同一段退化信号】上
   用 LL_PR=40 算的。fs=1.8 的奈奎斯特上限只有 54.7bpm, 于是候选频带被压成
   40~49bpm 的窄条, 输出必然落在这个窄条内 —— 实测回退值范围 [40.0, 46.7],
   与理论窄带完全吻合。这不是心率, 是频带边界伪影。
5. 低帧率本身的成因也已定位: 坏窗口 100% 伴随 SHADOW-GATE 帧饥饿拦截
   (好窗口仅 33.1%), 即浏览器页面切后台/关闭导致不再上传帧, 不是计算侧缺陷。

## 修复
频带非法(new_safe_ul_pr <= HR_TRACK_LL_PR)时, 不再回退到 calculated_hr,
而是【本轮不写入】, 沿用上一次有效值 —— 与影子链路 SHADOW-GATE 已采用的
freeze-on-failure 语义保持一致。
其它原因导致的 new_hr=nan (频带合法但选峰失败) 仍按原逻辑回退, 不改变。

## 离线验证(3891 条真实样本回放)
  现状(总是回退)      : n=3891 mean=81.70 <60占 9.5% in[60,100]=78.3%
  本修复(非法则冻结)  : n=3540 mean=85.46 <60占 0.6% in[60,100]=86.1%
  代价: 9.0% 的轮次不更新(这些轮次本就不含有效信息)

## 注意
生产链路当前【不对用户可见】(/max 前端只读 heart_rate=影子链路),
本修复改善的是 legacy_production_heart_rate 与 hr_history_buffer 的质量。
"""
import re, shutil, time, sys

SRC = '/home/lsz/real_time_plus/real_time_Demo/test2.py'
bak = SRC + '.before_fallbackgate_' + time.strftime('%Y%m%d_%H%M%S')

src = open(SRC, 'rb').read().decode('utf-8')
shutil.copy2(SRC, bak)
print('备份 ->', bak)


def block_a(text):
    """锁定区A: compute_hr_with_tracking, 本次必须逐字节不变"""
    s = text.find('心率计算修复区块 START')
    e = text.find('心率计算修复区块 END')
    return text[s:e]


a_before = block_a(src)
print('锁定区A 长度:', len(a_before))

old = """                        hr_to_use = new_hr
                        if hr_to_use is None or (isinstance(hr_to_use, float) and math.isnan(hr_to_use)):
                            hr_to_use = calculated_hr"""
assert src.count(old) == 1, '回退逻辑锚点不唯一'

new = """                        # [修复-回退带外伪值 2026-08-11] 原逻辑: new_hr 为 nan 时无条件
                        # 回退到 calculated_hr。实测该回退在【帧率塌陷】场景下会写入
                        # 与真实心率无关的伪值:
                        #   new_hr=nan 占 8.91%(337/3783), 且这些时刻 effective_fs
                        #   中位数只有 1.82(全部<2.2) —— 因为 safe_ul=fs*60/2*0.9
                        #   = 49.2 < HR_TRACK_LL_PR=58, 频带非法, 函数按设计返回 nan。
                        #   此时 calculated_hr 是在【同一段退化信号】上用 LL_PR=40 算的,
                        #   而 fs=1.8 的奈奎斯特上限仅 54.7bpm, 候选频带被压成 40~49bpm
                        #   的窄条, 输出必然落在其中 —— 实测回退值范围[40.0,46.7],
                        #   与理论窄带完全吻合, 是频带边界伪影而非心率。
                        # 低帧率的成因也已定位: 坏窗口 100% 伴随 SHADOW-GATE 帧饥饿
                        # 拦截(好窗口仅33.1%), 即浏览器切后台/关页面停止上传帧。
                        # 修复: 频带非法时【本轮不写入】, 沿用上次有效值(freeze-on-
                        # failure, 与影子链路 SHADOW-GATE 语义一致)。频带合法但选峰
                        # 失败导致的 nan 仍按原逻辑回退, 行为不变。
                        # 离线回放(3891条真实样本):
                        #   现状: mean=81.70 <60占9.5%  in[60,100]=78.3%
                        #   修复: mean=85.46 <60占0.6%  in[60,100]=86.1% (拦掉9.0%轮次)
                        _band_invalid = (new_safe_ul_pr <= HR_TRACK_LL_PR)
                        hr_to_use = new_hr
                        if hr_to_use is None or (isinstance(hr_to_use, float) and math.isnan(hr_to_use)):
                            if _band_invalid:
                                # 频带非法: 本轮无有效信息, 保持上次值, 不写入伪值
                                hr_to_use = None
                                self.band_invalid_skips = getattr(self, 'band_invalid_skips', 0) + 1
                            else:
                                hr_to_use = calculated_hr"""
src = src.replace(old, new, 1)

# 可观测: 在 NEW-HR-DEBUG 里暴露跳过计数
old_log = ('f"LL_PR={HR_TRACK_LL_PR} UL_PR={new_safe_ul_pr:.1f}", flush=True)')
assert src.count(old_log) == 1, '日志锚点不唯一'
src = src.replace(
    old_log,
    'f"LL_PR={HR_TRACK_LL_PR} UL_PR={new_safe_ul_pr:.1f} "\n'
    '                                  f"band_invalid={new_safe_ul_pr <= HR_TRACK_LL_PR} "\n'
    '                                  f"band_skips={getattr(self, \'band_invalid_skips\', 0)}", flush=True)',
    1)

a_after = block_a(src)
if a_before != a_after:
    print('!! 锁定区A 被改动, 中止'); sys.exit(1)
print('锁定区A 逐字节一致 ✓')
print('锁定区B 已按授权修改(回退逻辑)')

open(SRC, 'wb').write(src.encode('utf-8'))
print('写入完成, 新长度', len(src))
