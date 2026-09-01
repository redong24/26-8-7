# -*- coding: utf-8 -*-
"""用 duktape 真跑 paintPortrait，验证渲染结果而不是靠肉眼读代码。

只桩出 paintPortrait 用到的那几个 DOM 方法。目的不是模拟浏览器，
是回答一个具体问题：给定后端真实返回的 JSON，这五行到底显示成什么。
"""
import io, re, json, dukpy

view = io.open('/home/lsz/webapp/static/portrait_view.js', encoding='utf-8').read()

# 把 paintPortrait 函数体单独抠出来（从声明到下一个同级 function）
m = re.search(r'\n      function paintPortrait\(pt\) \{.*?\n      \}\n', view, re.S)
assert m, '未定位到 paintPortrait'
fn = m.group(0)

STUB = r"""
var LOG = {rows:{}, chip:null, fml:null};
function mkEl(dim){
  var fill = {style:{width:'0%'}, className:'pv3-p-fill'};
  var score = {textContent:'-', className:'pv3-p-score dim'};
  var row = {
    _dim: dim, title:'',
    getAttribute:function(k){ return this._dim; },
    querySelector:function(sel){
      if (sel.indexOf('fill')>=0) return fill;
      if (sel.indexOf('dim-score')>=0) return score;
      return null;
    },
    _fill:fill, _score:score
  };
  LOG.rows[dim]=row; return row;
}
var DIMS=['emotion_stability','relaxation','focus','stress','vitality'];
var ROWS=DIMS.map(mkEl);
var CHIP={textContent:'待采集', className:'pv3-chip gray'};
var FML ={textContent:'需三项采集齐备', className:'', title:''};
var root={
  querySelectorAll:function(s){ return ROWS; },
  querySelector:function(s){
    if (s.indexOf('portrait-chip')>=0) return CHIP;
    if (s.indexOf('portrait-formula')>=0) return FML;
    return null;
  }
};
"""

TAIL = r"""
paintPortrait(PT);
var out={rows:{}, chip:{t:CHIP.textContent,c:CHIP.className}, fml:{t:FML.textContent}};
DIMS.forEach(function(d){
  var r=LOG.rows[d];
  out.rows[d]={score:String(r._score.textContent), cls:r._score.className,
               w:r._fill.style.width, fcls:r._fill.className, title:r.title};
});
JSON.stringify(out);
"""

def run(pt):
    src = STUB + "var PT=" + json.dumps(pt) + ";\n" + fn + TAIL
    return json.loads(dukpy.evaljs(src))

def show(tag, pt):
    r = run(pt)
    print('=== %s ===' % tag)
    for d in ('emotion_stability','relaxation','focus','stress','vitality'):
        x = r['rows'][d]
        print('  %-18s %-4s w=%-6s fill=%-18s score_cls=%s'
              % (d, x['score'], x['w'], x['fcls'], x['cls']))
        if x['title']: print('        title: %s' % x['title'].replace('\n',' | ')[:100])
    print('  chip: %r (%s)' % (r['chip']['t'], r['chip']['c']))
    print('  fml : %r' % r['fml']['t'])
    return r

# --- 场景 1：未采集（应全部「—」、宽度 0、chip=待采集）-------------------
empty = {"gated": True, "dimensions":[{"id":d,"value":None,"missing":["缺 face"]}
          for d in ('emotion_stability','relaxation','focus','stress','vitality')],
         "composite":{"value":None,"missing":["三项采集未齐备（缺: face、voice、scale）"]}}
r1 = show('场景1 未采集', empty)

# --- 场景 2：齐备（用户截图里的状态：三项都完成）-------------------------
full = {"gated": False, "dimensions":[
    {"id":"emotion_stability","value":78.4,"missing":[]},
    {"id":"relaxation","value":61.2,"missing":[]},
    {"id":"focus","value":83.9,"missing":[]},
    {"id":"stress","value":42.5,"missing":[],"higher_is_worse":True},
    {"id":"vitality","value":55.0,"missing":[],"exploratory":True,
     "dropped":[{"term":"loudness_db_mean","why":"峰值归一化后恒为 -7.27dB"}]},
 ], "composite":{"value":70.25,"included":["emotion_stability","relaxation","focus","stress"],
                 "note":"四维等权，压力取反"}}
