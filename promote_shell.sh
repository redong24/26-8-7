#!/bin/bash
# ==================================================================
# 把侧边导航外壳提升为默认入口
# ------------------------------------------------------------------
# 目标：访问 /max 直接看到新界面，便于后期统一操作。
#
# 路由改造（仅改 test2.py 的模板映射，index.html 一字节不动）：
#   /max        index.html  ->  shell.html   （默认入口，改这一处）
#   /max_inner  不存在      ->  index.html   （新增，供 iframe 内层加载）
#   /v2         shell.html  ->  shell.html   （不变，历史别名）
#
# 为什么必须新增 /max_inner：
#   shell.html 的 iframe 原本 src="/max"。若 /max 改渲染 shell.html
#   而 iframe 仍指向 /max，外壳会在自己的 iframe 里递归加载自身，
#   浏览器直接卡死。故内层必须换成独立路由。
#
# 用法：
#   bash promote_shell.sh --dry-run    预检，不改动
#   bash promote_shell.sh              执行切换
#   bash promote_shell.sh --revert     还原（/max 回到 index.html）
# ==================================================================
set -e

SRC=/home/lsz/webapp
APP=/home/lsz/real_time_plus/real_time_Demo
TS=$(date +%Y-%m-%d_%H%M%S)

MODE=install
[ "$1" = "--dry-run" ] && MODE=dryrun
[ "$1" = "--revert" ]  && MODE=revert

# ---------- 还原 ----------
if [ "$MODE" = "revert" ]; then
    echo "=== 还原：/max 回到原首页 ==="
    BAK=$(ls -t "$APP"/test2.py.bak_promote_* 2>/dev/null | head -1 || true)
    if [ -z "$BAK" ]; then
        echo "  ✗ 找不到 promote 备份，无法还原" >&2
        exit 1
    fi
    cp "$APP/test2.py" "$APP/test2.py.bak_beforerevert_$TS"
    cp "$BAK" "$APP/test2.py"
    echo "  ✓ 已从 $(basename "$BAK") 还原 test2.py"
    # iframe src 同步回退
    if [ -f "$SRC/templates/shell.html" ]; then
        sed -i 's|src="/max_inner"|src="/max"|' "$SRC/templates/shell.html"
        cp "$SRC/templates/shell.html" "$APP/templates/shell.html"
        echo "  ✓ shell.html iframe src 回退为 /max"
    fi
    "$PYBIN" -m py_compile "$APP/test2.py" 2>/dev/null || python3 -m py_compile "$APP/test2.py"
    echo "  ✓ 语法检查通过"
    echo ""
    echo "重启服务生效： bash $SRC/start_gunicorn_8801.sh"
    exit 0
fi

PYBIN=/home/lsz/miniconda3/envs/rrpg_plus/bin/python3.11
[ -x "$PYBIN" ] || PYBIN=python3

echo "=== 前置检查 ==="

for f in "$APP/test2.py" "$APP/templates/index.html" "$SRC/templates/shell.html"; do
    [ -f "$f" ] || { echo "  ✗ 缺少 $f" >&2; exit 1; }
done

HOME_MD5_BEFORE=$(md5sum "$APP/templates/index.html" | awk '{print $1}')
echo "首页模板校验和(前)：$HOME_MD5_BEFORE"

# 确认 /max 当前状态
if grep -q "def index_max_inner" "$APP/test2.py"; then
    echo "  ! 检测到已执行过切换（/max_inner 已存在）"
    ALREADY=1
else
    ALREADY=0
fi

# 确认 shell.html 的 iframe 已指向内层路由
if ! grep -q 'src="/max_inner"' "$SRC/templates/shell.html"; then
    echo "  ✗ $SRC/templates/shell.html 的 iframe src 不是 /max_inner" >&2
    echo "    必须先改为 /max_inner，否则会递归套娃" >&2
    exit 1
fi
echo "  ✓ shell.html iframe 已指向 /max_inner，无递归风险"

if [ "$MODE" = "dryrun" ]; then
    echo ""
    echo "=== DRY RUN（不做任何改动）==="
    echo "将改造 $APP/test2.py 路由映射："
    echo "    /max        index.html -> shell.html   （默认入口）"
    echo "    /max_inner  新增       -> index.html   （iframe 内层）"
    echo "    /v2         保持 shell.html            （历史别名）"
    echo "将复制： shell.html / shell_panels.js -> $APP"
    echo "不会修改：templates/index.html（一字节不动）、/ 、/jc"
    [ "$ALREADY" = "1" ] && echo "注意：已切换过，重复执行为幂等空操作"
    exit 0
fi

echo ""
echo "=== 1. 同步模板与静态资源 ==="
cp "$SRC/templates/shell.html" "$APP/templates/shell.html"
echo "  ✓ templates/shell.html"
cp "$SRC/static/shell_panels.js" "$APP/static/shell_panels.js"
echo "  ✓ static/shell_panels.js"

echo ""
echo "=== 2. 改造路由映射 ==="
cp "$APP/test2.py" "$APP/test2.py.bak_promote_$TS"
echo "  ✓ 已备份 test2.py.bak_promote_$TS"

TARGET="$APP/test2.py" "$PYBIN" - <<'PYEOF'
import os, re, sys

