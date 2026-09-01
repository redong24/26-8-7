#!/usr/bin/env bash
# ==================================================================
# HiKO 侧边导航外壳 · 部署脚本
# ------------------------------------------------------------------
# 作用：把 shell 外壳安装到线上运行目录，新增 /v2 路由。
#
# 安全保证：
#   1. 不修改 templates/index.html（首页模板零改动）
#   2. 不修改 / /jc /max 三个现有路由
#   3. 只新增：templates/shell.html、static/shell_panels.js、/v2 路由
#   4. 修改 test2.py 前自动备份
#   5. 已安装过则幂等跳过，可重复执行
#
# 用法：
#   bash deploy_shell.sh            # 安装
#   bash deploy_shell.sh --dry-run  # 只检查不改动
#   bash deploy_shell.sh --rollback # 回滚 test2.py
# ==================================================================
set -euo pipefail

SRC="/home/lsz/webapp"
APP="/home/lsz/real_time_plus/real_time_Demo"
TS="$(date +%Y-%m-%d_%H%M%S)"
DRY=0; ROLLBACK=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
[[ "${1:-}" == "--rollback" ]] && ROLLBACK=1

say(){ printf '%s\n' "$*"; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---------- 回滚 ----------
if [[ $ROLLBACK -eq 1 ]]; then
  LAST=$(ls -1t "$APP"/test2.py.bak_shell_* 2>/dev/null | head -1 || true)
  [[ -z "$LAST" ]] && die "找不到备份文件，无法回滚"
  cp "$LAST" "$APP/test2.py"
  say "已回滚 test2.py  <-  $(basename "$LAST")"
  say "请重启服务：bash $APP/start_gunicorn_8801.sh"
  exit 0
fi

# ---------- 前置检查 ----------
say "=== 前置检查 ==="
[[ -d "$APP" ]]                  || die "找不到运行目录 $APP"
[[ -f "$APP/test2.py" ]]         || die "找不到 $APP/test2.py"
[[ -f "$APP/templates/index.html" ]] || die "找不到首页模板"
[[ -f "$SRC/templates/shell.html" ]] || die "找不到 shell.html 源文件"
[[ -f "$SRC/static/shell_panels.js" ]] || die "找不到 shell_panels.js 源文件"

# 记录首页校验和，安装后比对，确保首页未被触碰
HOME_SUM_BEFORE=$(md5sum "$APP/templates/index.html" | awk '{print $1}')
say "首页模板校验和(前)：$HOME_SUM_BEFORE"

if grep -q "@app.route('/v2')" "$APP/test2.py"; then
  say "检测到 /v2 路由已存在 —— 将只更新模板与静态文件"
  ROUTE_EXISTS=1
else
  ROUTE_EXISTS=0
fi

if [[ $DRY -eq 1 ]]; then
  say ""
  say "=== DRY RUN（不做任何改动）==="
  say "将复制：  $SRC/templates/shell.html      -> $APP/templates/shell.html"
  say "将复制：  $SRC/static/shell_panels.js    -> $APP/static/shell_panels.js"
  [[ $ROUTE_EXISTS -eq 0 ]] && say "将追加：  /v2 路由 -> $APP/test2.py（先备份）" \
                            || say "跳过：    /v2 路由已存在"
  say "不会修改：templates/index.html、/ 、/jc 、/max"
  exit 0
fi

# ---------- 1. 复制模板与静态文件 ----------
say ""
say "=== 1. 安装模板与静态资源 ==="
mkdir -p "$APP/static"
cp "$SRC/templates/shell.html"   "$APP/templates/shell.html"
cp "$SRC/static/shell_panels.js" "$APP/static/shell_panels.js"
say "  ✓ templates/shell.html"
say "  ✓ static/shell_panels.js"

# ---------- 2. 追加 /v2 路由 ----------
say ""
say "=== 2. 注册 /v2 路由 ==="
if [[ $ROUTE_EXISTS -eq 1 ]]; then
  say "  - 已存在，跳过（test2.py 未改动）"
else
  cp "$APP/test2.py" "$APP/test2.py.bak_shell_$TS"
  say "  ✓ 已备份 test2.py.bak_shell_$TS"

  # 用 python 精确追加，避免 shell 转义与 CRLF 问题
  python3 - "$APP/test2.py" <<'PY'
import sys, io
path = sys.argv[1]
with open(path, 'rb') as f:
    raw = f.read()

# 保持原文件的换行风格（该文件为 CRLF）
nl = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8')

route = nl.join([
    "",
    "",
    "# ==================================================================",
    "# [侧边导航外壳] 新增路由，不影响 / /jc /max 及 index.html",
    "# 首页通过 iframe 原样嵌入 /max，CSS/JS 完全隔离。",
    "# ==================================================================",
    "@app.route('/v2')",
    "def index_shell():",
    '    """侧边导航外壳页：iframe 嵌入 /max，附加评估弹窗"""',
    "    session_id = request.cookies.get('session_id')",
    "    if not session_id or not rppg_app.get_session(session_id):",
    "        session_id = rppg_app.create_session()",
    "        resp = make_response(render_template('shell.html'))",
    "        resp.set_cookie('session_id', session_id, max_age=14400)",
    "    else:",
    "        resp = make_response(render_template('shell.html'))",
    '    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"',
    '    resp.headers["Pragma"] = "no-cache"',
    '    resp.headers["Expires"] = "0"',
    "    return resp",
    "",
])

# 插到 /video_feed 之前，紧跟 /max 之后，保持文件条理
anchor = "@app.route('/video_feed')"
idx = text.find(anchor)
if idx == -1:
    text = text.rstrip() + route
else:
    text = text[:idx] + route.lstrip('\r\n') + nl + nl + text[idx:]

with open(path, 'w', encoding='utf-8', newline='') as f:
    f.write(text)
print("  ✓ /v2 路由已写入")
PY
fi

# ---------- 3. 语法检查 ----------
say ""
say "=== 3. 语法检查 ==="
PY_BIN="/home/lsz/miniconda3/envs/rrpg_plus/bin/python3.11"
[[ -x "$PY_BIN" ]] || PY_BIN=python3
if "$PY_BIN" -m py_compile "$APP/test2.py" 2>/dev/null; then
  say "  ✓ test2.py 语法正确"
else
  say "  ✗ 语法错误！正在自动回滚..."
  LAST=$(ls -1t "$APP"/test2.py.bak_shell_* 2>/dev/null | head -1 || true)
  [[ -n "$LAST" ]] && cp "$LAST" "$APP/test2.py" && say "  已回滚到 $(basename "$LAST")"
  die "test2.py 语法检查失败，已回滚，未影响线上"
fi

# ---------- 4. 确认首页未被改动 ----------
say ""
say "=== 4. 首页完整性校验 ==="
HOME_SUM_AFTER=$(md5sum "$APP/templates/index.html" | awk '{print $1}')
if [[ "$HOME_SUM_BEFORE" == "$HOME_SUM_AFTER" ]]; then
  say "  ✓ templates/index.html 校验和一致，首页零改动"
else
  die "首页模板被意外修改！请立即检查"
fi
for r in "@app.route('/')" "@app.route('/jc')" "@app.route('/max')"; do
  grep -q "$r" "$APP/test2.py" && say "  ✓ 路由保留：$r" || die "路由丢失：$r"
done

# ---------- 完成 ----------
say ""
say "=================================================="
say " 安装完成"
say "=================================================="
say " 重启服务： bash $APP/start_gunicorn_8801.sh"
say " 新页面：   https://<服务器地址>:8801/v2"
say " 原首页：   https://<服务器地址>:8801/max   （不受影响）"
say " 回滚：     bash deploy_shell.sh --rollback"
say "=================================================="
