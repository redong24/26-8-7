#!/usr/bin/env bash
# ==============================================================================
# 心理综合评估 v3 前端改造 · 部署脚本
# ------------------------------------------------------------------------------
# 作用：把 webapp 中已验证的两个前端文件同步到线上应用目录
#         static/psy_v3.css        （新增）
#         static/shell_panels.js   （psy 面板替换为 v3）
#         templates/shell.html     （新增 psy_v3.css 与字体 <link>）
#
# 不动的东西（本次改造零触碰）：
#         templates/index.html     ← 首页，md5 前后校验
#         test2.py                 ← 后端，本次无需改动（纯前端资源）
#
# 幂等：可反复执行。每次执行前自动备份被覆盖的文件。
#
# 用法：
#   bash deploy_psy_v3.sh              # 正式部署
#   bash deploy_psy_v3.sh --dry-run    # 只对比差异，不写入
#   bash deploy_psy_v3.sh --rollback   # 回滚到最近一次备份
# ==============================================================================
set -euo pipefail

SRC="/home/lsz/webapp"
APP="/home/lsz/real_time_plus/real_time_Demo"
TS="$(date +%Y-%m-%d_%H%M%S)"
TAG="psyv3"

MODE="deploy"
[[ "${1:-}" == "--dry-run" ]] && MODE="dry"
[[ "${1:-}" == "--rollback" ]] && MODE="rollback"

c_g(){ printf '\033[32m%s\033[0m\n' "$*"; }
c_r(){ printf '\033[31m%s\033[0m\n' "$*"; }
c_y(){ printf '\033[33m%s\033[0m\n' "$*"; }
c_b(){ printf '\033[36m%s\033[0m\n' "$*"; }

# 受保护文件：首页
HOME_TPL="$APP/templates/index.html"
HOME_MD5_EXPECT="a6f582c049f1a5e86662d36e2184983d"

# 同步清单： 源相对路径
FILES=(
  "static/psy_v3.css"
  "static/shell_panels.js"
  "static/voice_recorder.js"   # 语音任务录音模块（2026-08-12 新增）
  "static/emotion_view.js"     # 情绪构成/主导情绪（2026-08-12 新增）
  "static/behavior_view.js"    # 注意与表情行为（头姿&视线 + AU 融合，2026-08-13 新增）
  "static/portrait_view.js"    # 采集完成度 + 五维门控（接 /portrait/*，2026-08-13 新增）
  "static/ai_view.js"          # AI 综合解读栏（接 /portrait/ai_*，2026-08-21 新增）
  "templates/shell.html"
)

# [画像快照层 2026-08-13]
# 根目录同步清单：源相对路径 -> 线上相对路径
# portrait_state.py 与 test2.py 同级（被 test2.py 直接 import），
# 不能放进 FILES —— 那个数组假设「源与线上相对路径相同」。
ROOT_FILES=(
  "portrait/portrait_state.py:portrait_state.py"
  # 计分层（批次 3b）。与 portrait_state.py 同级，被它 import。
  "portrait/portrait_score.py:portrait_score.py"
  # 结论栏叙述层（规则驱动，不走网络）。被 portrait_state.py import。
  "portrait/portrait_narrate.py:portrait_narrate.py"
  # AI 解读层（走 DashScope）。被 portrait_state.py 以「可选依赖」方式 import：
  # 缺这个文件不会让服务起不来，但 AI 栏会永久停在占位且不报错 —— 故必须同步。
  "portrait/llm_client.py:llm_client.py"
  # 报告单渲染层（2026-08-21）。被 portrait_state.py 以「可选依赖」方式 import
  # 并由其 register_routes 转挂 /portrait/report。缺这个文件不会让服务起不来，
  # 但「生成报告」按钮会打开 404 —— 故必须同步。
  "portrait/portrait_report.py:portrait_report.py"
)

# ------------------------------------------------------------------ 回滚
if [[ "$MODE" == "rollback" ]]; then
  c_b "== 回滚模式 =="
  found=0
  for pair in "${FILES[@]}" "${ROOT_FILES[@]}"; do
    rel="${pair##*:}"                     # 取冒号后的线上相对路径
    latest="$(ls -1t "$APP/$rel".bak_${TAG}_* 2>/dev/null | head -1 || true)"
    if [[ -n "$latest" ]]; then
      cp -p "$latest" "$APP/$rel"
      c_g "  恢复 $rel  <- $(basename "$latest")"
      found=1
    else
      c_y "  跳过 $rel （无备份，可能是本次新增文件）"
    fi
  done
  [[ $found -eq 1 ]] && c_g "回滚完成，请重启服务。" || c_r "未找到任何备份。"
  exit 0
fi

# ------------------------------------------------------------------ 前置校验
c_b "== 前置校验 =="

# 1. 源文件齐备
for rel in "${FILES[@]}"; do
  [[ -f "$SRC/$rel" ]] || { c_r "缺少源文件 $SRC/$rel"; exit 1; }
done
c_g "  源文件齐备 (${#FILES[@]} 个)"

for pair in "${ROOT_FILES[@]}"; do
  s="${pair%%:*}"
  [[ -f "$SRC/$s" ]] || { c_r "缺少源文件 $SRC/$s"; exit 1; }
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$SRC/$s" \
    || { c_r "  $s 语法错误"; exit 1; }
done
c_g "  根目录源文件齐备并通过 ast 校验 (${#ROOT_FILES[@]} 个)"

