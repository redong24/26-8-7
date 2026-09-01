# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, '/tmp/ps')
import portrait_score as S

F=0
def ck(name, cond, extra=''):
    global F
    if cond: print('  PASS  %s %s' % (name, extra))
    else:
        F+=1; print('  FAIL  %s %s' % (name, extra))

print('=== 1. AU 归一化：cap=0.3 + sqrt（fix au_cap）===')
v = S.au_norm({'AU04':0.3}, 'AU04')
ck('AU04=0.3 -> 1.0', abs(v-1.0)<1e-9, '得 %.4f' % v)
v = S.au_norm({'AU04':0.075}, 'AU04')
ck('AU04=0.075 -> 0.5 (sqrt)', abs(v-0.5)<1e-9, '得 %.4f' % v)
v25 = S.au_norm({'AU25':0.3}, 'AU25')
ck('AU25 cap=0.6 例外，0.3 -> 0.707', abs(v25-0.5**0.5)<1e-9, '得 %.4f' % v25)
lin = 0.075/0.6
ck('若沿用旧 cap=0.6 会砍半：0.075/0.6=%.4f << 0.5' % lin, lin < 0.2)

print('=== 2. 语速区间（fix speech_rate_range）===')
ck('151.6 字/分(55s) 在新区间内得高分',
   S._lin(151.6,*S.SPEECH_RATE_RANGE) > 0.85,
   '得 %.3f' % S._lin(151.6,*S.SPEECH_RATE_RANGE))
ck('原区间[200,400]下 151.6 恒为 0', S._lin(151.6,200,400)==0.0)

print('=== 3. 心率 None -> 整维 None（fix hr_null_and_clip）===')
r = S.dim_relaxation({'au_intensity':{'AU04':0.05}}, {'heart_rate':None,'respiration_rate':None})
ck('放松度 value=None', r['value'] is None)
ck('missing 说明缺心率', any('心率' in m for m in r['missing']), r['missing'][:1])
r2 = S.dim_relaxation({'au_intensity':{'AU04':0.05}}, {'heart_rate':62,'respiration_rate':13})
ck('心率 62/呼吸 13 -> 放松度高', r2['value'] > 75, '得 %s' % r2['value'])
r3 = S.dim_relaxation({'au_intensity':{'AU04':0.05}}, {'heart_rate':98,'respiration_rate':23})
ck('心率 98/呼吸 23 -> 放松度低', r3['value'] < 40, '得 %s' % r3['value'])
ck('高心率 < 低心率（方向正确）', r3['value'] < r2['value'])

print('=== 4. 压力用 level_norm 不用 pct（fix use_level_norm）===')
# 键是 'S'（DASS_GROUPS 用单字母 D/A/S），不是 'stress'。
# 这正是第一版的 bug 所在，故测试夹具必须用真实键名。
def sc(ln): return {'scored':{'subscales':{'S':{'level_norm':ln,'pct':0.29}}}}
def sc_wrong(ln): return {'scored':{'subscales':{'stress':{'level_norm':ln}}}}
st_n = S.dim_stress({'au_intensity':{'AU04':0.05}}, {'heart_rate':70}, sc(0.0))
st_s = S.dim_stress({'au_intensity':{'AU04':0.05}}, {'heart_rate':70}, sc(1.0))
ck('level_norm 0 -> 低压力', st_n['value'] < 30, '得 %s' % st_n['value'])
# 自评满分但心率静息(70)、几乎不皱眉(AU04=0.05) -> 65.7，不是 100。
# 这正是三源加权该有的行为：自评单项最多贡献 50 分（权重 0.50）。
ck('level_norm 1 + 生理平静 -> 65.7（自评单项上限 50 分）',
   abs(st_s['value']-65.7)<0.05, '得 %s' % st_s['value'])
st_all = S.dim_stress({'au_intensity':{'AU04':0.3}}, {'heart_rate':100}, sc(1.0))
ck('三源全高 -> 100.0', abs(st_all['value']-100.0)<1e-6, '得 %s' % st_all['value'])
st_lo = S.dim_stress({'au_intensity':{'AU04':0.0}}, {'heart_rate':60}, sc(0.0))
ck('三源全低 -> 0.0', abs(st_lo['value']-0.0)<1e-6, '得 %s' % st_lo['value'])
ck('两者差值 = 0.50 权重 * 100 = 50',
   abs((st_s['value']-st_n['value'])-50.0)<1e-6,
   '差 %.1f' % (st_s['value']-st_n['value']))
ck('jitter/shimmer 记录在 dropped',
   any('jitter' in d['term'] for d in st_s['dropped']))
ck('higher_is_worse 标记存在', st_s.get('higher_is_worse') is True)
ck('量表未做 -> None', S.dim_stress({'au_intensity':{'AU04':0.05}},{'heart_rate':70},{})['value'] is None)
ck('回归：用错键名 stress 时压力维度为 None（这就是第一版的 bug 表现）',
   S.dim_stress({'au_intensity':{'AU04':0.05}},{'heart_rate':70},sc_wrong(1.0))['value'] is None)
ck('DASS_STRESS_KEY 常量值为 S', S.DASS_STRESS_KEY=='S')

