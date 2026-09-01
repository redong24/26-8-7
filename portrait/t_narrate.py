# -*- coding: utf-8 -*-
"""
portrait_narrate 的单元测试。

刻意【不手工编造 pt 结构】，而是驱动真实的 compute_portrait 产出 pt ——
手编夹具只能证明「我的假设内部自洽」，证明不了「和后端对得上」。
五维那次故障恰恰是前端假设与后端结构不一致，而两边各自都自洽。
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '/home/lsz/webapp/portrait')
import portrait_score as S
import portrait_state as ST
import portrait_narrate as N

F = 0
def ck(name, cond, extra=''):
    global F
    if cond:
        print('  PASS  %s %s' % (name, extra))
    else:
        F += 1
        print('  FAIL  %s %s' % (name, extra))

# 数字抽取。用 (?<![\d.]) / (?![\d.]) 防止把 0.65 切成 0 和 65 ——
# 这是我早先在 LLM 校验器里踩过的假阳性，同一个坑不踩两次。
NUM_RE = re.compile(r'(?<![\d.])\d+(?:\.\d+)?(?![\d.])')

# 评价【人】而非【数据】的词。结论栏一旦出现这类词就越界了。
# 注意：'正常' 不在此列 —— 它是 DASS-21 量表自带的分档名（见
# portrait_state._level_of 的返回值），转述量表分档不是我们在评价人。
# 「把缺测说成正常」这一独立风险由 MISREAD 单独覆盖。
JUDGY = ('状态良好', '状态不佳', '你很', '您很', '心理健康', '不健康',
         '未见异常', '建议就医', '诊断', '症状', '倾向')

# 缺测被叙述成「有测量结果且结果无异常」的所有说法。
# 这是整个结论栏最危险的失效模式：用户会据此认为自己被评估过。
MISREAD = ('未计算，正常', '暂缺，正常', '正常范围', '无异常',
           '未见异常', '一切正常', '均正常')

# 正文会原样转述后端的 missing 文案，其中含字段名（AU04、
# pose_deviation_60s、DASS-21）。字段名里的数字是【标识符的一部分】，
# 不是测量结果，抽取前必须先剔除，否则 04/60 会被误判为编造。
IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

def _measure_nums(text):
    return NUM_RE.findall(IDENT_RE.sub(' ', text))

def _all_nums_whitelisted(text, facts, label):
    bad = [n for n in _measure_nums(text) if n not in facts['numbers']]
    ck('%s：正文数字全部可溯源' % label, not bad,
       '越界数字 %r / 白名单 %r' % (bad, facts['numbers']))

def _no_judgy(text, label):
    hit = [w for w in JUDGY if w in text]
    ck('%s：无评价人的措辞' % label, not hit, '命中 %r' % hit)


# ---------------------------------------------------------------- 夹具
def face_full():
    """面部数据齐备。字段名照 portrait_score 实际消费的键。"""
    return {
        'emo_stability': 0.55,
        'emo_distribution': {'neutral': 0.62, 'happy': 0.18,
                             'sad': 0.12, 'angry': 0.08},
        'emo_dominant_duration_sec': 38.0,
        'window_sec': 60.0,
        'au_intensity': {'AU04': 0.12},
        'gaze_stability': 0.70,
        'pose_deviation_60s': 0.22,
    }

def scale_full(answers_val=1):
    """21 题全填 answers_val，走真实 score_dass21。"""
    ans = {i: answers_val for i in range(1, 22)}
    return {'submitted_at': 1.0, 'answers': ans,
            'scored': ST.score_dass21(ans)}

def voice_reading():
    """含 reading 段韵律 —— 活力值需要它才能算出来。"""
    return {'prosody': {'f0_semitone_std': 3.2,
                        'rms_variation': 0.35,
                        'loudness_db_mean': -22.0}}

def build(face, voice, scale, hr):
    """按 compute_portrait 的真实入参组装，hr 走 readiness['hr']。

    blocking 按 readiness() 的真实行为填充（未完成的步骤 id），
    而不是恒为空列表 —— 恒空会绕过 composite.missing 的拼装分支，
    让「缺: face、voice」这条真实文案路径永远测不到。
    """
    blocking = ([] if face else ['face']) + \
               ([] if voice else ['voice']) + \
               ([] if scale else ['scale'])
    rd = {'ready': bool(face and voice and scale),
          'blocking': blocking,
          'hr': hr,
          'hr_available': bool(hr and hr.get('heart_rate_available'))}
    snap = {'face': face, 'voice': voice, 'scale': scale, 'readiness': rd}
    pt = S.compute_portrait(snap, rd)
    pt['readiness'] = rd
    return pt, snap

HR = {'heart_rate': 72.0, 'respiration_rate': 14.0,
      'heart_rate_available': True, 'respiration_rate_available': True}


print('=== 场景 A：三项齐备 + 心率 + reading 段（综合分应算出）===')
ptA, snA = build(face_full(), voice_reading(), scale_full(1), HR)
fA = N.facts(ptA, snA)
nA = N.narrate(ptA, snA)
print('    composite=%r  title=%r' % (fA['composite'], nA['title']))
print('    body: %s' % nA['body'])
ck('A 综合分算出（非 None）', fA['composite'] is not None,
   '得 %r' % fA['composite'])
ck('A 标题不是「待评估」', nA['title'] != '待评估', nA['title'])
ck('A 标签含综合分', any('综合' in t['text'] for t in nA['tags']),
   '%r' % nA['tags'])
_all_nums_whitelisted(nA['body'], fA, 'A')
_no_judgy(nA['body'], 'A')
ck('A 正文含心率 72.0', '72.0 bpm' in nA['body'])
ck('A 声明非临床结论', '非临床结论' in nA['body'])
ck('A 五维全部出数 -> 无暂缺', not fA['unavailable'],
   '%r' % [u['label'] for u in fA['unavailable']])


print('=== 场景 B：ask#42 实况 —— 无 reading 段，活力值应缺 ===')
ptB, snB = build(face_full(), {'prosody': {}}, scale_full(1), HR)
fB = N.facts(ptB, snB)
nB = N.narrate(ptB, snB)
print('    composite=%r' % fB['composite'])
print('    body: %s' % nB['body'])
vit = [d for d in fB['dimensions'] if d['id'] == 'vitality'][0]
ck('B 活力值为 None', vit['value'] is None, '得 %r' % vit['value'])
ck('B 活力值进入 unavailable',
   any(u['label'] == vit['label'] for u in fB['unavailable']),
   '%r' % [u['label'] for u in fB['unavailable']])
ck('B 正文写明活力值未计算',
   ('未计算' in nB['body'] and vit['label'] in nB['body']))
# 这是本文件最要紧的一条断言：缺测绝不能被叙述成「已测且无异常」。
# 注意不能简单地断言 '正常' not in body —— DASS-21 的分档名就叫「正常」，
# 那样写会在量表压力分档为正常时误报（第一版就是这么错的）。
_hit = [w for w in MISREAD if w in nB['body']]
ck('B 缺项未被叙述成「已测且无异常」', not _hit, '命中 %r' % _hit)
# 且缺项必须与「未计算」直接相邻出现，不能只在别处含糊提一句
ck('B 缺项句式明确为「未计算 + 原因」',
   '未计算，因所需数据尚未采集到' in nB['body'])
ck('B 活力值缺失不阻断综合分（它本就不计入）',
   fB['composite'] is not None, '得 %r' % fB['composite'])
ck('B 标签含「1 项暂缺」',
   any('暂缺' in t['text'] for t in nB['tags']), '%r' % nB['tags'])
_all_nums_whitelisted(nB['body'], fB, 'B')
_no_judgy(nB['body'], 'B')


print('=== 场景 C：心率缺测 —— 放松度/压力值应缺，综合分应扣留 ===')
NOHR = {'heart_rate': None, 'respiration_rate': None,
        'heart_rate_available': False, 'respiration_rate_available': False}
ptC, snC = build(face_full(), voice_reading(), scale_full(1), NOHR)
fC = N.facts(ptC, snC)
nC = N.narrate(ptC, snC)
print('    composite=%r  title=%r' % (fC['composite'], nC['title']))
print('    body: %s' % nC['body'])
ck('C 综合分扣留（None）', fC['composite'] is None, '得 %r' % fC['composite'])
ck('C 标题回落「待评估」', nC['title'] == '待评估', nC['title'])
ck('C 正文说明为何没有综合分', '综合分暂未计算' in nC['body'])
ck('C 正文点名心率', '心率' in nC['body'])
ck('C 正文无 bpm 数字（心率没测到）', 'bpm' not in nC['body'])
ck('C 已出数的维度仍然被叙述',
   any(d['value'] is not None for d in fC['dimensions'])
   and ('专注度' in nC['body'] or '情绪稳定' in nC['body']))
_all_nums_whitelisted(nC['body'], fC, 'C')
_no_judgy(nC['body'], 'C')


print('=== 场景 D：全空（未开始采集）===')
ptD, snD = build(None, None, None, None)
fD = N.facts(ptD, snD)
nD = N.narrate(ptD, snD)
print('    body: %s' % nD['body'])
ck('D 不抛异常且返回三槽位',
   set(('title', 'tags', 'body')) <= set(nD))
ck('D 综合分 None', fD['composite'] is None)
ck('D 标题「待评估」', nD['title'] == '待评估')
ck('D 五维全部列入暂缺', len(fD['unavailable']) == 5,
   '%d 项' % len(fD['unavailable']))
ck('D 每个暂缺项都带原因',
   all(u['why'] for u in fD['unavailable']),
   '%r' % [(u['label'], u['why']) for u in fD['unavailable']])
_all_nums_whitelisted(nD['body'], fD, 'D')
_no_judgy(nD['body'], 'D')


print('=== 场景 I：步骤 id 中文化（面向用户的文案不得出现标识符）===')
ptI, snI = build(face_full(), None, None, HR)
fI = N.facts(ptI, snI)
nI = N.narrate(ptI, snI)
print('    composite_missing=%r' % fI['composite_missing'])
print('    body: %s' % nI['body'])
ck('I 综合分缺失原因已中文化',
   any('语音测试' in m for m in fI['composite_missing']),
   '%r' % fI['composite_missing'])
# 面向用户的正文里绝不该出现 face/voice/scale 这类步骤 id。
# 注意后端 missing 里合法地含字段名（emo_stability 等），那是
# 「缺哪个字段」的技术说明；这里只禁三个【步骤 id】。
for _sid in ('face、', 'voice、', '缺: face', '缺: voice', '缺: scale'):
    ck('I 正文不含步骤 id 片段 %r' % _sid, _sid not in nI['body'])
ck('I 正文点名语音测试', '语音测试' in nI['body'])
ck('I 正文点名量表评估', '量表评估' in nI['body'])
_all_nums_whitelisted(nI['body'], fI, 'I')
_no_judgy(nI['body'], 'I')


print('=== 场景 E：DASS-21 转述 ===')
ptE, snE = build(face_full(), voice_reading(), scale_full(3), HR)
fE = N.facts(ptE, snE)
nE = N.narrate(ptE, snE)
print('    scale=%r' % fE['scale'])
print('    body: %s' % nE['body'])
ck('E scale 三个分量表齐全', fE['scale'] and len(fE['scale']) == 3,
   '%r' % fE['scale'])
ck('E 分量表 key 为 D/A/S',
   [r['key'] for r in fE['scale']] == ['D', 'A', 'S'])
ck('E 正文转述 DASS-21', 'DASS-21 自评' in nE['body'])
ck('E raw 值进入白名单',
   all(str(r['raw']) in fE['numbers'] for r in fE['scale']),
   '%r' % fE['numbers'])
_all_nums_whitelisted(nE['body'], fE, 'E')
# 分档名（如「极重度」）来自量表本身，是转述不是我们的判断，
# 故不参与 JUDGY 检查；但仍需确认没有「建议就医」这类越界内容。
ck('E 无诊疗建议',
   not any(w in nE['body'] for w in ('建议就医', '诊断', '治疗', '服药')))


print('=== 场景 F：白名单机制自身（LLM 回验的基础）===')
ck('F 白名单含固定名词数字 21/100',
   '21' in fA['numbers'] and '100' in fA['numbers'],
   '%r' % fA['numbers'])
ck('F 白名单里的都是字符串',
   all(isinstance(x, str) for x in fA['numbers']))
ck('F 白名单已排序（便于比对与快照）',
   fA['numbers'] == sorted(fA['numbers']))
ck('F 72.0 与 72 两种写法都在白名单',
   '72' in fA['numbers'] and '72.0' in fA['numbers'],
   '%r' % [x for x in fA['numbers'] if x.startswith('72')])
# 反向：一个绝不该在清单里的数
ck('F 编造的 88 不在白名单', '88' not in fA['numbers'])
ck('F 0.65 不会被切成 0 和 65（正则自检）',
   NUM_RE.findall('权重 0.65 占比') == ['0.65'],
   '%r' % NUM_RE.findall('权重 0.65 占比'))
ck('F bool 不被当数字（_isnum 自检）',
   N._isnum(True) is False and N._isnum(1) is True)


print('=== 场景 G：分档边界 ===')
ck('G 39.9 -> 多项指标偏低', N._band(39.9) == '多项指标偏低')
ck('G 40.0 -> 指标中等区间', N._band(40.0) == '指标中等区间')
ck('G 59.9 -> 指标中等区间', N._band(59.9) == '指标中等区间')
ck('G 60.0 -> 多项指标良好', N._band(60.0) == '多项指标良好')
ck('G 79.9 -> 多项指标良好', N._band(79.9) == '多项指标良好')
ck('G 80.0 -> 各项指标偏高', N._band(80.0) == '各项指标偏高')
ck('G 100 -> 各项指标偏高', N._band(100.0) == '各项指标偏高')
ck('G 0 -> 多项指标偏低', N._band(0.0) == '多项指标偏低')
ck('G 无档位使用评价人的词',
   not any(any(w in txt for w in ('良好状态', '健康', '优秀', '差'))
           for _, txt in N.BANDS),
   '%r' % [t for _, t in N.BANDS])


print('=== 场景 H：取整与 dim 类名一致性 ===')
ck('H _fmt(83.9) -> 84', N._fmt(83.9) == 84)
ck('H _fmt(83.4) -> 83', N._fmt(83.4) == 83)
ck('H _fmt(None) -> None', N._fmt(None) is None)
ck('H _fmt("x") -> None（脏数据不炸）', N._fmt('x') is None)
ck('H 压力值带 higher_is_worse 且正文标注「越低越好」',
   any(d['higher_is_worse'] for d in fA['dimensions'])
   and '越低越好' in nA['body'])

print()
if F:
    print('!!! %d 条断言失败' % F)
    sys.exit(1)
print('全部通过 ✔')