r2 = show('场景2 三项齐备', full)

# --- 场景 3：部分可用（有语音无面部）------------------------------------
part = {"gated": False, "dimensions":[
    {"id":"emotion_stability","value":None,"missing":["面部数据未固化"]},
    {"id":"relaxation","value":None,"missing":["心率未测得"]},
    {"id":"focus","value":None,"missing":["面部数据未固化"]},
    {"id":"stress","value":50.0,"missing":[],"higher_is_worse":True},
    {"id":"vitality","value":48.3,"missing":[],"exploratory":True},
 ], "composite":{"value":None,"missing":["情绪稳定缺项，综合分不计算"]}}
r3 = show('场景3 部分可用', part)

# ---------------- 断言 ----------------
a=0
def ck(c,msg):
    global a
    assert c, 'FAIL: '+msg
    a+=1

# 场景1：绝不显示 0（0 是测量结果，缺测不是）
for d in r1['rows']:
    ck(r1['rows'][d]['score']=='—', '未采集时 %s 应显示「—」' % d)
    ck(r1['rows'][d]['w']=='0%', '未采集时 %s 宽度应为 0' % d)
    ck('dim' in r1['rows'][d]['cls'], '未采集时 %s 应带 dim 灰化类' % d)
    ck('暂缺' in r1['rows'][d]['title'], '未采集时 %s 应写明缺什么' % d)
ck(r1['chip']['t']=='待采集', '未采集 chip 应为「待采集」')
ck('公式待定义' not in r1['fml']['t'], '不得再出现「公式待定义」')

# 场景2：这是用户报的故障——必须出数
ck(r2['rows']['emotion_stability']['score']=='78', '情绪稳定应四舍五入为 78')
ck(r2['rows']['relaxation']['score']=='61', '放松度应为 61')
ck(r2['rows']['focus']['score']=='84', '专注度 83.9 应进位为 84')
ck(r2['rows']['stress']['score']=='43', '压力值 42.5 应为 43')
ck(r2['rows']['vitality']['score']=='55', '活力值应为 55')
ck(r2['rows']['emotion_stability']['w']=='78.4%', '进度条宽度应为原值不取整')
# 反向指标着色，且只有压力一条
ck(r2['rows']['stress']['fcls']=='pv3-p-fill amber', '压力条应为 amber，实为 %r' % r2['rows']['stress']['fcls'])
ck(r2['rows']['stress']['cls']=='pv3-p-score amber', '压力分数应为 amber')
for d in ('emotion_stability','relaxation','focus','vitality'):
    ck('amber' not in r2['rows'][d]['fcls'], '%s 不应着 amber' % d)
    ck('dim' not in r2['rows'][d]['cls'], '%s 有值时不应保留灰化类 dim' % d)
ck('探索性' in r2['rows']['vitality']['title'], '活力值应提示探索性')
ck('-7.27dB' in r2['rows']['vitality']['title'], '活力值 title 应带剔除说明')
ck(r2['chip']['t']=='综合 70', 'chip 应显示综合 70，实为 %r' % r2['chip']['t'])
ck('done' in r2['chip']['c'], 'chip 应为 done 态')
ck('活力值不计入' in r2['fml']['t'], '图例应说明活力值不计入')

# 场景3：部分可用时不得谎报 0，也不得谎报综合分
ck(r3['rows']['focus']['score']=='—', '缺项应显示「—」')
ck(r3['rows']['stress']['score']=='50', '已可算的维度应照常出数')
ck(r3['chip']['t']=='部分可用', 'chip 应为「部分可用」，实为 %r' % r3['chip']['t'])
ck(r3['fml']['t']=='情绪稳定缺项，综合分不计算', '应原样显示后端给的原因')

print('\n全部通过 ✔  共 %d 条断言' % a)
