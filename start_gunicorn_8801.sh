#!/bin/bash
# [2026-08-11 公网卡顿修复] 用 gunicorn 替代 Werkzeug 开发服务器托管 8801。
#
# 根因: Werkzeug 3.x 在 serving.py 中硬编码每个响应 "Connection: close"
#       (源码注释: "Always close the connection. This disables HTTP/1.1
#        keep-alive connections."), 改 protocol_version 也无法启用长连接。
#       公网访问时每个请求都要重建 TCP+TLS (~500ms), /max 页面 15fps 上传帧
#       + 轮询 + 3 路 MJPEG 流, 浏览器 6 连接配额被建连风暴占满 => 卡死。
#
# 方案: gunicorn gthread worker 原生支持 HTTP/1.1 keep-alive + TLS。
#   --workers 1   : 应用是内存会话(rppg_app.sessions)+线程锁, 多进程会丢会话, 必须单进程
#   --threads 64  : 每个客户端占 3 条 MJPEG 流线程 + 上传/轮询, 需要充足线程
#   --keep-alive 75 : 长连接保持 75s, 消除重复握手
#   --certfile/--keyfile : 替代 flask 的 ssl_context='adhoc' (adhoc 每次重启换证书,
#                          固定证书还能让浏览器记住例外, 减少告警)
set -e
APP_DIR=/home/lsz/real_time_plus/real_time_Demo
PY_ENV=/home/lsz/miniconda3/envs/rrpg_plus
CERT=$APP_DIR/certs/server.crt
KEY=$APP_DIR/certs/server.key

cd "$APP_DIR"

# 停掉旧的 8801 服务 (兼容 werkzeug 直跑与 gunicorn 两种形态)
#
# 2026-08-13 修复：ss 报出的监听 PID 是 gunicorn 的 **worker**，不是 master。
# 杀 worker 时 master 会立刻补一个新 worker，端口从不释放，
# 于是新 master 启动即撞 [Errno 98] Address already in use 而死，
# 但下方的"端口存活"探测因旧 master 仍在监听而返回真 —— 静默失效并谎报成功。
# 正确做法：从监听 PID 沿 PPID 向上追到真正的 master（其父不再是 gunicorn），杀它。
LISTEN_PID=$(ss -tlnpH 2>/dev/null | grep ':8801 ' | grep -oP 'pid=\K[0-9]+' | head -1 || true)

resolve_master() {
    local pid="$1" ppid pcmd
    while [ -n "$pid" ] && [ "$pid" != "1" ]; do
        ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
        [ -z "$ppid" ] && break
        pcmd=$(ps -o cmd= -p "$ppid" 2>/dev/null || true)
        # 父进程也是同一个 gunicorn，说明当前还是 worker，继续上溯
        case "$pcmd" in
            *gunicorn*8801*|*gunicorn*test2:app*) pid="$ppid" ;;
            *) break ;;
        esac
    done
    echo "$pid"
}

if [ -n "$LISTEN_PID" ]; then
    OLD_PID=$(resolve_master "$LISTEN_PID")
    if [ "$OLD_PID" != "$LISTEN_PID" ]; then
        echo "停止旧服务 master pid=$OLD_PID (监听 worker pid=$LISTEN_PID)"
    else
        echo "停止旧服务 pid=$OLD_PID"
    fi
    kill "$OLD_PID" 2>/dev/null || true
    # 等端口真正释放；判据是端口消失，而不是进程消失
    for i in $(seq 1 15); do
        ss -tlnH 2>/dev/null | grep -q ':8801 ' || break
        sleep 1
    done
    if ss -tlnH 2>/dev/null | grep -q ':8801 '; then
        echo "优雅停止超时，强制结束 pid=$OLD_PID"
        kill -9 "$OLD_PID" 2>/dev/null || true
        # 兜底：清掉可能残留的同名 worker
        pkill -9 -f 'gunicorn.*8801.*test2:app' 2>/dev/null || true
        for i in $(seq 1 10); do
            ss -tlnH 2>/dev/null | grep -q ':8801 ' || break
            sleep 1
        done
    fi
    if ss -tlnH 2>/dev/null | grep -q ':8801 '; then
        echo "8801 端口仍被占用，拒绝启动以免新进程撞 Errno 98" >&2
        exit 1
    fi
    sleep 1
fi

# 轮转日志
[ -f output.log ] && cp output.log "output.log.before_gunicorn_$(date +%Y%m%d_%H%M%S)"

nohup "$PY_ENV/bin/gunicorn" \
    --bind 0.0.0.0:8801 \
    --workers 1 \
    --threads 64 \
    --worker-class gthread \
    --keep-alive 75 \
    --timeout 0 \
    --certfile "$CERT" \
    --keyfile "$KEY" \
    --access-logfile - \
    --error-logfile - \
    test2:app > output.log 2>&1 &

NEW_PID=$!
echo "gunicorn 已启动 (pid=$NEW_PID), 等待就绪..."

# 就绪判据不能只看"8801 端口有人监听" —— 旧 master 未死时它恒为真。
# 必须同时满足：① 我们刚拉起的进程还活着；② HTTPS 能真实应答。
for i in $(seq 1 120); do
    if ! kill -0 "$NEW_PID" 2>/dev/null; then
        echo "新进程 pid=$NEW_PID 已退出，启动失败。最后 20 行日志：" >&2
        tail -20 output.log >&2
        exit 1
    fi
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 3 https://127.0.0.1:8801/max 2>/dev/null || true)
    if [ "$code" = "200" ]; then
        echo "8801 已就绪 (master pid=$NEW_PID, /max HTTP 200)"
        exit 0
    fi
    sleep 1
done
echo "启动超时, 请检查 $APP_DIR/output.log" >&2
exit 1
