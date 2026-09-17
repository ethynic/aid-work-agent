#!/usr/bin/env bash
# ============================================================================
# 一键同步：生产租户数据 -> 仿真环境（严格单向，无任何反向路径）
#
# 用法:
#   ./sync_tenant_to_sim.sh --tenant <tenant_id> [--wipe] [--redis] [--dry-run]
#
# 行为:
#   1. 安全断言（fail-fast）：写入目标库必须为 aid_work_agent1；
#      生产侧账号必须为只读账号 aid_readonly
#   2. 数据同步：动态枚举生产库所有含 tenant_id 列的表，逐表 COPY 同步
#   3. 渠道凭证置空：tenant_channel_configs.config 同步后整体清空
#      （防生产回调误路由验签通过 + 防仿真环境持生产凭证外呼）
#   4. --wipe: 先按同口径删除仿真库中该租户旧数据（删除口径 = 导入口径）
#   5. --redis: 按键模式白名单把生产 Redis prefix 镜像到仿真 prefix（保留 TTL）
#      清理仅按 prefix SCAN+DEL，禁止 FLUSHALL（与生产共用实例）
#   6. 附件不复制：仿真容器共享挂载生产附件目录（见设计文档 §5.4）
#
# 设计文档: docs/system/simulation-env-design.md
# 在 243 生产服务器上执行。连接走 docker exec aid-postgres（同实例双库）。
# ============================================================================
set -euo pipefail

# ---------- 可配置项（环境变量覆盖） ----------
PG_CONTAINER="${PG_CONTAINER:-aid-postgres}"
PROD_DB="${PROD_DB:-aid_work_agent}"
SIM_DB="${SIM_DB:-aid_work_agent1}"
PROD_USER="${PROD_USER:-aid_readonly}"     # 必须只读，断言强制
SIM_USER="${SIM_USER:-aid_sim_user}"       # 仅授权仿真库
PROD_PG_PASSWORD="${PROD_PG_PASSWORD:-}"
SIM_PG_PASSWORD="${SIM_PG_PASSWORD:-}"

REDIS_CONTAINER="${REDIS_CONTAINER:-aid-redis}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS_SRC_PREFIX="${REDIS_SRC_PREFIX:-aid-agent}"
REDIS_DST_PREFIX="${REDIS_DST_PREFIX:-aid-agent1}"
# 按键模式白名单复制（避免生产全局状态原样带入仿真）
REDIS_KEY_PATTERNS="${REDIS_KEY_PATTERNS:-uploaded_file:*}"

# ---------- 渠道凭证置空规则（表 -> SQL，随表结构演进人工维护） ----------
# 设计依据: docs/system/simulation-env-design.md §4.1
# tenant_channel_configs.config 为 JSON 文本，含 token/secret/aes_key 全部凭证
declare -A WIPE_RULES=(
  ["tenant_channel_configs"]="UPDATE tenant_channel_configs SET config = '{}', verified = 0 WHERE tenant_id = '%s';"
)

# ---------- 参数解析 ----------
TENANT="" ; DO_WIPE=0 ; DO_REDIS=0 ; DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant)  TENANT="$2" ; shift 2 ;;
    --wipe)    DO_WIPE=1 ; shift ;;
    --redis)   DO_REDIS=1 ; shift ;;
    --dry-run) DRY_RUN=1 ; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//' ; exit 0 ;;
    *) echo "未知参数: $1（--help 查看用法）" ; exit 1 ;;
  esac
done

