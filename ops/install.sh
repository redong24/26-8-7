#!/usr/bin/env bash
# =============================================================================
# install —— 安装「每天 05:00 定时重启」+「svc 快捷命令」
# =============================================================================
# 用法：
#   bash ops/install.sh            安装
#   bash ops/install.sh --status   查看当前安装状态
#   bash ops/install.sh --remove   卸载（撤掉定时任务与快捷命令）
#
# 本机环境（2026-08-21 实测）决定了这里的方案选择：
#
#   * PID 1 是 /init，systemctl 报 "System has not been booted with systemd"，
#     即 /etc/wsl.conf 里虽然写了 systemd=true，但当前这次会话并未生效。
#     => 不能用 systemd service / timer。
#   * cron 已安装（/usr/sbin/cron）但守护进程没在跑，且当前用户没有免密 sudo。
#     => crontab 能写入（已验证），但需要 cron 守护进程被拉起才会真正触发。
#        本脚本会写好 crontab，并在无法自启守护进程时明确告知需要执行的
#        那一条 sudo 命令，而不是假装安装成功。
#
# 「开机自启」在 WSL 下的现实：WSL 实例是随 Windows 端按需启停的，
# 没有传统意义的 boot。可靠做法是让 cron 守护进程随 WSL 启动被拉起，
# 因此本脚本同时提供 ~/.profile 兜底钩子（见 install_profile_hook）。
# =============================================================================

set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$OPS_DIR/.." && pwd)"
SVC="$OPS_DIR/svc.sh"
DAILY="$OPS_DIR/daily_restart.sh"
BIN_DIR="$HOME/bin"
CRON_TAG="# >>> psy-svc daily restart >>>"
CRON_TAG_END="# <<< psy-svc daily restart <<<"
HOOK_TAG="# >>> psy-svc autostart hook >>>"
HOOK_TAG_END="# <<< psy-svc autostart hook <<<"
PROFILE="$HOME/.profile"

C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_RED=$'\033[31m'
C_BLU=$'\033[36m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'; C_BLD=$'\033[1m'
ok()   { printf '%s\n' "${C_GRN}✔${C_RST} $*"; }
warn() { printf '%s\n' "${C_YEL}!${C_RST} $*"; }
err()  { printf '%s\n' "${C_RED}x${C_RST} $*"; }
step() { printf '\n%s\n' "${C_BLU}▸${C_RST} ${C_BLD}$*${C_RST}"; }

MODE="${1:-install}"

# ---------------------------------------------------------------------------
# 快捷命令：~/bin/svc -> ops/svc.sh
# 用 wrapper 而不是软链，是为了让 svc.sh 里的 BASH_SOURCE 始终解析到仓库
# 真实路径，配置文件才找得到。
# ---------------------------------------------------------------------------
install_shortcut() {
    step "安装快捷命令 svc"
    mkdir -p "$BIN_DIR"
    cat > "$BIN_DIR/svc" <<EOF
#!/usr/bin/env bash
# 由 $OPS_DIR/install.sh 生成，请勿手工编辑
exec "$SVC" "\$@"
EOF
    chmod +x "$BIN_DIR/svc"
    ok "已生成 $BIN_DIR/svc"

    # 确保 ~/bin 在 PATH 里。Ubuntu 的默认 .profile 里有一段
    # 「if [ -d "$HOME/bin" ]」的逻辑，但它只在登录 shell 生效且要求
    # 目录在读取时已存在 —— 目录是我们刚建的，所以显式补一条更稳。
    if ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
        if ! grep -q 'psy-svc PATH' "$HOME/.bashrc" 2>/dev/null; then
            {
                echo ''
                echo '# psy-svc PATH（由 ops/install.sh 追加）'
                echo "export PATH=\"\$HOME/bin:\$PATH\""
            } >> "$HOME/.bashrc"
            ok "已把 ~/bin 加入 PATH（写在 ~/.bashrc）"
        fi
        warn "当前这个 shell 还没生效，先执行： export PATH=\"\$HOME/bin:\$PATH\""
    else
        ok "~/bin 已在 PATH 中"
    fi
}

