#!/usr/bin/env bash
# =============================================================================
# boot_all —— 全站服务统一拉起（跨项目编排）
# =============================================================================
# svc.sh 只管 real_time 那三个服务（8801/5002/5003），而这台机器上实际还跑着
# nginx 入口、asthmaguard 的前后端、SPT 风团测量。2026-09-03 排查时发现：
# WSL 实例重启后，除了 Docker 容器（unless-stopped 会自愈）之外，
# 其余全部是停的，每次都要人工逐个敲命令。这个脚本把那套手工流程固化下来。
#
# 用法：
#   boot_all.sh              拉起所有未在跑的服务（幂等，已健康的跳过）
#   boot_all.sh status       只看状态，不做任何改动
#   boot_all.sh --only NAME  只处理指定服务（可重复：--only nginx --only spt）
#
# 服务名：docker / nginx / realtime / algo / backend / spt
#
# 设计约束（都是踩过的坑）：
#   1) 幂等。判据是「端口在监听 + HTTP 真的应答」，不是「进程存在」。
#      端口可能被没死透的旧进程占着，进程也可能活着但应用早已卡死。
#   2) 只补不重启。已经健康的服务一律不动 —— 这个脚本会被登录钩子反复触发，
#      要是每次都 restart，用户正在做的检测就被踢断了。
#   3) 顺序有依赖。Docker（数据库）必须在 asthmaguard 后端之前，
#      否则后端起来连不上库，gunicorn worker 会反复重启。
#   4) 前台脚本必须 setsid 脱离会话，否则 SSH 一断、cron 一结束就被带走。
#      asthmaguard 的 start_*.sh 结尾是 exec gunicorn（前台阻塞），
#      直接调用会把调用方挂死，必须放后台。
# =============================================================================
set -uo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export LANG="${LANG:-en_US.UTF-8}"

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBAPP_DIR="$(cd "$OPS_DIR/.." && pwd)"
LOG_DIR="$WEBAPP_DIR/logs"
mkdir -p "$LOG_DIR"
BOOT_LOG="$LOG_DIR/boot_all.log"

ASTHMA_DIR=/home/lsz/asthmaguard
SPT_DIR=/home/lsz/SPT

# ---- 单实例锁：登录钩子和 cron 可能同时触发，不能让两边抢着起同一个端口 ----
# fd 8：刻意避开 svc.sh 用的 fd 9，两把锁不能互相干扰。
#
# ⚠️ 这把锁有个致命陷阱，2026-09-03 亲手踩过一次：
#    子进程默认继承父进程所有打开的 fd。本脚本要启动的都是常驻服务，
#    只要有一个服务进程继承了 fd 8，这把锁就等于被它永久持有 ——
#    此后每一次 boot_all 都会卡在 flock 上。当时 SPT 的 python
#    （经 SPT/svc.sh 内部 nohup 启动）就这么把锁扣住了，
#    watchdog 每 10 分钟堆积一个僵死进程。
#    因此下面每一处启动服务的地方都必须显式 8>&-，一个都不能漏，
#    包括通过外部脚本间接启动的（外部脚本内部再 fork 也会继承）。
#
# 用非阻塞 -n 而不是 -w 600：上一轮还没跑完，说明要么正在启动服务
# （这一轮本来也没事可做），要么已经卡死（干等只会堆积更多进程）。
# 两种情况都该立刻退出，而不是排队。
LOCK_FILE="$LOG_DIR/.boot_all.lock"
exec 8>"$LOCK_FILE"
if ! flock -n 8; then
    echo "另一个 boot_all 正在运行，本次跳过。" >&2
    exit 0
fi

# ---------------- 输出着色（非终端自动关闭，日志里不留控制字符） -------------
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

audit() {
    printf '%s [%s] %s\n' "$(date '+%F %T')" "${SVC_INVOKER:-manual}" "$*" >> "$BOOT_LOG"
}

# ---------------- 探测原语 --------------------------------------------------
port_listening() {
    ss -tlnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"
}

