#!/bin/bash

# ==============================================================================
# PostgreSQL 镜像升级脚本
# 用途: 升级 timescale/timescaledb:latest-pg16 镜像，记录 digest、备份旧镜像
# 用法: bash deploy/update_postgres.sh
# ==============================================================================

set -e

CONTAINER=aid-postgres
IMAGE=timescale/timescaledb:latest-pg16
BACKUP_TAG=timescale/timescaledb:pg16-prev
BACKUP_FILE=/var/www/qb3_upload/postgres_image_backup/timescaledb-pg16-prev.tar

echo "=========================================="
echo "  PostgreSQL 镜像升级助手"
echo "=========================================="

# 1. 记录当前运行容器的 digest
echo ""
echo "[1] 记录当前 digest..."
OLD_DIGEST=$(docker inspect "$CONTAINER" --format='{{index .Image}}' 2>/dev/null || echo "")
if [ -z "$OLD_DIGEST" ]; then
    echo "  容器 $CONTAINER 不在运行，跳过 digest 记录"
else
    echo "  当前 digest: $OLD_DIGEST"
fi

# 2. 拉取最新镜像
echo ""
echo "[2] 拉取最新镜像 $IMAGE ..."
docker pull "$IMAGE"

# 3. 拿到新 digest
echo ""
echo "[3] 拿到新 digest..."
NEW_DIGEST=$(docker inspect "$IMAGE" --format='{{index .Id}}')
echo "  新 digest: $NEW_DIGEST"

# 4. 比对
echo ""
if [ "$OLD_DIGEST" == "$NEW_DIGEST" ]; then
    echo "[4] digest 未变化，无需升级"
    exit 0
fi

# 5. 备份当前镜像
echo "[5] 备份当前镜像（紧急回滚用）..."
mkdir -p "$(dirname "$BACKUP_FILE")"

# 取当前镜像（用 tag 引用，不依赖容器运行）
CURRENT_IMAGE=$(docker images --format='{{.Repository}}:{{.Tag}} @{{.ID}}' | grep "timescale/timescaledb" | grep -v "PREV\|RECOVERY" | head -1 | awk '{print $1}')
if [ -z "$CURRENT_IMAGE" ]; then
    CURRENT_IMAGE="$IMAGE"
fi
echo "  备份镜像: $CURRENT_IMAGE -> $BACKUP_TAG"
docker tag "$CURRENT_IMAGE" "$BACKUP_TAG" 2>/dev/null || true
docker save -o "$BACKUP_FILE" "$BACKUP_TAG" 2>/dev/null || true

if [ -f "$BACKUP_FILE" ]; then
    echo "  备份完成: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
else
    echo "  警告：备份失败，请手动备份"
fi

# 6. 重启容器
echo ""
echo "[6] 重启 PostgreSQL 容器..."
cd /var/www/agent2
docker compose -f deploy/docker-compose.postgres.yml up -d postgres

# 7. 等待启动
echo ""
echo "[7] 等待 PostgreSQL 启动..."
sleep 5
if docker exec "$CONTAINER" pg_isready -U "${POSTGRES_USER:-aid_user}" > /dev/null 2>&1; then
    echo "  ✓ PostgreSQL 已就绪"
else
    echo "  ✗ PostgreSQL 启动失败，请检查日志："
    echo "    docker logs $CONTAINER"
    echo ""
    echo "  紧急回滚命令："
    echo "    docker load -i $BACKUP_FILE"
    echo "    docker tag $BACKUP_TAG $IMAGE"
    echo "    docker compose -f deploy/docker-compose.postgres.yml up -d postgres"
    exit 1
fi

echo ""
echo "=========================================="
echo "  升级完成！"
echo "  旧 digest: $OLD_DIGEST"
echo "  新 digest: $NEW_DIGEST"
echo "  备份位置: $BACKUP_FILE"
echo "=========================================="