# ---------------------------------------------------------------------------
# 定时任务：每天 05:00
# ---------------------------------------------------------------------------
install_cron() {
    step "安装定时任务（每天 05:00 重启）"

    local current new
    current=$(crontab -l 2>/dev/null | sed "/$CRON_TAG/,/$CRON_TAG_END/d")

    new=$(cat <<EOF
$current
$CRON_TAG
# 每天 05:00 重启 8801 / 5002 / 5003 三个服务。
# CRON_TZ 显式声明时区：cron 默认用系统时区，写明了就不会因为
# 以后改时区而悄悄偏移（本机为 Asia/Shanghai）。
CRON_TZ=Asia/Shanghai
0 5 * * * /usr/bin/env bash $DAILY >> $REPO_DIR/logs/cron.log 2>&1
$CRON_TAG_END
EOF
)
    # 去掉可能产生的前导空行，保持 crontab 干净
    printf '%s\n' "$new" | sed '/^$/N;/^\n$/D' | crontab -
    if crontab -l 2>/dev/null | grep -q "$DAILY"; then
        ok "crontab 已写入：每天 05:00（Asia/Shanghai）"
    else
        err "crontab 写入失败"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# cron 守护进程：写了 crontab 不等于会被触发，守护进程必须在跑
# ---------------------------------------------------------------------------
ensure_crond() {
    step "检查 cron 守护进程"

    if pgrep -x cron >/dev/null 2>&1 || pgrep -x crond >/dev/null 2>&1; then
        ok "cron 守护进程正在运行"
        return 0
    fi

    warn "cron 守护进程当前【没有】运行 —— crontab 已写好，但不会被触发。"

    # 试着无密码拉起（多数环境会失败，失败是预期内的，不当作错误）
    if sudo -n true 2>/dev/null; then
        if sudo -n service cron start >/dev/null 2>&1 || sudo -n cron >/dev/null 2>&1; then
            sleep 1
            if pgrep -x cron >/dev/null 2>&1; then
                ok "已自动启动 cron 守护进程"
                return 0
            fi
        fi
    fi

    printf '%s\n' "${C_YEL}"
    cat <<'EOF'
  需要你手动执行下面这一条（要输入密码，只需做一次）：

      sudo service cron start

  想让它以后随 WSL 自动起来，再执行这一条（把 cron 加进免密白名单）：

      echo "$USER ALL=(root) NOPASSWD: /usr/sbin/service cron start" \
        | sudo tee /etc/sudoers.d/psy-svc-cron

  执行完后再跑一次： bash ops/install.sh --status
EOF
    printf '%s' "${C_RST}"
    return 1
}

# ---------------------------------------------------------------------------
# 兜底自启钩子：WSL 没有真正的 boot，登录 shell 是最可靠的触发点。
# 作用有两个：① 尽力拉起 cron 守护进程；② 服务掉线时顺手补起来。
# 加了 24 小时节流，避免每开一个终端都执行一遍。
# ---------------------------------------------------------------------------
install_profile_hook() {
    step "安装登录自启兜底钩子（~/.profile）"

    # 先删旧块，保证可重复执行
    if [ -f "$PROFILE" ]; then
        sed -i "/$HOOK_TAG/,/$HOOK_TAG_END/d" "$PROFILE"
    fi

    cat >> "$PROFILE" <<EOF

$HOOK_TAG
# WSL 下没有传统 boot，systemd 也未生效，因此把「确保服务在跑」挂在
# 登录 shell 上做兜底。带节流标记，不会每开一个终端就跑一次。
# 只在交互式登录时执行，且全程静默 + 后台，绝不拖慢终端启动。
if [ -n "\$PS1" ] && [ -x "$OPS_DIR/autostart.sh" ]; then
    ( "$OPS_DIR/autostart.sh" >/dev/null 2>&1 & )
fi
$HOOK_TAG_END
EOF
    ok "已写入 $PROFILE"
}

show_status() {
    step "当前安装状态"

    if [ -x "$BIN_DIR/svc" ]; then ok "快捷命令 ~/bin/svc 已安装"
    else warn "快捷命令未安装"; fi

    if crontab -l 2>/dev/null | grep -q "$DAILY"; then
        ok "定时任务已配置："
        crontab -l 2>/dev/null | grep -E '^(CRON_TZ|0 5)' | sed 's/^/    /'
    else
        warn "定时任务未配置"
    fi

    if pgrep -x cron >/dev/null 2>&1 || pgrep -x crond >/dev/null 2>&1; then
        ok "cron 守护进程在运行（定时任务会按时触发）"
    else
        err "cron 守护进程未运行 —— 定时任务不会触发，需执行： sudo service cron start"
    fi

    if grep -q "$HOOK_TAG" "$PROFILE" 2>/dev/null; then
        ok "登录自启兜底钩子已安装"
    else
        warn "登录自启兜底钩子未安装"
    fi

    step "服务实时状态"
    "$SVC" status
}

remove_all() {
    step "卸载"
    crontab -l 2>/dev/null | sed "/$CRON_TAG/,/$CRON_TAG_END/d" | crontab - 2>/dev/null \
        && ok "已移除定时任务"
    [ -f "$PROFILE" ] && sed -i "/$HOOK_TAG/,/$HOOK_TAG_END/d" "$PROFILE" \
        && ok "已移除自启钩子"
    rm -f "$BIN_DIR/svc" && ok "已移除快捷命令"
    warn "注意：正在运行的服务不受影响，如需停止请执行 $SVC stop"
}

case "$MODE" in
    --status|status) show_status ;;
    --remove|remove) remove_all ;;
    install)
        chmod +x "$SVC" "$DAILY" "$OPS_DIR/autostart.sh" 2>/dev/null
        install_shortcut
        install_cron
        install_profile_hook
        ensure_crond || true
        echo ""
        show_status
        cat <<EOF

$(printf '%s' "${C_BLD}")常用命令$(printf '%s' "${C_RST}")
  svc status          看状态
  svc restart         一键重启全部
  svc restart audio   只重启音频服务
  svc stop / start    一键停 / 起
  svc logs main 100   看主应用最后 100 行日志

$(printf '%s' "${C_DIM}")定时重启日志：$REPO_DIR/logs/daily_restart.log
操作审计日志：$REPO_DIR/logs/svc_actions.log$(printf '%s' "${C_RST}")
EOF
        ;;
    *) err "未知参数：$MODE（可用 install / --status / --remove）"; exit 2 ;;
esac
