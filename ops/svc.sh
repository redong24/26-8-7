#!/usr/bin/env bash
# =============================================================================
# svc —— 8801 / 5002 / 5003 三个服务的统一启停工具
# =============================================================================
# 用法：
#   svc start   [名字...]   启动（已在跑的跳过，不会重复拉起）
#   svc stop    [名字...]   停止
#   svc restart [名字...]   重启（= stop + start）
#   svc status              一览：端口 / PID / 存活 / 健康 / 运行时长
#   svc health              只做健康检查，返回码可给监控用（0=全好）
#   svc logs    <名字> [行数]  看日志尾部
#   svc tail    <名字>      跟踪日志（Ctrl-C 退出）
#
#   不写名字 = 对全部服务生效。名字可以给多个：svc restart audio openface
#
# 设计要点（都是踩过的坑）：
#   1) 停服以「端口是否还在监听」为判据，不以「进程是否消失」为判据。
#      gunicorn 的监听 PID 是 worker，杀 worker 会被 master 立刻补一个，
#      端口永不释放，新进程起来就撞 Errno 98，而「进程没了」的判据会误报成功。
#      所以这里从监听 PID 沿 PPID 上溯找到真正的 master 再杀。
#   2) 启动顺序：先微服务后主应用；停止顺序反过来。主应用虽然对微服务掉线
#      有 try 容错，但让它启动时就能连上，能省掉一轮重试日志。
#   3) 启动成功的判据是「HTTP 真的应答」，不是「端口有人监听」——
#      端口可能是没死透的旧进程占着的。
#   4) 全程不需要 root，不依赖 systemd（本机 WSL2 里 systemd 并未生效）。
# =============================================================================

set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$OPS_DIR/services.conf"
LOG_DIR="$(cd "$OPS_DIR/.." && pwd)/logs"
ACTION_LOG="$LOG_DIR/svc_actions.log"

mkdir -p "$LOG_DIR"

# ---- 单实例锁：避免定时重启和手动重启撞在一起，两边同时启动同一个端口 ----
LOCK_FILE="$LOG_DIR/.svc.lock"
exec 9>"$LOCK_FILE"
if ! flock -w 300 9; then
    echo "另一个 svc 操作已持锁超过 5 分钟，放弃本次操作。" >&2
    exit 1
fi

# ---------------- 输出着色（非终端时自动关闭，日志里不留控制字符） -----------
if [ -t 1 ]; then
    C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
    C_BLU=$'\033[36m'; C_DIM=$'\033[2m';  C_RST=$'\033[0m'; C_BLD=$'\033[1m'
else
    C_RED=''; C_GRN=''; C_YEL=''; C_BLU=''; C_DIM=''; C_RST=''; C_BLD=''
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s\n' "${C_GRN}✔${C_RST} $*"; }
warn() { printf '%s\n' "${C_YEL}!${C_RST} $*"; }
err()  { printf '%s\n' "${C_RED}x${C_RST} $*" >&2; }
step() { printf '%s\n' "${C_BLU}▸${C_RST} ${C_BLD}$*${C_RST}"; }

# 把每次操作记进审计日志：凌晨自动重启出问题时，这是唯一的现场证据
audit() {
    printf '%s [%s] %s\n' "$(date '+%F %T')" "${SVC_INVOKER:-manual}" "$*" >> "$ACTION_LOG"
}

# ---------------- 配置读取 ---------------------------------------------------
# 服务的启动顺序 = 配置文件里的书写顺序（微服务在前，主应用在后）
all_names() {
    awk -F'|' '/^[^#]/ && NF>=8 {print $1}' "$CONF"
}

# 取某服务的第 N 个字段
field() {
    local name="$1" idx="$2"
    awk -F'|' -v n="$name" -v i="$idx" \
        '/^[^#]/ && NF>=8 && $1==n {print $i; exit}' "$CONF"
}