# 2b. portrait_state.py 不得存储任何已证实的固化假值字段
#     （ear/perclos/blink_rate/roll/au_symmetry/au_activity/attention/psycho）
#     这些字段一旦进快照，迟早有人拿去算公式，注释拦不住。
python3 - "$SRC/portrait/portrait_state.py" <<'PY' || exit 1
import sys, re
s = open(sys.argv[1], encoding='utf-8').read()
m = re.search(r'snap\s*=\s*\{(.*?)\n\s*\}', s, re.S)
assert m, '未找到 put_face 的 snap 字典 —— 假值闸门无法校验'
body = m.group(1)
BAD = ('ear_l','ear_r','perclos','blink_rate','au_symmetry',
       'au_activity','attention','psycho','roll','fatigue')
hit = [b for b in BAD if b in body]
assert not hit, '快照中出现已证实的固化假值字段: %s' % hit
print('  portrait_state.py 假值闸门通过（%d 个禁用字段均未入快照）' % len(BAD))
PY

# 2. 首页 md5 必须是已知值（确认没被别人改过）
HOME_MD5_NOW="$(md5sum "$HOME_TPL" | awk '{print $1}')"
if [[ "$HOME_MD5_NOW" != "$HOME_MD5_EXPECT" ]]; then
  c_r "  首页 md5 与预期不符！"
  c_r "    预期 $HOME_MD5_EXPECT"
  c_r "    实际 $HOME_MD5_NOW"
  c_r "  为避免误判，请人工确认后再部署。"
  exit 1
fi
c_g "  首页 md5 校验通过 ($HOME_MD5_EXPECT)"

# 3. shell_panels.js 必须是 v3 版本且 5 个面板齐全
python3 - "$SRC/static/shell_panels.js" <<'PY' || exit 1
import sys,re
src=open(sys.argv[1],encoding='utf-8').read()
missing=[k for k in ('psy','bio','skin','scene','plan') if not re.search(r'^%s: \{'%k,src,re.M)]
assert not missing, '面板缺失: %s'%missing
assert 'class="psy-v3"' in src, 'psy 面板不是 v3 版本（缺少 .psy-v3 根节点）'
# 括号 / 模板字符串平衡
i=0;n=len(src);st=[]
while i<n:
    c=src[i]
    if c=='/' and src[i+1:i+2]=='/': j=src.find('\n',i); i=n if j<0 else j; continue
    if c=='/' and src[i+1:i+2]=='*': i=src.index('*/',i+2)+2; continue
    if c in '\'"':
        q=c;j=i+1
        while j<n:
            if src[j]=='\\': j+=2; continue
            if src[j]==q: break
            j+=1
        i=j+1; continue
    if c=='`':
        j=i+1
        while j<n:
            if src[j]=='\\': j+=2; continue
            if src[j]=='`': break
            if src[j]=='$' and src[j+1:j+2]=='{':
                d=1;k=j+2
                while k<n and d>0:
                    if src[k]=='{': d+=1
                    elif src[k]=='}': d-=1
                    k+=1
                j=k; continue
            j+=1
        i=j+1; continue
    if c in '{([': st.append(c)
    elif c in '})]':
        assert st and st[-1]=={'}':'{',')':'(',']':'['}[c], '括号不匹配 @%d'%src.count('\n',0,i)
        st.pop()
    i+=1
assert not st, '存在未闭合括号'
print('  shell_panels.js 语法与结构校验通过')
PY

# 4. shell.html 必须引用 psy_v3.css，且 iframe 仍指向 /max_inner（防递归）
grep -q "filename='psy_v3.css'" "$SRC/templates/shell.html" \
  || { c_r "  shell.html 未引用 psy_v3.css"; exit 1; }
grep -q 'src="/max_inner"' "$SRC/templates/shell.html" \
  || { c_r "  shell.html 的 iframe 未指向 /max_inner（递归风险）"; exit 1; }
grep -q 'src="/max"' "$SRC/templates/shell.html" \
  && { c_r "  shell.html 中存在 src=\"/max\"，会导致外壳递归自加载！"; exit 1; }
c_g "  shell.html 引用与防递归校验通过"

# 5. CSS 必须全部作用于 .psy-v3
#    唯一例外：.p-body:has(> .psy-v3) —— 用于收回外壳弹窗内边距。
#    它仍然被 .psy-v3 限定，只命中承载本面板的那个 .p-body，
#    不会影响 bio/skin/scene/plan 四个面板。
python3 - "$SRC/static/psy_v3.css" <<'PY' || exit 1
import sys,re
css=re.sub(r'/\*.*?\*/','',open(sys.argv[1],encoding='utf-8').read(),flags=re.S)
ALLOW=re.compile(r'^\.p-body:has\(\s*>\s*\.psy-v3\s*\)')
bad=[]
for m in re.finditer(r'([^{}]+)\{',css):
    s=m.group(1).strip()
    if not s or s.startswith('@'): continue
    for p in (x.strip() for x in s.split(',')):
        if not p or p.startswith('.psy-v3'): continue
        if ALLOW.match(p): continue
        if re.match(r'^(\d+%|from|to)$',p): continue
        bad.append(p)
assert not bad, '存在未隔离到 .psy-v3 的选择器: %s'%bad[:5]
print('  psy_v3.css 命名隔离校验通过（全部经 .psy-v3 限定）')
PY

