#!/usr/bin/env bash
# =============================================================================
# audio_service 源码同步 / 漂移检查
# =============================================================================
# 背景：音频微服务的运行目录在 /home/lsz/audio_service（与 openface_service
#       同级，便于两个微服务用同样的方式管理），但版本控制在 webapp 仓库里。
#       两处一旦不一致，就会出现「仓库里改了、线上跑的还是旧代码」这类
#       最难查的问题。本脚本让两者的差异始终显式可见。
#
# 用法：
#   bash sync_audio_service.sh check    # 只检查漂移，不改动（默认）
#   bash sync_audio_service.sh push     # 仓库 -> 运行目录（部署）
#   bash sync_audio_service.sh pull     # 运行目录 -> 仓库（把线上改动收回仓库）
#
# 注意：models/ 目录（SenseVoice 约 897MB）【不】纳入版本控制，
#       也不参与同步 —— 模型是数据不是代码。
# =============================================================================
set -uo pipefail

REPO_DIR="/home/lsz/webapp/audio_service"
RUN_DIR="/home/lsz/audio_service"
MODE="${1:-check}"

# 参与同步的代码文件（显式白名单，避免把日志/模型/缓存一起搬）
FILES=(
  features_handcrafted.py
  models_deep.py
  audio_service.py
  requirements.txt
)

# 只在「接入层」侧使用的文件：由 test2.py（rrpg_plus 环境）import，
# 不在音频微服务的 audio_linux 环境里运行。所以它的落点【不是】$RUN_DIR，
# 而是 test2.py 所在目录 —— 否则 test2.py 根本 import 不到。
CLIENT_FILES=(
  audio_client.py
)
CLIENT_DIR="/home/lsz/real_time_plus/real_time_Demo"

mkdir -p "$RUN_DIR"

drift=0
missing_repo=0
missing_run=0

# 检查一组文件在 仓库 与 目标目录 之间是否一致
# 用法：check_group <目标目录> <文件...>
check_group() {
  local dest="$1"; shift
  local f a b ma mb status
  for f in "$@"; do
    a="$REPO_DIR/$f"; b="$dest/$f"
    ma="-"; mb="-"
    [[ -f "$a" ]] && ma=$(md5sum "$a" | awk '{print $1}')
    [[ -f "$b" ]] && mb=$(md5sum "$b" | awk '{print $1}')

    if [[ "$ma" == "-" && "$mb" == "-" ]]; then
      status="尚未创建（跳过）"
    elif [[ "$ma" == "-" ]]; then
      status="⚠ 仓库缺失"; missing_repo=$((missing_repo+1)); drift=$((drift+1))
    elif [[ "$mb" == "-" ]]; then
      status="⚠ 目标目录缺失"; missing_run=$((missing_run+1)); drift=$((drift+1))
    elif [[ "$ma" == "$mb" ]]; then
      status="✅ 一致"
    else
      status="❌ 内容不一致"; drift=$((drift+1))
    fi
    printf '%-28s %-34s %-34s %s\n' "$f" "${ma:0:32}" "${mb:0:32}" "$status"
  done
}

# 复制一组文件：仓库 -> 目标目录
push_group() {
  local dest="$1"; shift
  local f
  for f in "$@"; do
    [[ -f "$REPO_DIR/$f" ]] && cp -v "$REPO_DIR/$f" "$dest/$f"
  done
}

# 复制一组文件：目标目录 -> 仓库
pull_group() {
  local dest="$1"; shift
  local f
  for f in "$@"; do
    [[ -f "$dest/$f" ]] && cp -v "$dest/$f" "$REPO_DIR/$f"
  done
}

printf '%-28s %-34s %-34s %s\n' "文件" "仓库 md5" "目标目录 md5" "状态"
printf '%s\n' "-------------------------------------------------------------------------------------------------------------"
echo "[微服务侧] -> $RUN_DIR"
check_group "$RUN_DIR" "${FILES[@]}"
echo "[接入层侧] -> $CLIENT_DIR"
check_group "$CLIENT_DIR" "${CLIENT_FILES[@]}"

echo
case "$MODE" in
  check)
    if [[ $drift -eq 0 ]]; then
      echo "无漂移：仓库与运行目录一致。"
      exit 0
    fi
    echo "检测到 $drift 处差异。用 'push'（仓库->运行）或 'pull'（运行->仓库）同步。"
    exit 1
    ;;
  push)
    push_group "$RUN_DIR" "${FILES[@]}"
    push_group "$CLIENT_DIR" "${CLIENT_FILES[@]}"
    echo "已同步：仓库 -> 运行目录 / 接入层目录。"
    echo "提示：audio_client.py 变更后需重启 test2.py 主服务才会生效（Python 已 import 的模块不会热更新）。"
    ;;
  pull)
    pull_group "$RUN_DIR" "${FILES[@]}"
    pull_group "$CLIENT_DIR" "${CLIENT_FILES[@]}"
    echo "已同步：运行目录 / 接入层目录 -> 仓库。"
    ;;
  *)
    echo "未知模式：$MODE（可用 check / push / pull）"; exit 2
    ;;
esac