exists() { [ -n "$(field "$1" 1)" ]; }

# ---------------- 端口 / 进程探测 -------------------------------------------
# 端口上是否有人监听
port_listening() {
    ss -tlnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"
}

# 端口上的监听 PID（可能是 gunicorn 的 worker）
port_pids() {
    ss -tlnpH 2>/dev/null \
        | awk -v p="$1" '$4 ~ ("[:.]" p "$")' \
        | grep -oP 'pid=\K[0-9]+' | sort -u
}

# 从任意监听 PID 上溯到真正的 master。
# 判据：父进程的命令行里还带着同一个端口号，说明当前这个还是子进程/worker。
resolve_master() {
    local pid="$1" port="$2" ppid pcmd
    while [ -n "$pid" ] && [ "$pid" != "1" ]; do
        ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
        [ -z "$ppid" ] && break
        [ "$ppid" = "1" ] && break
        pcmd=$(ps -o cmd= -p "$ppid" 2>/dev/null || true)
        if [ -n "$pcmd" ] && printf '%s' "$pcmd" | grep -q "$port"; then
            pid="$ppid"
        else
            break
        fi
    done
    printf '%s' "$pid"
}

# 健康检查：-k 容忍自签证书；主应用是 HTTPS 自签，必须带
http_ok() {
    local url="$1" timeout="${2:-5}" code
    [ "$url" = "-" ] && return 0
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time "$timeout" "$url" 2>/dev/null || true)
    case "$code" in
        200|204|301|302) return 0 ;;
        *) return 1 ;;
    esac
}

# ---------------- 停止 ------------------------------------------------------
stop_one() {
    local name="$1"
    local port; port=$(field "$name" 2)

    if ! port_listening "$port"; then
        say "  ${C_DIM}$name (:$port) 本来就没在跑${C_RST}"
        return 0
    fi

    local lpid master
    lpid=$(port_pids "$port" | head -1)
    if [ -z "$lpid" ]; then
        # 端口在监听但查不到 PID：通常是别的用户/容器占用，不该硬杀
        err "  $name (:$port) 端口被占用但查不到 PID，跳过（可能不属于当前用户）"
        return 1
    fi

    master=$(resolve_master "$lpid" "$port")
    if [ "$master" != "$lpid" ]; then
        say "  停止 $name (:$port) master=$master  ${C_DIM}[监听 worker=$lpid]${C_RST}"
    else
        say "  停止 $name (:$port) pid=$master"
    fi

    # 先温和地要求退出，让它有机会 flush 日志、释放 GPU 显存
    kill "$master" 2>/dev/null || true

    # 等端口真正消失 —— 判据是端口，不是进程
    local i
    for i in $(seq 1 15); do
        port_listening "$port" || break
        sleep 1
    done

    if port_listening "$port"; then
        warn "  优雅停止超时，强制结束 pid=$master"
        kill -9 "$master" 2>/dev/null || true
        # 兜底：清掉可能被 master 落下的孤儿 worker
        local p
        for p in $(port_pids "$port"); do kill -9 "$p" 2>/dev/null || true; done
        for i in $(seq 1 10); do
            port_listening "$port" || break
            sleep 1
        done
    fi

    if port_listening "$port"; then
        err "  $name (:$port) 端口仍未释放，后续启动会撞 Errno 98"
        audit "stop $name FAILED (port still held)"
        return 1
    fi

    ok "  $name 已停止"
    audit "stop $name ok"
    return 0
}