# 6. 布局高度预算：面板固有高度须能容纳于常见视口，避免滚动导致顶部卡片被裁切
python3 - "$SRC/static/psy_v3.css" <<'PY' || exit 1
import sys,re
css=open(sys.argv[1],encoding='utf-8').read()
need=[('max-height:940px',),('max-height:880px',),('max-height:800px',)]
missing=[h[0] for h in need if '@media (%s)'%h[0] not in css]
assert not missing, '缺少高度降级断点: %s'%missing
# 面板里最占高度的可视块必须随视口变矮而逐档递减（按文件出现顺序，基准档在最前）。
# 2026-08-13：情绪时间线(.pv3-chart)已被「注意与表情行为」取代，此处改为校验注意罗盘。
# 原 .pv3-chart 断点规则已无 DOM 匹配，属死规则，一并移除。
assert '.pv3-chart{' not in css, '.pv3-chart 已随情绪时间线移除，断点里不应再有其死规则'
hs=[int(m.group(1)) for m in
    re.finditer(r'\.pv3-gaze-compass\{[^}]*?height:(\d+)px', css)]
assert len(hs)>=4, '未找到足够的 .pv3-gaze-compass 高度声明(找到%d个)'%len(hs)
assert hs==sorted(hs,reverse=True), '注意罗盘高度未逐档递减: %s'%hs
# 罗盘缩小后，圆点半径必须由 JS 按元素实际尺寸推导，不能写死 px，
# 否则小屏下圆点会被推到罗盘之外。
beh=open(sys.argv[1].replace('psy_v3.css','behavior_view.js'),encoding='utf-8').read()
assert 'dotRadius' in beh and 'offsetWidth' in beh, \
    'behavior_view.js 未按实际尺寸推导圆点半径 —— 罗盘在断点中会缩小，写死半径将使圆点溢出'
assert 'R_PX' not in beh, 'behavior_view.js 仍存在写死的 R_PX 半径'
print('  布局高度预算校验通过（3 档高度断点，注意罗盘 %s，圆点半径已解耦）'%('→'.join(map(str,hs))))
PY

# 7. 移除标题带 .p-head 后的功能完整性
#    标题带被删掉时，住在里面的关闭按钮与合规声明极易被一起带走。
#    此闸门确保：弹窗不再渲染 .p-head，但关闭入口与「示例数据」声明仍在，
#    且 JS 里 querySelector('[data-close]') 一定能取到节点（否则弹窗关不掉）。
python3 - "$SRC/templates/shell.html" <<'PY' || exit 1
import sys,re
h=open(sys.argv[1],encoding='utf-8').read()

# 标题带不得再被渲染（注释里提到名字是允许的，这里只查真实标签）
assert not re.search(r'<div\s+class="p-head"', h), '.p-head 标签仍在渲染，标题带未移除'

# 关闭按钮必须存在，且必须位于 .p-foot 之内（JS 依赖它作为唯一鼠标关闭入口）
m=re.search(r'<div class="p-foot">(.*?)</div>\s*`', h, re.S)
assert m, '未找到 .p-foot 区块'
foot=m.group(1)
assert 'data-close' in foot, '关闭按钮 [data-close] 不在底栏内 —— 弹窗将无法用鼠标关闭'
assert 'data-export' in foot, '导出按钮 [data-export] 丢失'
assert 'mock-note' in foot, '「示例数据·未接入实时模型」合规声明丢失'
assert 'breadcrumb' in foot, '面包屑丢失 —— 移除标题带后它是面板身份的唯一文字标识'

# JS 仍按 [data-close] 取节点，二者必须对应
assert "querySelector('[data-close]')" in h, 'JS 未绑定 [data-close] 关闭事件'

# 底栏样式必须能容纳缩小后的按钮
assert re.search(r'\.p-foot\s*\{[^}]*flex:0 0 32px', h), '.p-foot 高度未按新布局设为 32px'
print('  标题带移除后的功能完整性校验通过（关闭/导出/声明/面包屑均在底栏）')
PY

# 8. 闸门 8：画像接线完整性（2026-08-13）
#    这条链路极易被"只改渲染"的后续改动悄悄切断，而切断后【没有任何报错】：
#    DASS 照样能答、分数照样显示，只是答案永远到不了后端，
#    五维画像永远等不齐三份数据。故把每一环都钉死。
python3 - "$SRC/static/shell_panels.js" "$SRC/static/portrait_view.js" "$SRC/templates/shell.html" "$SRC/portrait/portrait_score.py" "$SRC/static/psy_v3.css" "$SRC/portrait/portrait_state.py" "$SRC/portrait/portrait_narrate.py" "$SRC/static/ai_view.js" "$SRC/portrait/llm_client.py" <<'GATE8' || exit 1
import sys
panels = open(sys.argv[1], encoding='utf-8').read()
view   = open(sys.argv[2], encoding='utf-8').read()
shell  = open(sys.argv[3], encoding='utf-8').read()
score  = open(sys.argv[4], encoding='utf-8').read()
css    = open(sys.argv[5], encoding='utf-8').read()
pstate = open(sys.argv[6], encoding='utf-8').read()
NARRATE_PATH = sys.argv[7]

# a) 脚本必须被加载，否则钩子根本不存在
assert "filename='portrait_view.js'" in shell, 'shell.html 未加载 portrait_view.js'

# b) 挂载点与 DOM 锚点：两侧都要有，缺一边就是静默失效
assert 'window.PortraitView.mount' in panels, 'shell_panels.js 未挂载 PortraitView'
for hook in ('data-ready-list','data-ready-chip','data-ready-note','data-portrait-face'):
    assert hook in panels, 'DOM 锚点缺失: %s' % hook
    assert hook in view,   'portrait_view.js 未消费锚点: %s' % hook

# c) 量表必须真的 POST 出去 —— 这是整轮改造的核心目的
assert '_submitScale' in panels and '_submitScale' in view, 'DASS 提交钩子断裂'
for ep in ('/portrait/scale', '/portrait/face', '/portrait/readiness'):
    assert ep in view, 'portrait_view.js 未调用 %s' % ep

