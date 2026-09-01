# -*- coding: utf-8 -*-
"""
用 duktape 真跑 paintConclusion，验证核心结论栏的渲染结果。

与 t_dom.py 同一策略，但更进一步：喂进 JS 的 JSON 不是手写的，
而是【真实 compute_portrait + 真实 narrate】的输出。这样这个文件
覆盖的是整条链路 —— 后端算分 → 后端文案 → 前端渲染。
任一环节的字段名对不上，这里就会挂。

要回答的具体问题：给定后端真实返回，那个「— / 100」大圆圈、
进度环、定性标题、标签行、结论正文，到底显示成什么。
"""
import io, re, json, sys, dukpy
sys.path.insert(0, '/home/lsz/webapp/portrait')
import portrait_score as S
import portrait_state as ST
import portrait_narrate as N

VIEW = '/home/lsz/webapp/static/portrait_view.js'
view = io.open(VIEW, encoding='utf-8').read()

# 抠出 paintConclusion 与它依赖的 RING_LEN 常量
m = re.search(r'\n      var RING_LEN = [\d.]+;.*?\n      function paintConclusion\(pt\) \{.*?\n      \}\n',
              view, re.S)
assert m, '未定位到 paintConclusion（含 RING_LEN）'
fn = m.group(0)

F = 0
def ck(name, cond, extra=''):
    global F
    if cond:
        print('  PASS  %s %s' % (name, extra))
    else:
        F += 1
        print('  FAIL  %s %s' % (name, extra))


STUB = r"""
var NUM  = {textContent:'\u2014'};
var RING = {_off:'263.9',
            setAttribute:function(k,v){ if(k==='stroke-dashoffset') this._off=v; }};
var TIT  = {textContent:'待评估', className:'title pv3-placeholder'};
var TAGS = {_kids:[], innerHTML:'x',
            appendChild:function(el){ this._kids.push(el); }};
var BODY = {textContent:'占位', className:'pv3-placeholder'};

/* innerHTML='' 必须真的清空子节点：真实 DOM 会清，桩若不清，
   「标签行重复堆积」这个 bug 就永远测不出来。 */
Object.defineProperty(TAGS, 'innerHTML', {
  set:function(v){ if(v==='') this._kids=[]; },
  get:function(){ return ''; }
});

var document = {
  createElement:function(t){ return {tag:t, className:'', textContent:''}; }
};

var root = {
  querySelector:function(s){
    if (s.indexOf('score-num')>=0)       return NUM;
    if (s.indexOf('score-ring')>=0)      return RING;
    if (s.indexOf('score-title')>=0)     return TIT;
    if (s.indexOf('score-tags')>=0)      return TAGS;
    if (s.indexOf('conclusion-body')>=0) return BODY;
    return null;
  }
};
"""

TAIL = r"""
paintConclusion(PT);
JSON.stringify({
  num:  String(NUM.textContent),
  ring: String(RING._off),
  title:{t:TIT.textContent, c:TIT.className},
  tags: TAGS._kids.map(function(k){ return {t:k.textContent, c:k.className}; }),
  body: {t:BODY.textContent, c:BODY.className}
});
"""

def render(pt):
    src = STUB + "var PT=" + json.dumps(pt, ensure_ascii=False) + ";\n" + fn + TAIL
    return json.loads(dukpy.evaljs(src))


# --------------------------------------------------- 真实后端产出
def face_full():
    return {'emo_stability': 0.55,
            'emo_distribution': {'neutral': 0.62, 'happy': 0.18,
                                 'sad': 0.12, 'angry': 0.08},
            'emo_dominant_duration_sec': 38.0, 'window_sec': 60.0,
            'au_intensity': {'AU04': 0.12},
            'gaze_stability': 0.70, 'pose_deviation_60s': 0.22}

def scale_full(v=1):
    ans = {i: v for i in range(1, 22)}
    return {'submitted_at': 1.0, 'answers': ans, 'scored': ST.score_dass21(ans)}

VOICE = {'prosody': {'f0_semitone_std': 3.2, 'rms_variation': 0.35,
                     'loudness_db_mean': -22.0}}
HR = {'heart_rate': 72.0, 'respiration_rate': 14.0,
      'heart_rate_available': True, 'respiration_rate_available': True}