# ---------------- 启动 ------------------------------------------------------
start_one() {
    local name="$1"
    local port workdir kind py cmd health log
    port=$(field "$name" 2);   workdir=$(field "$name" 3)
    kind=$(field "$name" 4);   py=$(field "$name" 5)
    cmd=$(field "$name" 6);    health=$(field "$name" 7)
    log=$(field "$name" 8)

    # 已经健康就别动它 —— 幂等，定时任务重复触发也安全
    if port_listening "$port"; then
        if http_ok "$health" 5; then
            say "  ${C_DIM}$name (:$port) 已在运行且健康，跳过${C_RST}"
            return 0
        fi
        warn "  $name (:$port) 端口在监听但不健康，先停掉再重启"
        stop_one "$name" || return 1
    fi

    # 前置检查：路径不对就直接说清楚，而不是让 nohup 静默失败
    if [ ! -d "$workdir" ]; then
        err "  $name 工作目录不存在：$workdir"; audit "start $name FAILED (no workdir)"; return 1
    fi
    if [ ! -x "$py" ]; then
        err "  $name 解释器不可执行：$py"; audit "start $name FAILED (no interpreter)"; return 1
    fi

    # 日志轮转：单个日志涨到 200MB 就归档，避免 output.log 那种 200GB+ 的情况
    if [ -f "$log" ]; then
        local sz; sz=$(stat -c %s "$log" 2>/dev/null || echo 0)
        if [ "$sz" -gt 209715200 ]; then
            mv "$log" "$log.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
            say "  ${C_DIM}日志超 200MB 已归档${C_RST}"
        fi
    fi
    mkdir -p "$(dirname "$log")" 2>/dev/null || true

    step "启动 $name (:$port)"
    cd "$workdir" || { err "  无法进入 $workdir"; return 1; }

    # setsid 让新进程脱离当前会话：SSH 断开、cron 任务结束都不会带走它。
    #
    # 9>&- 是必须的：子进程默认会继承父进程所有打开的 fd，其中包括本脚本
    # 用来做单实例互斥的 fd 9（那把 flock）。服务是常驻进程，一旦继承了
    # 这个 fd，锁就等于被它永久持有 —— 之后任何一次 svc 调用都会在
    # flock 上干等 300 秒然后放弃。自测时就是这么卡住的，必须显式关掉。
    setsid nohup "$py" $cmd >> "$log" 2>&1 9>&- &
    local newpid=$!
    disown "$newpid" 2>/dev/null || true

    # 音频服务要把 ~900MB 模型读进 GPU，冷启动明显更慢，给它更长的窗口
    local budget=90
    [ "$name" = "audio" ] && budget=180

    local i
    for i in $(seq 1 "$budget"); do
        # 进程自己死了就别再等，直接把日志尾部摊开给人看
        if ! kill -0 "$newpid" 2>/dev/null; then
            # 注意：也可能是它 fork 出子进程后父进程正常退出，所以还要看端口
            if port_listening "$port" && http_ok "$health" 5; then
                ok "  $name 已就绪（:$port 健康）"
                audit "start $name ok"
                return 0
            fi
            err "  $name 启动进程已退出，日志尾部："
            tail -n 15 "$log" 2>/dev/null | sed 's/^/      /' >&2
            audit "start $name FAILED (process exited)"
            return 1
        fi
        if port_listening "$port" && http_ok "$health" 5; then
            ok "  $name 已就绪（pid=$newpid, :$port 健康）"
            audit "start $name ok pid=$newpid"
            return 0
        fi
        sleep 1
    done

    err "  $name 启动超时（${budget}s），日志尾部："
    tail -n 15 "$log" 2>/dev/null | sed 's/^/      /' >&2
    audit "start $name FAILED (timeout ${budget}s)"
    return 1
}

