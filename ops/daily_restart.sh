#!/usr/bin/env bash
# =============================================================================
# daily_restart —— 每天 05:00 的定时重启入口（由 cron 调用）
# =============================================================================
# 为什么不让 cron 直接调 svc.sh restart：
#   1) cron 的环境变量极其贫瘠（没有 PATH 里的 conda、没有 LANG），
#      需要在这里补齐，否则同样的命令「手工能跑、定时跑不了」。
#   2) 需要把每次定时重启的完整输出单独留档。凌晨没人盯着，
#      出问题时只有日志能说明发生了什么。
#   3) 重启后要复检一次健康状态；失败要重试，而不是留下一地死服务。
#
# 手动跑一次（用于验证定时逻辑本身）：
#   bash /home/lsz/webapp/ops/daily_restart.sh
# =============================================================================

set -uo pipefail

# cron 环境下 PATH 通常只有 /usr/bin:/bin，ss / curl / flock 可能找不到
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
# 日志里的中文不要变成 ? 号
export LANG="${LANG:-en_US.UTF-8}"
# 标记调用来源，写进 svc 的审计日志，用来区分「定时」和「手工」
export SVC_INVOKER="cron-daily"

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$(cd "$OPS_DIR/.." && pwd)/logs"
mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/daily_restart.log"

# 所有输出同时进日志和标准输出：手动执行时看得见，cron 执行时留得下
exec > >(tee -a "$RUN_LOG") 2>&1

echo ""
echo "==================================================================="
echo "定时重启开始：$(date '+%F %T %Z')"
echo "==================================================================="

# --- 重启 ---
"$OPS_DIR/svc.sh" restart
rc=$?

# --- 复检：restart 自己会判健康，但这里再独立确认一次 ---
# 有些服务（尤其是加载大模型的音频服务）可能刚好在 restart 的等待窗口
# 边缘就绪，稍等再查一次能减少误报。
if [ "$rc" -ne 0 ]; then
    echo ""
    echo "首轮重启返回非 0（rc=$rc），等待 20s 后复检..."
    sleep 20
    if "$OPS_DIR/svc.sh" health; then
        echo "复检通过：服务实际已就绪，视为成功。"
        rc=0
    else
        echo ""
        echo "复检仍失败，对未就绪的服务做一次补启动..."
        "$OPS_DIR/svc.sh" start
        sleep 10
        if "$OPS_DIR/svc.sh" health; then
            echo "补启动后恢复正常。"
            rc=0
        else
            echo "补启动后依然异常 —— 需要人工介入。"
            rc=1
        fi
    fi
fi

# --- 清理陈旧归档：只留最近 10 个轮转日志，避免磁盘被日志吃掉 ---
# 现网 output.log 曾涨到 200GB+、归档 40 个共 2GB，这里主动收口。
for d in /home/lsz/real_time_plus/real_time_Demo /home/lsz/audio_service /home/lsz/openface_service; do
    ls -1t "$d"/*.log.2* 2>/dev/null | tail -n +11 | while read -r f; do
        rm -f "$f" && echo "清理旧日志：$f"
    done
done

echo ""
if [ "$rc" -eq 0 ]; then
    echo "定时重启完成：全部服务正常  $(date '+%F %T')"
else
    echo "定时重启结束但存在异常，请查看上方日志  $(date '+%F %T')"
fi
echo "==================================================================="

exit "$rc"