# d) 清空重答必须同步后端，否则前端显示未答而后端仍报已完成
assert '_resetScale' in panels and '_resetScale' in view, '清空重答未同步后端'

# e) 得分必须以后端为准。前端 pct=score/42 已实测出错：
#    score=12 同时对应「焦虑 中度」与「压力 正常」，两者都显示 29%。
assert 'srvScored' in panels, '未采用后端权威得分（srvScored 缺失）'
assert 'srvScored.subscales' in panels, '回填未优先读取后端 subscales'

# f) 五维 tooltip 不得再引用已证实恒为死值的指标
import re
# map 的解构签名 2026-08-13 起是三元组（多了维度 id），这里放宽到
# 「name, formula 之后允许再跟若干形参」，避免以后加列又把闸门写死。
m = re.search(r"data-portrait-list>.*?\]\.map\(\(\[name,\s*formula[^\]]*\]\)", panels, re.S)
assert m, '未定位到五维 tooltip 区块'
block = m.group(0)
block = re.sub(r'/\*.*?\*/', '', block, flags=re.S)   # 注释里说明死值是允许的
for dead in ('HRV-SDNN', 'PERCLOS', '眨眼频率'):
    assert dead not in block, '五维 tooltip 仍引用死指标: %s' % dead
# g) 五维接线（2026-08-13 批次 3b 收尾）。
#    这一段是【用户实际踩到的坑】：后端 /portrait/portrait 算得完全正确，
#    但前端从来没去取，五行分数一直是批次 1 留下的静态占位。
#    表现是「三项采集都打勾、心率也有值，五维却全是 —」，
#    而且没有任何报错 —— 因为「—」本身就是合法的缺项显示。
#    故把这段接线的每一环都钉死。
d = re.search(r"DIMENSION_ORDER\s*=\s*\(([^)]*)\)", score)
assert d, 'portrait_score.py 未定义 DIMENSION_ORDER'
back_dims = re.findall(r'"([a-z_]+)"', d.group(1))
assert len(back_dims) == 5, 'DIMENSION_ORDER 应为 5 维，实为 %r' % (back_dims,)

# 前端 5 行必须各自带 data-dim，且 id 与后端逐一对应。
# 对不上的后果是该行永远找不到数据、永远显示「—」，同样不报错。
front_dims = re.findall(r"',\s*'([a-z_]+)'\]", block)
assert sorted(front_dims) == sorted(back_dims), \
    ('前端维度 id %r 与后端 DIMENSION_ORDER %r 不一致 —— '
     '对不上的维度会永远显示「—」且无报错' % (sorted(front_dims), sorted(back_dims)))
assert 'data-dim="${dim}"' in panels, '五维行缺 data-dim 锚点，前端无法定位到行'
print('  五维 id 契约通过: %s' % ','.join(back_dims))

# 前端必须真的去取 /portrait/portrait。缺这一句就是本次故障的全部成因。
# 必须匹配【调用形式】而不是裸字符串：这个路径名同样出现在注释里，
# 纯文本搜索会被注释骗过去（反向测试实测：注掉调用仍然放行）。
# 与闸门 9 用 AST 而非文本搜是同一个理由。
assert re.search(r'''\(\s*['"]/portrait/portrait['"]''', view), \
    'portrait_view.js 未真正调用 /portrait/portrait —— 五维会永远无输出（本次故障成因）'
assert 'paintPortrait' in view, 'portrait_view.js 缺五维渲染函数'
assert re.search(r'paintPortrait\s*\(', view.split('function paintPortrait')[-1]), \
    'paintPortrait 已定义但从未被调用'

# 三个新锚点两侧都要在
for hook in ('data-dim-score', 'data-portrait-chip', 'data-portrait-formula'):
    assert hook in panels, '五维 DOM 锚点缺失: %s' % hook
    assert hook in view,   'portrait_view.js 未消费锚点: %s' % hook

# 反向指标必须用 CSS 里真实存在的类名。psy_v3.css 定义的是 .amber；
# 写 .rev 会得到一个存在于 DOM 但没有任何样式规则的类 —— 静默视觉失败。
assert re.search(r"'pv3-p-fill'\s*\+\s*\(d\.higher_is_worse\s*\?\s*' amber'", view), \
    '压力条未使用 .amber（CSS 里没有 .rev，写错等于无样式且不报错）'

# 占位文案不得残留：公式已定稿，再显示「公式待定义」就是过期信息。
assert '公式待定义' not in panels, 'shell_panels.js 仍残留「公式待定义」占位文案'
print('  五维接线校验通过（取数/渲染/锚点/反向色/文案）')


# h) 核心结论栏接线（ask#48 步骤 2）
#    这一栏是整个面板里最像「结论」的地方，也是最容易静默出错的地方：
#    锚点存在但没人消费 = 永远显示占位文案，无异常、无日志。
#    与五维那次是完全同一类故障，故照同样的强度设闸。
CONC_HOOKS = ('data-score-num', 'data-score-ring',
              'data-score-title', 'data-score-tags',
              'data-conclusion-body')
for hook in CONC_HOOKS:
    assert hook in panels, '结论栏 DOM 锚点缺失: %s' % hook
    assert hook in view, \
        ('portrait_view.js 未消费锚点 %s —— 该槽位会永远停在占位文案，'
         '且不会报任何错' % hook)

assert 'function paintConclusion' in view, 'portrait_view.js 缺结论栏渲染函数'
# 定义了但没被调用 = 死代码，界面照旧是占位。必须匹配【调用形式】。
assert re.search(r'paintConclusion\s*\(',
                 view.split('function paintConclusion')[-1]), \
    'paintConclusion 已定义但从未被调用'