path = os.environ['TARGET']
with open(path, 'rb') as fh:
    raw = fh.read()
nl = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8')

if 'def index_max_inner' in text:
    print('  ! /max_inner 已存在，跳过路由改造（幂等）')
    sys.exit(0)

# 定位 /max 路由块：从 @app.route('/max') 到下一个 @app.route 之前
pat = re.compile(
    r"@app\.route\((['\"])/max\1\)\s*\n"      # 装饰器
    r"def\s+indexNew\s*\(\s*\)\s*:\s*\n"       # 函数定义
    r"(?:.*?\n)*?"                             # 函数体（非贪婪）
    r"(?=@app\.route)"                         # 直到下一个路由装饰器
)
m = pat.search(text)
if not m:
    print('  ✗ 未能定位 /max 路由块，放弃改造', file=sys.stderr)
    sys.exit(2)

old_block = m.group(0)

# 原块里把渲染目标换成 shell.html，函数名保持 indexNew 不变
# （函数名不动，避免影响任何 url_for('indexNew') 之类的引用）
new_max = old_block.replace("render_template('index.html')",
                            "render_template('shell.html')")
if new_max == old_block:
    print('  ✗ /max 块中未找到 render_template(\'index.html\')', file=sys.stderr)
    sys.exit(3)

# 追加内层路由，渲染原首页 index.html
inner = (
    "# ==================================================================\n"
    "# [外壳内层] 原首页 index.html 的专用路由。\n"
    "# shell.html 的 iframe 加载本路由。index.html 本身未做任何改动。\n"
    "# 不要让 shell.html 的 iframe 指向 /max —— /max 现在就是外壳页，\n"
    "# 那样会导致外壳在自己的 iframe 中递归加载自身，浏览器卡死。\n"
    "# ==================================================================\n"
    "@app.route('/max_inner')\n"
    "def index_max_inner():\n"
    "    \"\"\"原首页（供外壳页 iframe 内层加载）\"\"\"\n"
    "    session_id = request.cookies.get('session_id')\n"
    "    if not session_id or not rppg_app.get_session(session_id):\n"
    "        session_id = rppg_app.create_session()\n"
    "        resp = make_response(render_template('index.html'))\n"
    "        resp.set_cookie('session_id', session_id, max_age=14400)\n"
    "    else:\n"
    "        resp = make_response(render_template('index.html'))\n"
    "    resp.headers[\"Cache-Control\"] = \"no-store, no-cache, must-revalidate, max-age=0\"\n"
    "    resp.headers[\"Pragma\"] = \"no-cache\"\n"
    "    resp.headers[\"Expires\"] = \"0\"\n"
    "    return resp\n"
    "\n"
    "\n"
)

text = text[:m.start()] + new_max + inner + text[m.end():]

if nl != '\n':
    text = text.replace('\n', nl)
with open(path, 'wb') as fh:
    fh.write(text.encode('utf-8'))
print('  ✓ /max 已指向 shell.html')
print('  ✓ /max_inner 已新增，指向 index.html')
PYEOF

echo ""
echo "=== 3. 语法检查 ==="
if ! "$PYBIN" -m py_compile "$APP/test2.py" 2>/dev/null; then
    echo "  ✗ 语法错误，自动回滚" >&2
    cp "$APP/test2.py.bak_promote_$TS" "$APP/test2.py"
    echo "  ✓ 已回滚" >&2
    exit 1
fi
echo "  ✓ test2.py 语法正确"

echo ""
echo "=== 4. 完整性校验 ==="
HOME_MD5_AFTER=$(md5sum "$APP/templates/index.html" | awk '{print $1}')
if [ "$HOME_MD5_BEFORE" != "$HOME_MD5_AFTER" ]; then
    echo "  ✗ index.html 被修改！回滚" >&2
    cp "$APP/test2.py.bak_promote_$TS" "$APP/test2.py"
    exit 1
fi
echo "  ✓ templates/index.html 校验和一致，首页文件零改动"

# 路由齐全性
for r in "/" "/jc" "/max" "/max_inner" "/v2"; do
    if grep -q "@app.route('$r')" "$APP/test2.py"; then
        echo "  ✓ 路由存在：$r"
    else
        echo "  ✗ 路由缺失：$r，回滚" >&2
        cp "$APP/test2.py.bak_promote_$TS" "$APP/test2.py"
        exit 1
    fi
done

# 递归自检：iframe 不得指向 /max
if grep -q 'src="/max"' "$APP/templates/shell.html"; then
    echo "  ✗ 线上 shell.html 的 iframe 仍指向 /max，会递归！回滚" >&2
    cp "$APP/test2.py.bak_promote_$TS" "$APP/test2.py"
    exit 1
fi
echo "  ✓ 无 iframe 递归风险"

echo ""
echo "=================================================="
echo " 切换完成：新界面已成为 /max 默认入口"
echo "=================================================="
echo " 重启服务： bash $SRC/start_gunicorn_8801.sh"
echo ""
echo " 新界面(默认)： https://<服务器>:8801/max"
echo " 原首页(单独)： https://<服务器>:8801/max_inner"
echo " 历史别名：     https://<服务器>:8801/v2"
echo ""
echo " 还原：     bash promote_shell.sh --revert"
echo "=================================================="
