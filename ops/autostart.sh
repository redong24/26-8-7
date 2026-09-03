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
# ---- 0) 保证 cron 守护进程在跑 —— 这一步刻意放在节流之前 ----
# 理由：cron 是整条自愈链的根。@reboot 拉服务、*/10 巡检、05:00 重启，
# 全都挂在 cron 上；cron 自己没起来，后面所有兜底机制一起失效。
# 而 WSL 实例重启后 cron 默认就是不运行的（本机 systemd 未生效，
# 没有服务管理器帮它起来），这恰恰是最需要自愈的时刻。
# 若放在节流之后，"6 小时内已跑过" 就会把这次检查跳掉，
# 于是实例重启后整整 6 小时处于无人值守状态。
# 这个检查只有一次 pgrep，代价可以忽略，不值得为它省。
if ! pgrep -x cron >/dev/null 2>&1 && ! pgrep -x crond >/dev/null 2>&1; then
    if sudo -n service cron start >/dev/null 2>&1; then
        echo "$(date '+%F %T') 已拉起 cron 守护进程" >> "$LOG"
    else
        echo "$(date '+%F %T') cron 未运行且无免密权限（定时任务不会触发，需手动 sudo service cron start）" >> "$LOG"
    fi
fi

# ---- 节流：补服务这件事没必要每开一个终端就做一次 ----
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

# ---- 2) 缺谁补谁 ----
# 这里用 start 而不是 restart：start 对健康的服务是空操作，
# 不会把正在做检测的用户踢下线。
#
# 2026-09-03 起改调 boot_all.sh：原先只拉 svc.sh 管的三个服务
# （8801/5002/5003），nginx、asthmaguard 前后端、SPT 都在覆盖范围外，
# WSL 实例重启后这些全是停的，每次都要人工补。boot_all 同样是幂等的，
# 已健康的服务一律跳过，所以换过来不会增加任何打断风险。
if [ -x "$OPS_DIR/boot_all.sh" ]; then
    "$OPS_DIR/boot_all.sh"
else
    # 兜底：boot_all 不在就退回原来的行为，至少把主服务拉起来
    echo "boot_all.sh 缺失，回退到 svc.sh start"
    "$OPS_DIR/svc.sh" start
fi
rc=$?
echo "autostart 结束 rc=$rc"
exit "$rc"