# ---------------- 状态 ------------------------------------------------------
cmd_status() {
    # 先把纯文本按宽度补齐，再套颜色。
    # 反过来做的话，颜色转义符会被 printf 算进字段宽度，列就全歪了。
    local fmt='%-10s %-7s %-8s %-8s %-6s %s\n'
    # shellcheck disable=SC2059
    printf "$fmt" SERVICE PORT PID PORT HEALTH UPTIME
    printf '%s\n' "-------------------------------------------------------------"
    local name port health lpid master up hstat pstat pad
    for name in $(all_names); do
        port=$(field "$name" 2); health=$(field "$name" 7)
        if port_listening "$port"; then
            lpid=$(port_pids "$port" | head -1)
            master=$(resolve_master "${lpid:-}" "$port")
            up=$(ps -o etime= -p "${master:-0}" 2>/dev/null | tr -d ' ')
            [ -z "$up" ] && up='-'
            pad=$(printf '%-8s' 'LISTEN'); pstat="${C_GRN}${pad}${C_RST}"
            if http_ok "$health" 6; then
                pad=$(printf '%-6s' 'ok');  hstat="${C_GRN}${pad}${C_RST}"
            else
                pad=$(printf '%-6s' 'bad'); hstat="${C_RED}${pad}${C_RST}"
            fi
        else
            master='-'; up='-'
            pad=$(printf '%-8s' 'down'); pstat="${C_RED}${pad}${C_RST}"
            hstat=$(printf '%-6s' '-')
        fi
        printf '%-10s %-7s %-8s %s %s %s\n' \
            "$name" "$port" "${master:--}" "$pstat" "$hstat" "$up"
    done
    printf '\n%s\n' "${C_DIM}操作日志：$ACTION_LOG${C_RST}"
}

# 只做健康检查，给监控/定时任务用：全好返回 0，有问题返回坏掉的个数
cmd_health() {
    local name port health bad=0
    for name in $(all_names); do
        port=$(field "$name" 2); health=$(field "$name" 7)
        if port_listening "$port" && http_ok "$health" 8; then
            ok "$name (:$port) 健康"
        else
            err "$name (:$port) 异常"
            bad=$((bad+1))
        fi
    done
    [ "$bad" -eq 0 ] && say "${C_GRN}全部正常${C_RST}"
    return "$bad"
}

# ---------------- 参数解析 --------------------------------------------------
# 把用户给的名字校验一遍；不给名字就返回全部
targets() {
    if [ "$#" -eq 0 ]; then all_names; return 0; fi
    local n rc=0
    for n in "$@"; do
        if exists "$n"; then printf '%s\n' "$n"
        else err "未知服务名：$n（可用：$(all_names | tr '\n' ' '))"; rc=1
        fi
    done
    return $rc
}

usage() {
    sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

ACTION="${1:-status}"; shift || true

case "$ACTION" in
    start)
        LIST=$(targets "$@") || exit 2
        rc=0
        for n in $LIST; do start_one "$n" || rc=1; done
        exit $rc
        ;;
    stop)
        LIST=$(targets "$@") || exit 2
        # 反序停止：主应用先走，微服务后走
        rc=0
        for n in $(printf '%s\n' "$LIST" | tac); do stop_one "$n" || rc=1; done
        exit $rc
        ;;
    restart)
        LIST=$(targets "$@") || exit 2
        audit "restart begin: $(printf '%s' "$LIST" | tr '\n' ' ')"
        rc=0
        for n in $(printf '%s\n' "$LIST" | tac); do stop_one "$n" || rc=1; done
        say ""
        for n in $LIST; do start_one "$n" || rc=1; done
        say ""
        cmd_status
        audit "restart end rc=$rc"
        exit $rc
        ;;
    status) cmd_status ;;
    health) cmd_health ;;
    logs)
        n="${1:-}"; lines="${2:-80}"
        exists "$n" || { err "用法：svc logs <名字> [行数]（可用：$(all_names | tr '\n' ' '))"; exit 2; }
        f=$(field "$n" 8)
        say "${C_DIM}==> $f（最后 $lines 行）${C_RST}"
        tail -n "$lines" "$f"
        ;;
    tail)
        n="${1:-}"
        exists "$n" || { err "用法：svc tail <名字>"; exit 2; }
        tail -f "$(field "$n" 8)"
        ;;
    -h|--help|help) usage ;;
    *) err "未知命令：$ACTION"; usage; exit 2 ;;
esac