NOHR = {'heart_rate': None, 'respiration_rate': None,
        'heart_rate_available': False, 'respiration_rate_available': False}

def backend(face, voice, scale, hr):
    """完全复刻 /portrait/portrait 路由的行为，含 _attach_narrative。"""
    blocking = ([] if face else ['face']) + ([] if voice else ['voice']) + \
               ([] if scale else ['scale'])
    rd = {'ready': not blocking, 'blocking': blocking, 'hr': hr,
          'hr_available': bool(hr and hr.get('heart_rate_available'))}
    snap = {'face': face, 'voice': voice, 'scale': scale, 'readiness': rd}
    pt = S.compute_portrait(snap, rd)
    pt['readiness'] = rd
    ST._attach_narrative(pt, snap)       # 走后端真实的挂载函数
    return pt


print('=== 1. 三项齐备：综合分应显示、环应走、标题应转正 ===')
ptA = backend(face_full(), VOICE, scale_full(1), HR)
comp = ptA['narrative']['facts']['composite']
r = render(ptA)
print('    后端 composite=%r  narrative.title=%r' % (comp, ptA['narrative']['title']))
print('    渲染 -> %s' % json.dumps(r, ensure_ascii=False))
ck('1 后端算出了综合分', comp is not None, '得 %r' % comp)
ck('1 大圆圈显示综合分', r['num'] == str(comp), '显示 %r' % r['num'])
ck('1 圆圈不是破折号', r['num'] != '\u2014')
exp_off = round(263.9 * (1 - comp / 100.0), 1)
ck('1 进度环与数字一致', abs(float(r['ring']) - exp_off) < 0.05,
   '环 %s / 期望 %.1f' % (r['ring'], exp_off))
ck('1 标题摘掉 placeholder（真实结论不该看起来像占位）',
   'pv3-placeholder' not in r['title']['c'], r['title']['c'])
ck('1 标题为分档措辞', r['title']['t'] == ptA['narrative']['title'],
   r['title']['t'])
ck('1 标题不评价人',
   not any(w in r['title']['t'] for w in ('健康', '良好状态', '优秀')),
   r['title']['t'])
ck('1 标签渲染出来了', len(r['tags']) >= 1, '%r' % r['tags'])
ck('1 标签含综合分', any('综合' in t['t'] for t in r['tags']), '%r' % r['tags'])
ck('1 标签类名合法（CSS 里真实存在）',
   all(t['c'] in ('pv3-tag', 'pv3-tag gray', 'pv3-tag calm') for t in r['tags']),
   '%r' % [t['c'] for t in r['tags']])
ck('1 正文替换掉占位文案', '尚未接入' not in r['body']['t'])
ck('1 正文摘掉 placeholder 类', 'pv3-placeholder' not in r['body']['c'],
   r['body']['c'])
ck('1 正文声明非临床结论', '非临床结论' in r['body']['t'])


print('=== 2. 心率缺测：综合分扣留，圆圈与环必须【同步】清零 ===')
ptC = backend(face_full(), VOICE, scale_full(1), NOHR)
r = render(ptC)
print('    渲染 -> num=%r ring=%r title=%r' % (r['num'], r['ring'], r['title']))
ck('2 后端扣留综合分',
   ptC['narrative']['facts']['composite'] is None)
ck('2 圆圈显示破折号而非 0', r['num'] == '\u2014', '显示 %r' % r['num'])
# 这是最要紧的一条：只改数字不改环 = 「显示 — 但环走了 68%」
ck('2 进度环同步清零（不得出现「— 但环走了」）',
   abs(float(r['ring']) - 263.9) < 0.05, '环 %s' % r['ring'])
ck('2 标题回落且带 placeholder 类',
   r['title']['t'] == '待评估' and 'pv3-placeholder' in r['title']['c'],
   '%r' % r['title'])
ck('2 正文说明为何没有综合分', '综合分暂未计算' in r['body']['t'])
ck('2 正文点名心率', '心率' in r['body']['t'])
ck('2 标签为灰（灰=中性状态，青=有结果）',
   all('gray' in t['c'] for t in r['tags']), '%r' % r['tags'])