[[ -z "$TENANT" ]] && { echo "错误: 必须指定 --tenant <tenant_id>" ; exit 1 ; }
# 防注入：tenant_id 中的单引号转义
TENANT_SQL=${TENANT//\'/\'\'}

log()  { echo "[$(date '+%H:%M:%S')] $*" ; }
warn() { echo "[$(date '+%H:%M:%S')] ⚠️  $*" ; }
die()  { echo "[$(date '+%H:%M:%S')] ✗ $*" ; exit 1 ; }

# ---------- psql 封装 ----------
prod_psql() {
  docker exec -e PGPASSWORD="$PROD_PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$PROD_USER" -d "$PROD_DB" -v ON_ERROR_STOP=1 -q "$@"
}
sim_psql() {
  docker exec -e PGPASSWORD="$SIM_PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$SIM_USER" -d "$SIM_DB" -v ON_ERROR_STOP=1 -q "$@"
}

# ---------- 安全断言 ----------
log "安全断言..."
SIM_DB_ACTUAL=$(sim_psql -t -A -c "SELECT current_database()")
[[ "$SIM_DB_ACTUAL" == "$SIM_DB" ]] || die "写入目标库为 $SIM_DB_ACTUAL，断言失败（期望 $SIM_DB）。拒绝执行。"
PROD_USER_ACTUAL=$(prod_psql -t -A -c "SELECT current_user")
[[ "$PROD_USER_ACTUAL" == "$PROD_USER" ]] || die "生产侧账号为 $PROD_USER_ACTUAL，断言失败（期望只读账号 $PROD_USER）。拒绝执行。"
TENANT_EXISTS=$(prod_psql -t -A -c "SELECT COUNT(*) FROM tenants WHERE tenant_id = '$TENANT_SQL' OR tenant_id = 'tenant_$TENANT_SQL'")
[[ "$TENANT_EXISTS" -ge 1 ]] || die "生产库不存在租户 $TENANT"
log "断言通过：目标库 $SIM_DB，生产只读账号 $PROD_USER，租户 $TENANT 存在"

# ---------- 表清单 ----------
TABLES=$(prod_psql -t -A -c "
  SELECT c.table_name FROM information_schema.columns c
  JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = 'public'
  WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id'
  GROUP BY c.table_name ORDER BY 1")
[[ -n "$TABLES" ]] || die "生产库未枚举到任何含 tenant_id 的表"

log "枚举到 $(echo "$TABLES" | wc -l) 张含 tenant_id 的表"

# ---------- 列结构比对（生产 vs 仿真，不一致跳过并告警） ----------
declare -A COLUMN_MISMATCH
for t in $TABLES; do
  COLS_PROD=$(prod_psql -t -A -c "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='$t'")
  COLS_SIM=$(sim_psql -t -A -c "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='$t'" 2>/dev/null || echo "")
  if [[ "$COLS_PROD" != "$COLS_SIM" ]]; then
    COLUMN_MISMATCH[$t]=1
    warn "表 $t 列结构不一致（仿真库缺表或结构漂移），本轮跳过"
  fi
done

# ---------- dry-run：只输出行数统计 ----------
if [[ $DRY_RUN -eq 1 ]]; then
  log "== DRY-RUN 行数预览（租户 $TENANT）=="
  for t in $TABLES; do
    [[ -n "${COLUMN_MISMATCH[$t]:-}" ]] && continue
    CNT=$(prod_psql -t -A -c "SELECT COUNT(*) FROM \"$t\" WHERE tenant_id = '$TENANT_SQL'")
    printf "  %-45s %s\n" "$t" "$CNT"
  done
  log "DRY-RUN 结束。加 --wipe 清仿真旧数据、--redis 镜像 Redis 状态。"
  exit 0
fi

START_TS=$(date +%s)

# ---------- wipe ----------
if [[ $DO_WIPE -eq 1 ]]; then
  log "--wipe: 清空仿真库中租户 $TENANT 旧数据..."
  for t in $TABLES; do
    [[ -n "${COLUMN_MISMATCH[$t]:-}" ]] && continue
    sim_psql -c "DELETE FROM \"$t\" WHERE tenant_id = '$TENANT_SQL'" >/dev/null
  done
  # Redis 侧：按仿真 prefix SCAN+DEL（禁止 FLUSHALL，与生产共用实例）
  if [[ $DO_REDIS -eq 1 ]]; then
    redis_cli() { docker exec "$REDIS_CONTAINER" redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} --raw "$@" ; }
    log "清理仿真 Redis prefix=$REDIS_DST_PREFIX 旧键..."
    redis_cli --scan --pattern "$REDIS_DST_PREFIX:*" | while read -r k; do
      [[ -n "$k" ]] && redis_cli DEL "$k" >/dev/null
    done
  fi
fi

# ---------- 数据同步 ----------
log "开始数据同步..."
declare -A SYNCED_COUNT
for t in $TABLES; do
  [[ -n "${COLUMN_MISMATCH[$t]:-}" ]] && continue
  CNT=$(prod_psql -t -A -c "SELECT COUNT(*) FROM \"$t\" WHERE tenant_id = '$TENANT_SQL'")
  [[ "$CNT" -eq 0 ]] && continue
  docker exec -e PGPASSWORD="$PROD_PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$PROD_USER" -d "$PROD_DB" -v ON_ERROR_STOP=1 -q \
    -c "\copy (SELECT * FROM \"$t\" WHERE tenant_id = '$TENANT_SQL') TO STDOUT WITH (FORMAT csv, HEADER)" \
  | docker exec -i -e PGPASSWORD="$SIM_PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$SIM_USER" -d "$SIM_DB" -v ON_ERROR_STOP=1 -q \
    -c "\copy \"$t\" FROM STDIN WITH (FORMAT csv, HEADER)"
  SYNCED_COUNT[$t]=$CNT
  log "  $t: $CNT 行"
done

# ---------- 渠道凭证置空 ----------
for t in "${!WIPE_RULES[@]}"; do
  [[ -z "${SYNCED_COUNT[$t]:-}" ]] && continue
  SQL=${WIPE_RULES[$t]//%s/$TENANT_SQL}
  sim_psql -c "$SQL" >/dev/null
  warn "已置空 $t 的凭证字段（渠道回调将验签失败拒答，主动外呼不可用）"
done

# ---------- Redis prefix 镜像 ----------
if [[ $DO_REDIS -eq 1 ]]; then
  log "Redis 镜像: $REDIS_SRC_PREFIX:* -> $REDIS_DST_PREFIX:*（模式白名单: $REDIS_KEY_PATTERNS）"
  redis_cli() { docker exec "$REDIS_CONTAINER" redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} --raw "$@" ; }
  TMP_DUMP=$(mktemp)
  COPIED=0
  for pat in $REDIS_KEY_PATTERNS; do
    while read -r key; do
      [[ -z "$key" ]] && continue
      newkey="${REDIS_DST_PREFIX}:${key#${REDIS_SRC_PREFIX}:}"
      redis_cli DUMP "$key" > "$TMP_DUMP" 2>/dev/null || continue
      ttl=$(redis_cli PTTL "$key")
      [[ "$ttl" == "-1" ]] && ttl=0
      docker exec -i "$REDIS_CONTAINER" redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} --raw -x RESTORE "$newkey" "$ttl" REPLACE < "$TMP_DUMP" >/dev/null
      COPIED=$((COPIED+1))
    done < <(redis_cli --scan --pattern "$pat" 2>/dev/null | grep "^${REDIS_SRC_PREFIX}:" || true)
  done
  rm -f "$TMP_DUMP"
  log "Redis 镜像完成: $COPIED 个键"
fi

# ---------- 报告 ----------
ELAPSED=$(( $(date +%s) - START_TS ))
TOTAL_ROWS=0
for c in "${SYNCED_COUNT[@]:-}"; do TOTAL_ROWS=$((TOTAL_ROWS + ${c:-0})) ; done
log "同步完成: $(echo "${!SYNCED_COUNT[@]}" | wc -w) 张表 / $TOTAL_ROWS 行 / 耗时 ${ELAPSED}s"
log "下一步: docker compose -f docker-compose.sim.yml start 起仿真环境（SOP 见设计文档 §8）"