# -k 容忍自签证书：8801 和 9443 都是自签，不加会一律失败
http_ok() {
    local url="$1" timeout="${2:-5}" code
    [ "$url" = "-" ] && return 0
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time "$timeout" "$url" 2>/dev/null || true)
    case "$code" in
        200|204|301|302|401|403) return 0 ;;
        *) return 1 ;;
    esac
}

# 等待「端口监听 + HTTP 应答」同时成立
wait_ready() {
    local port="$1" health="$2" budget="${3:-60}" i
    for i in $(seq 1 "$budget"); do
        if port_listening "$port" && http_ok "$health" 5; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# ---------------- 各服务的拉起逻辑 ------------------------------------------

start_docker() {
    step "Docker 基础服务（MySQL 3307/3308、Redis 6379、MinIO 9000/9001）"
    if ! command -v docker >/dev/null 2>&1; then
        warn "  未安装 docker，跳过"; return 0
    fi
    # WSL 下 docker daemon 不一定随实例起来
    if ! docker info >/dev/null 2>&1; then
        say "  docker daemon 未响应，尝试拉起…"
        sudo -n service docker start >/dev/null 2>&1 || true
        local i
        for i in $(seq 1 30); do
            docker info >/dev/null 2>&1 && break
            sleep 1
        done
    fi
    if ! docker info >/dev/null 2>&1; then
        err "  docker daemon 无法启动，asthmaguard 后端将连不上数据库"
        audit "docker FAILED (daemon down)"
        return 1
    fi

    # 容器是 unless-stopped，daemon 起来后通常会自愈；这里只补被显式停掉的
    local c missing=0
    for c in asthmaguard-mysql-product asthmaguard-mysql-research \
             asthmaguard-redis asthmaguard-minio; do
        if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" != "true" ]; then
            say "  启动容器 $c"
            docker start "$c" >/dev/null 2>&1 || { err "  $c 启动失败"; missing=1; }
        fi
    done

    # 等数据库真正可接受连接 —— 容器 Running 不等于 MySQL 已就绪，
    # 后端在这个空窗期连上去会直接崩掉 worker。
    local i
    for i in $(seq 1 60); do
        if [ "$(docker inspect -f '{{.State.Health.Status}}' asthmaguard-mysql-product 2>/dev/null)" = "healthy" ]; then
            break
        fi
        sleep 1
    done

    for c in asthmaguard-mysql-product asthmaguard-mysql-research \
             asthmaguard-redis asthmaguard-minio; do
        printf '    %-30s %s\n' "$c" "$(docker inspect -f '{{.State.Status}} ({{.State.Health.Status}})' "$c" 2>/dev/null || echo '不存在')"
    done
    [ "$missing" -eq 0 ] && ok "  Docker 基础服务就绪" || warn "  部分容器未起来"
    audit "docker done missing=$missing"
    return 0
}

start_nginx() {
    step "Nginx 入口（80 / 443 / 8080 / 8443 / 9443）"
    if pgrep -x nginx >/dev/null 2>&1 && port_listening 9443; then
        say "  ${C_DIM}已在运行，跳过${C_RST}"; return 0
    fi
    # 起之前先验配置：语法错误时 nginx 会静默失败，事后很难看出原因
    if ! sudo -n nginx -t >/dev/null 2>&1; then
        err "  nginx 配置校验失败："
        sudo -n nginx -t 2>&1 | sed 's/^/      /' >&2
        audit "nginx FAILED (config test)"
        return 1
    fi
    sudo -n nginx 2>/dev/null || sudo -n service nginx start >/dev/null 2>&1
    sleep 2
    if port_listening 9443 && port_listening 8443; then
        ok "  Nginx 已就绪"; audit "nginx ok"; return 0
    fi
    err "  Nginx 启动后端口未监听"; audit "nginx FAILED (no listen)"; return 1
}

start_realtime() {
    step "real_time 三服务（8801 主应用 / 5002 OpenFace / 5003 Audio）"
    # 复用既有 svc.sh：它对停服判据、gunicorn master 定位、模型冷启动
    # 超时都有专门处理，没必要在这里重造一遍。
    # 8>&- 必须加：svc.sh 会 setsid 出常驻服务进程，不关掉就把本脚本的
    # 单实例锁一起继承走了（svc.sh 自己的 fd 9 它内部已经处理）。
    SVC_INVOKER="${SVC_INVOKER:-boot_all}" "$OPS_DIR/svc.sh" start 8>&- 2>&1 | sed 's/^/  /'
    local rc=${PIPESTATUS[0]}
    audit "realtime rc=$rc"
    return "$rc"
}

start_algo() {
    step "asthmaguard 算法服务（8010）"
    if port_listening 8010 && http_ok "http://127.0.0.1:8010/health" 5; then
        say "  ${C_DIM}已在运行且健康，跳过${C_RST}"; return 0
    fi
    [ -x "$ASTHMA_DIR/start_algo.sh" ] || { err "  找不到 start_algo.sh"; return 1; }

    cd "$ASTHMA_DIR" || return 1
    # start_algo.sh 结尾是 exec gunicorn（前台阻塞），必须 setsid 放后台，
    # 否则会把调用方（登录 shell / cron）一直挂住。
    # 8>&- 关掉继承的锁 fd：常驻进程一旦继承，这把锁就等于被永久持有。
    setsid nohup bash "$ASTHMA_DIR/start_algo.sh" >> "$ASTHMA_DIR/algo.log" 2>&1 8>&- < /dev/null &
    disown 2>/dev/null || true

    if wait_ready 8010 "http://127.0.0.1:8010/health" 90; then
        ok "  算法服务已就绪"; audit "algo ok"; return 0
    fi
    err "  算法服务启动超时，日志尾部："
    tail -n 15 "$ASTHMA_DIR/algo.log" 2>/dev/null | sed 's/^/      /' >&2
    audit "algo FAILED (timeout)"
    return 1
}

start_backend() {
    step "asthmaguard 产品后端（8000）"
    if port_listening 8000 && http_ok "http://127.0.0.1:8000/docs" 5; then
        say "  ${C_DIM}已在运行且健康，跳过${C_RST}"; return 0
    fi
    [ -x "$ASTHMA_DIR/start_backend.sh" ] || { err "  找不到 start_backend.sh"; return 1; }

    cd "$ASTHMA_DIR" || return 1
    setsid nohup bash "$ASTHMA_DIR/start_backend.sh" >> "$ASTHMA_DIR/backend.log" 2>&1 8>&- < /dev/null &
    disown 2>/dev/null || true

    # 8 个 worker 各自建连接池，比算法服务慢，窗口给宽些
    if wait_ready 8000 "http://127.0.0.1:8000/docs" 120; then
        ok "  产品后端已就绪"; audit "backend ok"; return 0
    fi
    err "  产品后端启动超时，日志尾部："
    tail -n 15 "$ASTHMA_DIR/backend.log" 2>/dev/null | sed 's/^/      /' >&2
    audit "backend FAILED (timeout)"
    return 1
}

start_spt() {
    step "SPT 风团测量服务（8850）"
    if port_listening 8850 && http_ok "http://127.0.0.1:8850/health" 5; then
        say "  ${C_DIM}已在运行且健康，跳过${C_RST}"; return 0
    fi
    [ -f "$SPT_DIR/svc.sh" ] || { err "  找不到 SPT/svc.sh"; return 1; }

    # SPT 自带 svc.sh，内部已经 nohup，直接调用即可。
    # 8>&- 是这里最关键的一笔：SPT/svc.sh 内部用 `nohup python app.py &`
    # 启动常驻进程，它不像本仓库的 svc.sh 那样会自己关闭继承来的 fd。
    # 漏掉这个的后果是 SPT 的 python 永久持有 boot_all 的锁，
    # 之后每次巡检都卡死 —— 这个 bug 真实发生过，别再去掉。
    bash "$SPT_DIR/svc.sh" start 8>&- 2>&1 | sed 's/^/  /'

    # 要加载 SAM 模型，冷启动比端口监听晚不少
    if wait_ready 8850 "http://127.0.0.1:8850/health" 120; then
        # 光看 HTTP 200 不够：模型没加载成功时它照样返回 200，
        # 只是 sam_loaded=false，功能其实是残的。
        local body; body=$(curl -s --max-time 5 http://127.0.0.1:8850/health 2>/dev/null)
        if printf '%s' "$body" | grep -q '"sam_loaded":true'; then
            ok "  SPT 已就绪（sam_loaded=true）"
        else
            warn "  SPT 端口通了但模型未加载：$body"
        fi
        audit "spt ok"
        return 0
    fi
    err "  SPT 启动超时，日志尾部："
    tail -n 15 "$SPT_DIR/run/svc.log" 2>/dev/null | sed 's/^/      /' >&2
    audit "spt FAILED (timeout)"
    return 1
}

# ---------------- 状态总览 --------------------------------------------------
cmd_status() {
    printf '%-26s %-7s %-9s %-7s\n' SERVICE PORT PORT HEALTH
    printf '%s\n' "----------------------------------------------------"
    _row() {
        local label="$1" port="$2" health="$3" pstat hstat pad
        if port_listening "$port"; then
            pad=$(printf '%-9s' 'LISTEN'); pstat="${C_GRN}${pad}${C_RST}"
            if http_ok "$health" 6; then
                pad=$(printf '%-7s' 'ok');  hstat="${C_GRN}${pad}${C_RST}"
            else
                pad=$(printf '%-7s' 'bad'); hstat="${C_RED}${pad}${C_RST}"
            fi
        else
            pad=$(printf '%-9s' 'down'); pstat="${C_RED}${pad}${C_RST}"
            hstat=$(printf '%-7s' '-')
        fi
        printf '%-26s %-7s %s %s\n' "$label" "$port" "$pstat" "$hstat"
    }
    _row "nginx (https 入口)"   9443 "https://127.0.0.1:9443/users"
    _row "nginx (saas)"         8443 "https://127.0.0.1:8443/"
    _row "nginx (http 入口)"    8080 "http://127.0.0.1:8080/users"
    _row "realtime main"        8801 "https://127.0.0.1:8801/max"
    _row "realtime openface"    5002 "http://127.0.0.1:5002/health"
    _row "realtime audio"       5003 "http://127.0.0.1:5003/health"
    _row "asthmaguard backend"  8000 "http://127.0.0.1:8000/docs"
    _row "asthmaguard algo"     8010 "http://127.0.0.1:8010/health"
    _row "SPT wheal-measure"    8850 "http://127.0.0.1:8850/health"
    _row "mysql product"        3308 "-"
    _row "mysql research"       3307 "-"
    _row "redis"                6379 "-"
    _row "minio api"            9000 "-"
    printf '\n%s\n' "${C_DIM}操作日志：$BOOT_LOG${C_RST}"
}

# ---------------- 参数解析 --------------------------------------------------
ALL_STAGES="docker nginx realtime algo backend spt"
SELECTED=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        status) cmd_status; exit 0 ;;
        --only) shift; SELECTED="$SELECTED ${1:-}" ;;
        -h|--help)
            sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) err "未知参数：$1"; exit 2 ;;
    esac
    shift
done

STAGES="${SELECTED:-$ALL_STAGES}"

say ""
say "==================================================================="
say "全站服务拉起：$(date '+%F %T %Z')"
say "==================================================================="
audit "boot_all begin: $STAGES"

rc=0
for s in $STAGES; do
    case "$s" in
        docker)   start_docker   || rc=1 ;;
        nginx)    start_nginx    || rc=1 ;;
        realtime) start_realtime || rc=1 ;;
        algo)     start_algo     || rc=1 ;;
        backend)  start_backend  || rc=1 ;;
        spt)      start_spt      || rc=1 ;;
        *) err "未知服务名：$s（可用：$ALL_STAGES）"; rc=2 ;;
    esac
    say ""
done

cmd_status
audit "boot_all end rc=$rc"
exit "$rc"
