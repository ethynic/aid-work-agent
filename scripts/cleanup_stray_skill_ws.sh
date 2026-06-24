#!/usr/bin/env bash
#
# 清理项目根目录下散落的 skill 工作目录（skill_ws_*）。
#
# 背景：旧版 agent.py 用 tempfile.mkdtemp 创建工作目录时未指定 dir，
# 导致 skill_ws_* 目录散落在项目根目录。语音等附件在
# storage/tenants/{tenant_id}/conversation/ 下已有持久副本，
# 这些散落目录可安全删除。
#
# 用法：
#   ./cleanup_stray_skill_ws.sh            # 预演：只列出将删除的目录，不实际删除
#   ./cleanup_stray_skill_ws.sh --delete   # 实际删除
#
# 可选环境变量：
#   TARGET_DIR  要清理的根目录，默认 /var/www/agent2

set -euo pipefail

TARGET_DIR="${TARGET_DIR:-/var/www/agent2}"
DELETE=0

for arg in "$@"; do
  case "$arg" in
    --delete) DELETE=1 ;;
    *) echo "未知参数: $arg" >&2; exit 1 ;;
  esac
done

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "目录不存在: $TARGET_DIR" >&2
  exit 1
fi

# 只匹配根目录直接子项中名为 skill_ws_* 的目录，避免误删 storage/ 下的新目录
mapfile -t DIRS < <(find "$TARGET_DIR" -maxdepth 1 -type d -name 'skill_ws_*' | sort)

count="${#DIRS[@]}"
if [[ "$count" -eq 0 ]]; then
  echo "未发现 ${TARGET_DIR}/skill_ws_* 目录，无需清理。"
  exit 0
fi

echo "在 ${TARGET_DIR} 下发现 ${count} 个 skill_ws_* 目录："
total_size=0
for d in "${DIRS[@]}"; do
  size=$(du -sh "$d" 2>/dev/null | cut -f1)
  echo "  $d  ($size)"
done

if [[ "$DELETE" -ne 1 ]]; then
  echo
  echo "[预演模式] 未删除任何文件。确认无误后执行："
  echo "  $0 --delete"
  exit 0
fi

echo
read -r -p "确认删除以上 ${count} 个目录？(yes/no) " ans
if [[ "$ans" != "yes" ]]; then
  echo "已取消。"
  exit 0
fi

for d in "${DIRS[@]}"; do
  rm -rf -- "$d"
  echo "已删除: $d"
done

echo "清理完成，共删除 ${count} 个目录。"