print('=== 3. 全空（未开始采集）===')
ptD = backend(None, None, None, None)
r = render(ptD)
print('    渲染 -> num=%r ring=%r' % (r['num'], r['ring']))
print('    body: %s' % r['body']['t'][:120])
ck('3 圆圈破折号', r['num'] == '\u2014')
ck('3 环清零', abs(float(r['ring']) - 263.9) < 0.05, '环 %s' % r['ring'])
ck('3 正文非空（要告诉用户缺什么，不能空白）', len(r['body']['t']) > 20)
ck('3 正文中文化了步骤 id',
   ('语音测试' in r['body']['t']) and ('缺: face' not in r['body']['t']),
   r['body']['t'][:80])
ck('3 未把缺测说成正常/无异常',
   not any(w in r['body']['t'] for w in ('未见异常', '无异常', '一切正常')))


print('=== 4. 活力值缺（ask#42 实况）：综合分仍应出 ===')
ptB = backend(face_full(), {'prosody': {}}, scale_full(1), HR)
compB = ptB['narrative']['facts']['composite']
r = render(ptB)
print('    composite=%r  渲染 num=%r  tags=%r' % (compB, r['num'], r['tags']))
ck('4 活力值缺不阻断综合分', compB is not None, '得 %r' % compB)
ck('4 圆圈显示该分', r['num'] == str(compB))
ck('4 标签含「暂缺」提示', any('暂缺' in t['t'] for t in r['tags']),
   '%r' % r['tags'])
ck('4 正文写明活力值未计算',
   '活力值' in r['body']['t'] and '未计算' in r['body']['t'])


print('=== 5. 退化路径：narrative 缺失 / 为 error 时不得炸、不得显示假值 ===')
ptX = backend(face_full(), VOICE, scale_full(1), HR)
del ptX['narrative']
r = render(ptX)
ck('5a 无 narrative 时圆圈保持破折号', r['num'] == '\u2014', '显示 %r' % r['num'])
ck('5a 无 narrative 时环保持清零', abs(float(r['ring']) - 263.9) < 0.05)
ck('5a 无 narrative 时正文保留原占位文案', r['body']['t'] == '占位',
   '得 %r' % r['body']['t'])

ptY = backend(face_full(), VOICE, scale_full(1), HR)
ptY['narrative'] = {'title': None, 'tags': [], 'body': None,
                    'source': 'error', 'error': '结论生成失败: boom'}
r = render(ptY)
ck('5b source=error 时不显示分数', r['num'] == '\u2014', '显示 %r' % r['num'])
ck('5b source=error 时环清零', abs(float(r['ring']) - 263.9) < 0.05)
ck('5b source=error 时标题不被清空成空字符串',
   r['title']['t'] == '待评估', '得 %r' % r['title']['t'])

r = render({})
ck('5c 空对象不抛异常', r['num'] == '\u2014')
r = render({'narrative': None})
ck('5d narrative=null 不抛异常', r['num'] == '\u2014')


print('=== 6. 标签行重复渲染（refresh 每 N 秒调一次）===')
# 真实场景里 refresh 会反复调用 paintConclusion，标签必须先清空再填，
# 否则每次刷新都会往后堆一串重复标签。
src = STUB + "var PT=" + json.dumps(ptB, ensure_ascii=False) + ";\n" + fn + \
      "\npaintConclusion(PT); paintConclusion(PT); paintConclusion(PT);\n" + \
      "JSON.stringify({n:TAGS._kids.length});"
n = json.loads(dukpy.evaljs(src))['n']
exp = len(ptB['narrative']['tags'])
ck('6 连调三次标签数不累积', n == exp, '得 %d 个 / 期望 %d' % (n, exp))


print('=== 7. 越界分数被夹住（防御后端将来出 bug）===')
ptZ = backend(face_full(), VOICE, scale_full(1), HR)
ptZ['narrative']['facts']['composite'] = 140
r = render(ptZ)
ck('7 分数 140 时环不为负偏移',
   float(r['ring']) >= -0.05, '环 %s' % r['ring'])
ptZ['narrative']['facts']['composite'] = -20
r = render(ptZ)
ck('7 分数 -20 时环不超过周长',
   float(r['ring']) <= 263.95, '环 %s' % r['ring'])

print()
if F:
    print('!!! %d 条断言失败' % F)
    sys.exit(1)
print('全部通过 ✔')
