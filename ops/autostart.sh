#!/usr/bin/env bash
# =============================================================================
# autostart —— 登录时的兜底自启（由 ~/.profile 静默调用）
# =============================================================================
# 存在理由：WSL 实例随 Windows 端按需启停，没有传统的开机流程，systemd 在
# 本机也未生效。所以「开机自启」只能挂在最可靠的触发点上 —— 登录 shell。
#
# 它做两件事：
#   1) 尽力拉起 cron 守护进程（免密可用时），让 05:00 的定时任务真的会触发。
#   2) 发现有服务没在跑就补起来。已经在跑的一律不动（svc start 本身幂等）。
#
# 三条自我约束，避免它变成负担：
#   * 节流：默认 6 小时内只跑一次，开十个终端也不会重复折腾。
#   * 静默：所有输出进日志，不往终端打字，不拖慢 shell 启动。
#   * 幂等：只补不重启，绝不打断正在跑的服务（也就不会踢掉在线用户）。
#
# 手动验证：bash ops/autostart.sh --force
# =============================================================================

set -uo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export SVC_INVOKER="autostart"

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$(cd "$OPS_DIR/.." && pwd)/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/autostart.log"
STAMP="$LOG_DIR/.autostart.stamp"
THROTTLE_SEC=$((6 * 3600))

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# ---- 节流 ----
if [ "$FORCE" -eq 0 ] && [ -f "$STAMP" ]; then
    last=$(stat -c %Y "$STAMP" 2>/dev/null || echo 0)
    now=$(date +%s)
    if [ $((now - last)) -lt "$THROTTLE_SEC" ]; then
        exit 0
    fi
fi
touch "$STAMP"

exec >> "$LOG" 2>&1
echo ""
echo "--- autostart $(date '+%F %T') ---"

# ---- 1) 尽力保证 cron 守护进程在跑（不成功也不影响后面） ----
if ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
    if sudo -n service cron start >/dev/null 2>&1; then
        echo "已拉起 cron 守护进程"
    else
        echo "cron 未运行且无免密权限，跳过（05:00 定时任务不会触发，需手动 sudo service cron start）"
    fi
fi

# ---- 2) 缺谁补谁 ----
# 这里用 start 而不是 restart：start 对健康的服务是空操作，
# 不会把正在做检测的用户踢下线。
"$OPS_DIR/svc.sh" start
rc=$?
echo "autostart 结束 rc=$rc"
exit "$rc"