# 进度环周长必须与 SVG 的 stroke-dasharray 一致。两处写死同一个数，
# 改了一处不改另一处 -> 环的长度与百分比错配，视觉上却「看着像对的」。
mring = re.search(r'stroke-dasharray="([\d.]+)"', panels)
assert mring, 'shell_panels.js 未找到进度环 stroke-dasharray'
mlen = re.search(r'RING_LEN\s*=\s*([\d.]+)', view)
assert mlen, 'portrait_view.js 未定义 RING_LEN'
assert abs(float(mring.group(1)) - float(mlen.group(1))) < 0.05, \
    ('进度环周长不一致: SVG dasharray=%s 但 RING_LEN=%s —— '
     '环的长度会与百分比错配' % (mring.group(1), mlen.group(1)))

# 缺测绝不能回落成 0 分：0 是测量结果，「没算出来」不是。
assert re.search(r"\\u2014'\s*:\s*String\(comp\)", view), \
    '综合分缺失时未回落到破折号（回落成 0 会被读成「测得 0 分」）'
# 数字与环必须同源同步。只改数字不改环 -> 「显示 — 但环走了 68%」。
assert 'stroke-dashoffset' in view, \
    'portrait_view.js 未同步进度环 —— 会出现「数字为 — 但环走了」的自相矛盾'

# 标签类名只能用 CSS 里真实存在的。写一个不存在的类不报错但没样式。
assert re.search(r"t\.kind === 'gray'", view), \
    '标签 kind 未做白名单校验（非法类名不报错但无样式）'
for cls in ('pv3-tag.gray',):
    assert cls.replace('.', '.') in css or '.pv3-tag.gray' in css, \
        'psy_v3.css 缺 %s' % cls

# 后端必须真的挂上 narrative，否则前端消费的是 undefined。
assert '_attach_narrative' in pstate, \
    'portrait_state.py 未定义 _attach_narrative —— 结论栏拿不到文案'
assert pstate.count('_attach_narrative(') >= 3, \
    ('_attach_narrative 未在 snapshot 与 /portrait/portrait 两处都调用'
     '（定义+2处调用应 >=3 次出现，实为 %d）'
     % pstate.count('_attach_narrative('))
assert 'import portrait_narrate' in pstate, \
    'portrait_state.py 未导入 portrait_narrate'
# 叙述层必须可导入且三槽位齐全 —— 用真实导入而非文本搜。
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('_pn', NARRATE_PATH)
_pn = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_pn)
_probe = _pn.narrate({}, {})
for k in ('title', 'tags', 'body', 'source', 'facts'):
    assert k in _probe, 'portrait_narrate.narrate 缺槽位 %s' % k
assert _probe['facts']['composite'] is None, \
    '空输入下 narrate 竟给出了综合分 —— 无数据必须无分数'
assert 'numbers' in _probe['facts'], \
    'facts 缺 numbers 白名单 —— LLM 层的数字回验将无从比对'
print('  结论栏接线校验通过（5 锚点/环周长/破折号回落/标签白名单/后端挂载）')

# i) AI 综合解读栏接线（2026-08-21）
#    与结论栏同一类故障模型，但多了一条更危险的路：这一栏的内容来自外部大模型。
#    因此除了「锚点没人消费」之外，还要钉死两件事：
#      1. 失败时必须回落到占位，绝不能编；
#      2. 未测量的维度绝不能进 payload，否则模型会顺着空位往下写。
AI_JS_PATH = sys.argv[8]
LLM_PATH = sys.argv[9]
aijs = open(AI_JS_PATH, encoding='utf-8').read()
llm = open(LLM_PATH, encoding='utf-8').read()

assert "filename='ai_view.js'" in shell, 'shell.html 未加载 ai_view.js'
# 加载顺序：ai_view.js 必须在 shell_panels.js 之后，否则它开机时找不到面板 DOM。
assert shell.index("ai_view.js") > shell.index("shell_panels.js"), \
    'ai_view.js 必须在 shell_panels.js 之后加载（否则拿不到面板锚点）'

# 前端消费的锚点必须真实存在于面板。这里刻意校验 data-* 锚点而非类名：
# 类名是给样式用的（改了只丑），data-* 锚点是给脚本用的（改了直接失联）。
for hook in ('data-ai-body', 'data-ai-cta'):
    assert hook in panels, 'shell_panels.js 缺 AI 栏锚点 %s' % hook
    assert hook in aijs, \
        'ai_view.js 未消费锚点 %s —— 该栏会永久停在占位且不报错' % hook
# 容器类名仍需存在，否则布局与配色全丢。
assert 'pv3-ai-body' in panels, 'shell_panels.js 缺 AI 栏容器类名 pv3-ai-body'
for cls in ('pv3-ai-points', 'pv3-ai-meta'):
    assert cls in aijs, 'ai_view.js 未使用 %s' % cls
    assert '.' + cls in css, \
        'psy_v3.css 缺 .%s —— 有内容但无样式（长文会被 flex 居中裁掉）' % cls

# CTA 必须真的能被解锁。之前它是写死 disabled 的，点了没反应也不报错。
assert '生成解读' in panels, 'shell_panels.js 的 CTA 文案未更新为「生成解读」'
# 「未接入」只在【用户可见文案】里是错的（凭证已接入，能力已具备）；
# 注释里保留历史备忘是必要的，故先剥掉 HTML 注释再查，避免把备忘也判违规。
panels_visible = re.sub(r'<!--.*?-->', '', panels, flags=re.S)
assert '未接入' not in panels_visible, \
    ('shell_panels.js 可见文案仍残留「未接入」—— 能力已具备，'
     '这样写会让用户去查配置而不是去完成采集')

