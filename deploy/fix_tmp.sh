#!/bin/sh
# 修复容器内 /tmp 与 /home/appuser/tmp 权限
#
# 背景：
#   1) python:3.11-slim 镜像的 /tmp 是 tmpfs 挂载点，Dockerfile 里 RUN chmod 1777 /tmp
#      不会作用于运行时的 tmpfs，必须由 entrypoint 在容器启动后处理。
#   2) compose 给 /home/appuser/tmp 挂 tmpfs 时，mode 选项在本机引擎组合
#      （docker 24.0.2 + compose 2.24.5，agent2 实测）下不可靠：
#      - 挂载点在镜像内已存在时，按镜像目录模式生效（worker 实测 755 root:root，
#        而镜像目录是 appuser:appgroup）；
#      - mode 必须是 YAML 数字，引擎把十进制值按八进制重新解释（1777 -> 位模式 0o3361）。
#      worker 以 gosu appuser 运行，写不进自己的 TMPDIR 会使 Playwright 渲染等
#      依赖 TMPDIR 直算的工具报 EACCES（2026-11-12 事故）。
#      因此 TMPDIR 相关目录统一由本脚本在 entrypoint 兜底修复，不依赖 compose mode。
#
# 行为：
#   1) root 身份修复 /tmp（1777 全局可写）与 /home/appuser/tmp（恢复镜像定义的
#      appuser:appgroup 属主，不放宽为 1777）
#   2) 以 appuser 身份逐一验证可写性，失败输出含 "[fix_tmp.sh] ERROR" 的日志行
#      （docker logs 可 grep，避免再次静默劣化）
#
# 用法：在 ENTRYPOINT 最前面调用，例如：
#   ENTRYPOINT ["sh", "-c", "/usr/local/bin/fix_tmp.sh && exec gunicorn ..."]

CURRENT_UID=$(id -u)
echo "[fix_tmp.sh] 当前 uid=${CURRENT_UID}"

fix_dir() {
    dir=$1
    mode=$2
    owner=$3
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir" 2>/dev/null
    fi
    if [ "${CURRENT_UID}" = "0" ]; then
        if [ -n "$owner" ]; then
            chown "$owner" "$dir" 2>/dev/null || echo "[fix_tmp.sh] ERROR: chown $owner $dir 失败"
        fi
        chmod "$mode" "$dir" 2>/dev/null || echo "[fix_tmp.sh] ERROR: chmod $mode $dir 失败"
        echo "[fix_tmp.sh] $dir 修复完成: $(ls -ld "$dir")"
    else
        echo "[fix_tmp.sh] 警告: 非 root，无法修复 $dir（如需修复请在 root entrypoint 阶段调用本脚本）"
    fi
}

verify_dir() {
    dir=$1
    if [ "${CURRENT_UID}" = "0" ] && command -v gosu >/dev/null 2>&1; then
        if gosu appuser sh -c "touch '$dir/.fix_tmp_probe' && rm -f '$dir/.fix_tmp_probe'" 2>/dev/null; then
            echo "[fix_tmp.sh] 验证通过: appuser 可写 $dir"
        else
            echo "[fix_tmp.sh] ERROR: appuser 无法写入 $dir -- TMPDIR 类工具（渲染/转换）将失败，请检查挂载配置"
        fi
    fi
}

# /tmp：标准临时目录，1777 全局可写
fix_dir /tmp 1777 ""
verify_dir /tmp

# /home/appuser/tmp：应用私有 TMPDIR（TMPDIR 环境变量指向），恢复镜像内属主而非放宽为 1777
fix_dir /home/appuser/tmp 755 appuser:appgroup
verify_dir /home/appuser/tmp

# 修复失败不阻断容器启动（与旧版行为一致），靠上面的 ERROR 日志暴露问题
exit 0
