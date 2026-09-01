# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0,'/home/lsz/webapp/portrait')
import portrait_state as P
F=0
def ck(n,c,e=''):
    global F
    print(('  PASS  ' if c else '  FAIL  ')+n+' '+str(e))
    if not c: F+=1

class FakeAudio:
    def __init__(self, done): self.done=done
    def snapshot(self): return {'completed': list(self.done)}
    def merged(self):
        # 忠实模拟 audio_client.merged()：prosody 只来自 reading 段，
        # 未做朗读就没有 f0/rms。夹具不照着真实行为写，
        # 测出来的「通过」是假的。
        if 'reading' not in self.done:
            return {'prosody': {}, 'usable': False}
        return {'prosody':{'f0_semitone_std':4.0,'rms_variation':0.5},'usable':True}

ANS = {str(i+1): (i%4) for i in range(21)}   # JSON 上来的键是字符串，模拟真实请求
FACE = {'status':'ok','window_sec':60,'emo_stability':0.9,
  'emo_dominant_duration_sec':48,'emo_distribution':{'happy':0.5,'neutral':0.5},
  'gaze_stability':0.8,'pose_deviation_60s':0.1,
  'au_intensity':{'AU04':0.05},'pose':{'yaw':0.1,'pitch':-0.2}}

print('=== 1. 空态骨架 ===')
st0 = P.PortraitState(); s0 = P.snapshot(st0, None)
ck('snapshot 含 portrait', 'portrait' in s0)
ck('composite=None', s0['portrait']['composite']['value'] is None)
ck('gated=True', s0['portrait']['gated'] is True)
ck('5 维骨架齐全', len(s0['portrait']['dimensions'])==5)
ck('formula_status 已定稿', '已定稿' in s0['readiness']['formula_status'])
ck('formula_fix_count=7', s0['readiness']['formula_fix_count']==7)
ck('formula_open_count=2', s0['readiness']['formula_open_count']==2)

print('=== 2. 三项齐备（走真实 put_* 路径，键为字符串）===')
st = P.PortraitState()
ok,msg = st.put_face(FACE, hr_text='70', resp_text='14'); ck('put_face', ok, msg)
ok2,msg2 = st.put_scale(ANS);                             ck('put_scale', ok2, msg2)
ck('量表 complete=True', st.scale['scored']['complete'] is True)
ck('压力分量表键为 S', 'S' in st.scale['scored']['subscales'])
s1 = P.snapshot(st, FakeAudio(['vowel','reading'])); pt = s1['portrait']
ck('ready=True', s1['readiness']['ready'] is True, s1['readiness']['blocking'])
ck('gated=False', pt['gated'] is False)
for d in pt['dimensions']:
    ck('维度 %-18s = %-6s' % (d['id'], d['value']), d['value'] is not None, d['missing'])
ck('综合分有值', pt['composite']['value'] is not None, pt['composite']['value'])
vals={d['id']:d['value'] for d in pt['dimensions']}
exp=round((vals['emotion_stability']+vals['relaxation']+vals['focus']+(100-vals['stress']))/4,1)
ck('综合分 = 四维等权(压力取反) = %s' % exp, abs(pt['composite']['value']-exp)<1e-6)
ck('活力值 exploratory', [d for d in pt['dimensions'] if d['id']=='vitality'][0]['exploratory'] is True)
ck('活力值不在 included', 'vitality' not in pt['composite']['included'])
ck('压力 higher_is_worse', [d for d in pt['dimensions'] if d['id']=='stress'][0]['higher_is_worse'] is True)
ck('spec_version', pt['spec_version']=='3b-2026-08-13')
ck('JSON 可序列化', bool(json.dumps(s1, ensure_ascii=False)))

print('=== 3. 心率哨兵 "0" 的隔离性 ===')
st2 = P.PortraitState()
st2.put_face(FACE, hr_text='0', resp_text='0')   # "0" = 尚未测到
st2.put_scale(ANS)
p2 = P.snapshot(st2, FakeAudio(['vowel','reading']))['portrait']
dv = {d['id']:d for d in p2['dimensions']}
ck('放松度 None（不当成深度放松）', dv['relaxation']['value'] is None)
ck('压力 None（不当成零压力）', dv['stress']['value'] is None)
ck('专注度仍有值', dv['focus']['value'] is not None, dv['focus']['value'])
ck('情绪稳定仍有值', dv['emotion_stability']['value'] is not None, dv['emotion_stability']['value'])
ck('活力值仍有值', dv['vitality']['value'] is not None, dv['vitality']['value'])
ck('ready=True 但综合分 None（不重分配权重）', p2['composite']['value'] is None)
ck('missing 指名哪一维不可用', len(p2['composite']['missing'])==2, p2['composite']['missing'])

print('=== 4. 缺语音时 ===')
p3 = P.snapshot(st, FakeAudio(['vowel']))['portrait']
ck('gated=True', p3['gated'] is True)
ck('综合分 None', p3['composite']['value'] is None)
dv3 = {d['id']:d for d in p3['dimensions']}
ck('活力值 None（缺 f0/rms）', dv3['vitality']['value'] is None, dv3['vitality']['missing'])
ck('面部三维仍有值',
   all(dv3[k]['value'] is not None for k in ('emotion_stability','focus','relaxation')))

print('=== 5. 计分模块缺失降级 ===')
sv=P._score; P._score=None
try:
    s4=P.snapshot(st, FakeAudio(['vowel','reading']))
    ck('快照本身仍可用', s4['face'] is not None and s4['readiness']['ready'] is True)
    ck('portrait 带 error', 'error' in s4['portrait'], s4['portrait'].get('error'))
    ck('formula_status 反映未加载', '未加载' in s4['readiness']['formula_status'])
finally: P._score=sv

print('=== 6. 契约：计分层的键与 DASS_GROUPS 一致 ===')
import portrait_score as S
ck('DASS_STRESS_KEY 在 DASS_GROUPS 里', S.DASS_STRESS_KEY in P.DASS_GROUPS)
ck('且其 label 是「压力」', P.DASS_GROUPS[S.DASS_STRESS_KEY]['label']=='压力')
ck('FORMULA_SPEC.weights 与计分层同一对象', P.FORMULA_SPEC['weights'] is S.WEIGHTS)
ck('两项 open_question 均已 resolved',
   all(q.get('resolved') for q in P.FORMULA_OPEN_QUESTIONS))
print()
print('全部通过 ✔' if F==0 else '失败 %d 项 ✘' % F)
sys.exit(1 if F else 0)