# 占位文案必须【保留】：它现在是失败时的回落态，删了就会出现空白栏。
# 2026-08-21：实现从模块级单例 placeholderHTML 改为按 DOM 实例保存
# （面板每次打开都重建 DOM，单例会在第二次打开时还原成上一轮被替换后的
# 内容）。这里校验新的保存点，保持「占位必须留存」这条意图不变。
assert '__aiPlaceholder' in aijs and 'placeholderOf' in aijs, \
    'ai_view.js 未保存占位文案 —— 失败时会留下空白栏而非诚实提示'

# 后端两条路由缺一不可：status 用来解锁按钮，summary 用来出内容。
for route in ('/portrait/ai_summary', '/portrait/ai_status'):
    assert route in pstate, 'portrait_state.py 缺路由 %s' % route
    assert route in aijs, 'ai_view.js 未调用 %s' % route

# 最关键的一条：失败绝不能变成 5xx，也绝不能编。
assert 'unavailable' in pstate, \
    'portrait_state.py 未使用 unavailable 失败契约（失败会变成 5xx）'
# 前端必须用【白名单】判定：只有 status==='ok' 才渲染内容，其余一律回落。
# 反过来做（黑名单排除 unavailable）会在出现新失败码时把报错当内容渲染。
assert re.search(r"status\s*===\s*['\"]ok['\"]", aijs), \
    'ai_view.js 未以 status===\"ok\" 白名单判定成功（新失败码会被当内容渲染）'
assert 'renderFallback' in aijs, 'ai_view.js 缺失败回落渲染函数'

# --- CTA 必须由 mount() 驱动（2026-08-21 修复「按钮点不动」的防回归）---
# 根因：外壳 openPopup() 每次打开面板都 remove() 旧 DOM 再 buildPopup()
# 建新 DOM。ai_view.js 原先靠自己 setInterval 轮询等 DOM 出现，且只在页面
# load 后跑 30 秒 —— 用户 30 秒内没点开面板，此后 CTA 就永久停在初始的
# .disabled 上，点了毫无反应也不报错。其余四个视图模块都由 mount() 驱动，
# 唯独它漏接。DOM 生命周期由 mount() 掌握，绑定就必须在那里做。
assert re.search(r'window\.AiView\s*&&\s*window\.AiView\.mount', panels), \
    ('shell_panels.js 的 mount() 未调用 AiView.mount —— '
     '面板每次重建都会丢掉事件绑定，「生成解读」按钮将永久点不动')
assert 'mount' in aijs and re.search(r'window\.AiView\s*=', aijs), \
    'ai_view.js 未暴露 AiView.mount（无法被 shell_panels.js 驱动）'
# mount 调用必须在 DASS 的 early-return 之前：否则量表结构一缺，
# AI 栏会被连带跳过，表现为「其它卡片都正常、只有这栏是死的」。
assert panels.index('window.AiView') < panels.index('if (!listEl) return;'), \
    'AiView.mount 必须在 DASS early-return 之前调用（否则量表缺失会连带跳过）'

# --- 采集齐备后自动生成（2026-08-21 需求）---
# 闸门只认 readiness.ready：这是「三项全部完成」的唯一判定处（后端 readiness()），
# 前端不得自己另立一套完成度标准，否则两处口径迟早漂移。
assert '/portrait/readiness' in aijs, \
    'ai_view.js 未轮询 /portrait/readiness —— 无法在采集齐备后自动生成'
assert 'rd.ready' in aijs, \
    'ai_view.js 未以 readiness.ready 作为自动生成闸门（不得自立完成度标准）'
# 去重与失败抑制：模型调用是计费的，4s 轮询若不去重会把额度打光。
assert 'autoDoneKey' in aijs, \
    'ai_view.js 缺自动生成去重键 —— 同一份采集会被反复送去生成（计费）'
assert 'autoFailedKey' in aijs, \
    'ai_view.js 缺失败抑制 —— 生成失败后会每 4s 重试一次，打光模型额度'

# 未测量维度必须被剔除而不是以 null 送进去。
assert '未测量维度' in llm, \
    'llm_client 未显式列出未测量维度 —— 模型会顺着空位编造'
assert 'enable_thinking' in llm, 'llm_client 缺 enable_thinking 开关'
# 实测：长提示词 + 深度思考 = 67s，会把前端 45s 超时打穿。必须显式关掉。
assert re.search(r'"enable_thinking\"\s*:\s*False', llm), \
    'enable_thinking 必须显式为 False（开启时实测 67s，前端会超时）'

# 密钥不得进版本库，且必须挡住未展开的占位符（历史上就是它导致 403）。
assert 'sk-' not in llm or 'DASHSCOPE_API_KEY' in llm, 'llm_client 疑似硬编码密钥'
assert re.search(r'startswith\(["\']\$\{', llm), \
    'llm_client 未拦截未展开的 ${VAR} 占位符（历史 403 根因）'

# 字段名必须与 portrait_state 实际写入的一致。猜错了数据就永远到不了模型，
# 而表现是「模型说没测到」—— 看起来像没采集，实际是键名对不上。
assert 'emo_distribution' in llm, \
    "llm_client 取情绪分布的键名应为 emo_distribution（put_face 写入的真实键名）"
assert 'emo_distribution' in pstate, 'portrait_state 未写入 emo_distribution'
assert re.search(r'\[["\']scored["\']\]|get\(["\']scored["\']\)', llm), \
    'llm_client 未按 scored.subscales 结构取量表分（键名对不上则量表数据丢失）'

