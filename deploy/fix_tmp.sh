#!/bin/sh
# 修复容器内 /tmp 权限
# 背景：python:3.11-slim 镜像的 /tmp 是 tmpfs 挂载点，
#      Dockerfile 里 RUN chmod 1777 /tmp 不会作用于运行时的 tmpfs。
#      必须由 entrypoint 在容器启动后处理。
#
# 行为：
#   1) 始终尝试 chmod 1777 /tmp（tmpfs 上通常需要 root 权限；非 root 时忽略失败）
#   2) 当 uid=0 时再 chmod 一次，确保 tmpfs 权限生效
#
# 用法：在 ENTRYPOINT 最前面调用，例如：
#   ENTRYPOINT ["sh", "-c", "/usr/local/bin/fix_tmp.sh && exec gunicorn ..."]

set -e

# 打印诊断信息，便于排查
if mount | grep -q " on /tmp "; then
    echo "[fix_tmp.sh] /tmp 是挂载点: $(mount | grep ' on /tmp ')"
else
    echo "[fix_tmp.sh] /tmp 不是挂载点"
fi

# 当前 uid/euid
CURRENT_UID=$(id -u)
echo "[fix_tmp.sh] 当前 uid=${CURRENT_UID}"

# 如果是 root，直接 chmod；非 root 尝试 sudo（多数镜像无 sudo，所以可能失败，忽略）
if [ "${CURRENT_UID}" = "0" ]; then
    chmod 1777 /tmp
    ls -ld /tmp
    echo "[fix_tmp.sh] /tmp 权限修复完成（root 模式）"
else
    # 非 root 用户：先尝试用 chmod（部分 tmpfs 允许非 root 写），失败则尝试用 mount remount
    if chmod 1777 /tmp 2>/dev/null; then
        ls -ld /tmp
        echo "[fix_tmp.sh] /tmp 权限修复完成（非 root 直接 chmod）"
    else
        echo "[fix_tmp.sh] 非 root 模式下 chmod 失败，尝试 mount remount"
        # 尝试重新挂载 tmpfs 并指定 mode=1777
        if mount -o remount,mode=1777 /tmp 2>/dev/null; then
            ls -ld /tmp
            echo "[fix_tmp.sh] /tmp 权限修复完成（mount remount）"
        else
            echo "[fix_tmp.sh] 警告：/tmp 权限修复失败，appuser 可能无法写入"
            ls -ld /tmp
            # 不退出，避免整个容器起不来
            exit 0
        fi
    fi
fi
