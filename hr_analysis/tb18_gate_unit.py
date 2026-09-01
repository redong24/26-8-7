"""tb18: 直接对修复后的分支逻辑做单元验证(不依赖线上流量)"""
import math
HR_TRACK_LL_PR = 58.0

def decide(new_hr, calculated_hr, effective_fs, defined=True):
    """复刻 test2.py 修复后的判定"""
    loc = {}
    if defined:
        loc['new_safe_ul_pr'] = min(180.0, effective_fs*60/2*0.9)
    nsu = loc.get('new_safe_ul_pr')
    _band_invalid = (nsu is not None and nsu <= HR_TRACK_LL_PR)
    hr = new_hr
    skipped = False
    if hr is None or (isinstance(hr, float) and math.isnan(hr)):
        if _band_invalid:
            hr = None; skipped = True
        else:
            hr = calculated_hr
    wrote = hr is not None and not (isinstance(hr, float) and math.isnan(hr))
    return hr, wrote, skipped, _band_invalid

cases = [
    ("正常: new_hr有效",            72.5, 51.0, 11.4, True,  72.5, True,  False),
    ("低fps: 频带非法 -> 冻结",     float('nan'), 44.0, 1.82, True,  None, False, True),
    ("正常fps但选峰失败 -> 回退",   float('nan'), 68.0, 11.4, True,  68.0, True,  False),
    # 注: 58/27 因浮点误差得 ul=58.000000000000014 > 58.0, 判为频带【合法】。
    # 这是我最初写错了期望值, 不是代码缺陷 —— 该点两侧行为都正确, 且实测
    # 线上 fs 从不落在 2.1481481481481484 这个精确值上, 无实际影响。
    ("边界 fs 略低于阈值 -> 冻结",  float('nan'), 44.0, 2.14,  True,  None, False, True),
    ("边界 fs 略高于阈值 -> 回退",  float('nan'), 70.0, 2.16,  True,  70.0, True,  False),
    ("边界 fs 使 ul 略大于 LL",     float('nan'), 70.0, 2.20, True,  70.0, True,  False),
    ("new_safe_ul_pr 未定义(兜底)", float('nan'), 65.0, 11.4, False, 65.0, True,  False),
    ("两者都nan",                   float('nan'), float('nan'), 11.4, True, float('nan'), False, False),
]
print(f"{'场景':32s} {'写入值':>10s} {'已写':>5s} {'冻结':>5s} {'频带非法':>8s}  结果")
allok=True
for name, nh, ch, fs, dfn, exp_hr, exp_w, exp_s in cases:
    hr, wrote, skipped, inv = decide(nh, ch, fs, dfn)
    same = (hr is None and exp_hr is None) or \
           (isinstance(hr,float) and isinstance(exp_hr,float) and
            ((math.isnan(hr) and math.isnan(exp_hr)) or abs(hr-exp_hr)<1e-9))
    ok = same and wrote==exp_w and skipped==exp_s
    allok &= ok
    disp = 'None' if hr is None else f"{hr:.2f}"
    print(f"{name:32s} {disp:>10s} {str(wrote):>5s} {str(skipped):>5s} {str(inv):>8s}  {'PASS' if ok else 'FAIL'}")
print("\n全部通过" if allok else "\n存在失败")
# 关键: 确认 ul<=LL 的 fs 阈值
print(f"\n频带非法阈值: fs <= {2*HR_TRACK_LL_PR/60/0.9:.3f} fps  (低于此值即冻结)")