print('=== 5. 情绪稳定只用 emo_stability（fix drop_emo_switches）===')
e = S.dim_emotion_stability({'emo_stability':0.9,'emo_dominant_duration_sec':48,'window_sec':60,'emo_switches':99})
ck('emo_switches 不在 terms 里', 'emo_switches' not in e['terms'], list(e['terms']))
ck('0.7*0.9+0.3*0.8=0.87 -> 87.0', abs(e['value']-87.0)<1e-6, '得 %s' % e['value'])
ck('AU 强度波动记录在 dropped 并说明无数据源',
   any('AU' in d['term'] and '单帧' in d['why'] for d in e['dropped']))
ck('主导时长按 window_sec 归一（20s窗15s = 0.75）',
   abs(S.dim_emotion_stability({'emo_stability':1.0,'emo_dominant_duration_sec':15,'window_sec':20})['terms']['dominant_ratio']-0.75)<1e-9)

print('=== 6. 活力值不用 loudness（fix loudness_gain_invariant）===')
vi = S.dim_vitality({'emo_distribution':{'happy':0.5,'neutral':0.5}},
                    {'prosody':{'f0_semitone_std':4.0,'rms_variation':0.5,'loudness_db_mean':-7.27}})
ck('loudness_db_mean 不在 terms', 'loudness_db_mean' not in vi['terms'], list(vi['terms']))
ck('loudness 记录在 dropped 并给出 -7.27 证据',
   any('7.27' in d['why'] for d in vi['dropped']))
ck('rms_variation 参与计算', vi['terms']['rms_var'] is not None)
ck('正向情绪占比 0.5', abs(vi['terms']['positive_emo']-0.5)<1e-9)
ck('exploratory=True', vi.get('exploratory') is True)
ck('exclude_from_composite=True', vi.get('exclude_from_composite') is True)

print('=== 7. 专注度不用被污染的 attention ===')
fo = S.dim_focus({'gaze_stability':0.8,'pose_deviation_60s':0.1})
ck('0.6*0.8+0.4*0.9=0.84 -> 84.0', abs(fo['value']-84.0)<1e-6, '得 %s' % fo['value'])
ck('attention 记录在 dropped', any('attention' in d['term'] for d in fo['dropped']))

print('=== 8. 综合分门控 ===')
snap_full = {
  'face': {'emo_stability':0.9,'emo_dominant_duration_sec':48,'window_sec':60,
           'emo_distribution':{'happy':0.5,'neutral':0.5},
           'gaze_stability':0.8,'pose_deviation_60s':0.1,
           'au_intensity':{'AU04':0.05}},
  'voice': {'prosody':{'f0_semitone_std':4.0,'rms_variation':0.5}},
  'scale': sc(0.25),
  'readiness': {'ready':True,'blocking':[],'hr':{'heart_rate':70,'respiration_rate':14}},
}
out = S.compute_portrait(snap_full)
ck('5 个维度齐全', len(out['dimensions'])==5)
ck('全部有值', all(d['value'] is not None for d in out['dimensions']),
   [ (d['id'],d['value']) for d in out['dimensions'] ])
ck('综合分有值', out['composite']['value'] is not None, '得 %s' % out['composite']['value'])
ck('gated=False', out['gated'] is False)
vals = {d['id']:d['value'] for d in out['dimensions']}
exp = round((vals['emotion_stability']+vals['relaxation']+vals['focus']+(100-vals['stress']))/4,1)
ck('综合分 = 四维等权(压力取反) = %s' % exp, abs(out['composite']['value']-exp)<1e-6)
ck('活力值不在 included', 'vitality' not in out['composite']['included'])

print('--- 未齐备时 ---')
snap_partial = dict(snap_full)
snap_partial['readiness'] = {'ready':False,'blocking':['voice'],'hr':{'heart_rate':70,'respiration_rate':14}}
o2 = S.compute_portrait(snap_partial)
ck('综合分为 None', o2['composite']['value'] is None)
ck('gated=True', o2['gated'] is True)
ck('missing 写明缺 voice', any('voice' in m for m in o2['composite']['missing']), o2['composite']['missing'])
ck('各维度仍然计算（用户能看到进度）',
   sum(1 for d in o2['dimensions'] if d['value'] is not None)==5)

print('--- 空快照不崩 ---')
o3 = S.compute_portrait({})
ck('返回完整骨架', len(o3['dimensions'])==5 and o3['composite']['value'] is None)
ck('全维 None 且都有 missing 说明',
   all(d['value'] is None and d['missing'] for d in o3['dimensions']))
ck('None 参数不崩', S.compute_portrait(None)['dimensions'] is not None)
ck('JSON 可序列化', bool(json.dumps(out, ensure_ascii=False)))

print('=== 9. 脏数据鲁棒性 ===')
ck('心率字符串 "0" 经 _num 得 0，但 _norm_hr 已在上游转 None；此处 0 不应被当放松',
   S._lin(0,*S.HR_RELAX_RANGE)==0.0)
ck('NaN -> None', S._num(float('nan')) is None)
ck('Inf -> None', S._num(float('inf')) is None)
ck('bool 不当数字', S._num(True) is None)
ck('非数字串 -> None', S._num('abc') is None)
ck('au_intensity 非 dict -> None', S.au_norm(None,'AU04') is None)
ck('emo_distribution 全 0 -> None + missing',
   S.dim_vitality({'emo_distribution':{'happy':0}},{'prosody':{}})['terms']['positive_emo'] is None)
ck('超界裁剪 gaze_stability=1.5 -> 1.0',
   S.dim_focus({'gaze_stability':1.5,'pose_deviation_60s':0})['value']==100.0)

print()
print('全部通过 ✔' if F==0 else '失败 %d 项 ✘' % F)
sys.exit(1 if F else 0)