# 真实导入 llm_client，确认无数据时【拒绝生成】而不是硬编一段话。
_spec2 = _ilu.spec_from_file_location('_llmc', LLM_PATH)
_llmc = _ilu.module_from_spec(_spec2)
_spec2.loader.exec_module(_llmc)
_pl, _n = _llmc.build_payload({})
assert _n == 0, '空快照下 measured_count 应为 0，实为 %d' % _n
print('  AI 解读栏接线校验通过（加载序/锚点/样式/回落/契约/键名/无数据拒答）')

print('  画像接线完整性校验通过（脚本/锚点/POST/重置/后端权威分/tooltip/五维）')
GATE8

# ---- 闸门 9：计分层契约（批次 3b） -------------------------------------
# 校验的每一条都真的错过或极易错：
#   1) DASS_STRESS_KEY 必须与 DASS_GROUPS 的键一致。第一版写成 "stress"
#      而实际是 "S"，结果压力维度恒 None、综合分恒 None，且【无任何报错】
#      —— 取不到就是缺项，而缺项是合法状态。是集成测试才逮住的。
#   2) 计分层不得【使用】任何固化假值/已证伪字段（只在说明里提及是允许的，
#      那正是留档；故这里只查代码行，不查字符串与注释）。
#   3) 拍板结果必须留在代码里，否则下次又要重问一遍。
echo "== 闸门 9: 计分层契约 =="
python3 - "$SRC/portrait/portrait_score.py" "$SRC/portrait/portrait_state.py" <<'GATE9' || exit 1
import sys, re, ast
score_src = open(sys.argv[1], encoding='utf-8').read()
state_src = open(sys.argv[2], encoding='utf-8').read()
ast.parse(score_src)

m = re.search(r'DASS_STRESS_KEY\s*=\s*"([^"]+)"', score_src)
assert m, 'portrait_score.py 未定义 DASS_STRESS_KEY'
key = m.group(1)
g = re.search(r'DASS_GROUPS\s*=\s*\{(.*?)\n\}', state_src, re.S)
assert g, 'portrait_state.py 未找到 DASS_GROUPS'
gk = re.findall(r'"([A-Z])"\s*:\s*\{', g.group(1))
assert key in gk, ('DASS_STRESS_KEY=%r 不在 DASS_GROUPS %r 中 —— '
                   '压力维度会恒为 None 且不报错' % (key, gk))
print('  压力分量表键契约通过: %s in %s' % (key, gk))

# 用 AST 找【真正被取用】的字典键，而不是文本搜 —— 文本搜会把
# dropped 说明里提到的字段名误判成引用，那恰恰是必须保留的留档。
used = set()
tree = ast.parse(score_src)
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
       and node.func.attr == 'get' and node.args:
        a = node.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            used.add(a.value)
    elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
         and isinstance(node.slice.value, str):
        used.add(node.slice.value)
BAD = {'perclos','blink_rate','blink_rate_per_min','au_symmetry','au_activity',
       'ear_l','ear_r','ear_l_avg','ear_r_avg','psycho','fatigue','attention',
       'attention_score','loudness_db_mean','jitter_local','shimmer_local'}
hit = used & BAD
assert not hit, ('计分层取用了禁用字段 %r（固化假值或已证伪）。'
                 '若确需使用，先在 FORMULA_FIXES 里给出证据' % sorted(hit))
print('  假值/证伪字段闸门通过（实际取用 %d 个键，无一命中 %d 个禁用项）'
      % (len(used), len(BAD)))

for qid in ('vitality_signal', 'voice_quality_availability'):
    assert qid in state_src, '缺 open_question: %s' % qid
assert state_src.count('"resolved": True') >= 2, '两项 open_question 必须都标 resolved'
assert '"decision"' in state_src, 'open_question 缺 decision 字段'
print('  拍板结果留档通过（2 项 resolved + decision）')

assert 'getattr(_score, "WEIGHTS"' in state_src, \
    'FORMULA_SPEC.weights 必须从 portrait_score 取，不得复写（否则文档与实现会不一致）'
print('  权重单一来源通过')

cd = re.search(r'COMPOSITE_DIMENSIONS\s*=\s*\(([^)]*)\)', score_src)
assert cd and 'vitality' not in cd.group(1), \
    '活力值不得计入综合分（拍板：标注为探索性指标）'
print('  活力值排除出综合分通过')
print('闸门 9 通过')
GATE9

# ---- 闸门 10：报告单契约（2026-08-21） ---------------------------------
# 校验的每一条都是「静默出错」型风险：
#   1) DASS-21 题干必须与前端逐字一致。报告在后端渲染，拿不到前端那份
#      数组，故后端另存了一份。两份不一致 -> 报告上印的题目和用户实际
#      作答的题目错位，【没有任何报错】，但报告就是错的。
#   2) 报告页是 GET，绝不允许调用 interpret()（真花钱、可刷新重复扣费）。
#      只允许走 peek_cached。
#   3) 拍板结果必须留在代码里：缺数据允许生成 + 显著标注、逐题作答进报告、
#      免责声明必须存在。
#   4) 报告 HTML 必须能在空快照下渲染出来 —— 报告是兜底产物，不能崩。
echo "== 闸门 10: 报告单契约 =="
python3 - "$SRC/portrait/portrait_report.py" "$SRC/static/shell_panels.js" <<'GATE10' || exit 1
import sys, re, ast, importlib.util

rpt_path, js_path = sys.argv[1], sys.argv[2]
rpt_src = open(rpt_path, encoding='utf-8').read()
js_src = open(js_path, encoding='utf-8').read()

spec = importlib.util.spec_from_file_location("portrait_report", rpt_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# 1) 题干一致性
ok, probs = mod.verify_dass_items(js_src)
assert ok, "DASS-21 题干与前端不一致:\n" + "\n".join(probs)
print('  DASS-21 题干前后端逐字一致通过 (21 题)')

# 2) 报告层不得直接调用付费接口
tree = ast.parse(rpt_src)
banned = {"interpret", "interpret_cached"}
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        f = node.func
        name = getattr(f, "attr", None) or getattr(f, "id", None)
        assert name not in banned, (
            "报告层不得调用 %s —— 报告是 GET，会导致刷新即重复付费调用模型。"
            "只允许 peek_cached。" % name)
print('  报告层未调用付费模型接口通过')

# 3) 拍板结果留档
for kw, why in [
    ("数据不完备", "缺数据必须显著标注（拍板4）"),
    ("敏感个人信息", "逐题作答需带隐私提示（拍板3）"),
    ("免责声明", "报告必须含免责声明"),
    ("不构成医学诊断", "免责声明必须写明非医疗诊断"),
    ("@page", "必须有 A4 打印页面规则"),
    ("@media print", "必须有打印媒体查询"),
    ("window.print()", "必须提供打印/另存为 PDF 入口"),
]:
    assert kw in rpt_src, "报告缺少「%s」：%s" % (kw, why)
print('  拍板结果与打印能力留档通过')

# 4) 空快照必须渲染成功且不含区块异常
for snap in ({}, None, {"readiness": {}, "portrait": {}}):
    h = mod.render_report_html(snap)
    assert h.startswith("<!DOCTYPE html"), "空快照未渲染出合法 HTML"
    assert "本节渲染失败" not in h, "空快照触发了区块异常"
    for tag in ("div", "table", "tr", "td"):
        o = len(re.findall(r'<%s[\s>]' % tag, h))
        c = len(re.findall(r'</%s>' % tag, h))
        assert o == c, "<%s> 标签不配对 %d/%d" % (tag, o, c)
print('  空快照兜底渲染 + 标签配对通过')

# 5) 转义防护
evil = {"readiness": {"ready": False, "hr": {}, "hr_available": False,
        "steps": [{"id": "x", "label": "<script>alert(1)</script>",
                   "done": False, "reason": "</table><img onerror=x>"}]},
        "portrait": {"dimensions": [], "composite": {"value": None}}}
h = mod.render_report_html(evil)
assert "<script>alert(1)</script>" not in h, "HTML 注入未被转义"
assert "<img onerror=x>" not in h, "HTML 注入未被转义"
print('  HTML 转义防护通过')

print('闸门 10 通过')
GATE10

# ------------------------------------------------------------------ 差异
echo
c_b "== 变更清单 =="
CHANGED=()
for pair in "${FILES[@]}" "${ROOT_FILES[@]}"; do
  s="${pair%%:*}"; rel="${pair##*:}"
  if [[ ! -f "$APP/$rel" ]]; then
    c_y "  [新增] $rel  ($(wc -c <"$SRC/$s") bytes)"
    CHANGED+=("$s:$rel")
  elif ! cmp -s "$SRC/$s" "$APP/$rel"; then
    o=$(wc -c <"$APP/$rel"); n=$(wc -c <"$SRC/$s")
    c_y "  [更新] $rel  ($o -> $n bytes)"
    CHANGED+=("$s:$rel")
  else
    c_g "  [一致] $rel  （无需操作）"
  fi
done


if [[ ${#CHANGED[@]} -eq 0 ]]; then
  c_g "线上已是最新，无需部署。"
  exit 0
fi

if [[ "$MODE" == "dry" ]]; then
  echo
  c_b "== DRY-RUN：以上为将要执行的变更，未写入任何文件 =="
  exit 0
fi

# ------------------------------------------------------------------ 写入
echo
c_b "== 写入 =="
for pair in "${CHANGED[@]}"; do
  s="${pair%%:*}"; rel="${pair##*:}"
  if [[ -f "$APP/$rel" ]]; then
    cp -p "$APP/$rel" "$APP/$rel.bak_${TAG}_${TS}"
    c_g "  备份 $rel.bak_${TAG}_${TS}"
  fi
  install -m 644 "$SRC/$s" "$APP/$rel"
  c_g "  写入 $rel"
done

# ------------------------------------------------------------------ 事后校验
echo
c_b "== 事后校验 =="

# 首页绝对未变
HOME_MD5_AFTER="$(md5sum "$HOME_TPL" | awk '{print $1}')"
if [[ "$HOME_MD5_AFTER" == "$HOME_MD5_EXPECT" ]]; then
  c_g "  首页 md5 未变化 ✔ ($HOME_MD5_AFTER)"
else
  c_r "  首页被意外修改！立即回滚"
  bash "$0" --rollback
  exit 1
fi

# 逐字节一致
for pair in "${CHANGED[@]}"; do
  s="${pair%%:*}"; rel="${pair##*:}"
  if cmp -s "$SRC/$s" "$APP/$rel"; then
    c_g "  $rel 逐字节一致 ✔"
  else
    c_r "  $rel 写入后不一致！回滚"
    bash "$0" --rollback
    exit 1
  fi
done

echo
c_g "=============================================="
c_g " 部署完成"
c_g "=============================================="
echo "下一步：重启服务使模板生效"
echo "  bash /home/lsz/webapp/start_gunicorn_8801.sh"
echo
echo "验证：打开 https://39.183.171.185:8801/max"
echo "      点击左侧「心理综合评估」，应显示 v3 新版界面"
