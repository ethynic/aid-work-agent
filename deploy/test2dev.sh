#!/usr/bin/env bash

# 禁止以 root/sudo 运行（同 sim.sh，2026-10-19 事故防护）：
# sudo 会保留 HOME=/home/ubuntu，root 身份的 git/写文件操作还会把仓库 .git 内部
# 文件改成 root 属主，导致 ubuntu 后续免密 fetch 失败、git 反复索要密码。
# 本脚本设计为以 ubuntu 用户直接运行（需要提权处已自带 sudo，ubuntu 已配 NOPASSWD）。
if [ "$(id -u)" -eq 0 ]; then
    echo "错误：请勿用 sudo/root 运行本脚本（会污染 git 凭据与仓库属主）。" >&2
    echo "      请直接以 ubuntu 用户执行，不要加 sudo：bash $0" >&2
    exit 1
fi

# ============================================================================
# 一键同步：测试环境数据 -> 在线开发环境（严格单向，无任何反向路径）
#
# 服务器：旧机 124.222.3.254（/var/www/agent2 = 测试，/var/www/agent3 = 在线开发）
# 背景：2026-10-29 agent3 拆库启用（此前与 agent2 共库 aid_work_agent2 而停用），
#       开发环境无需手动造数据，定期从测试库整库同步。
#
# 用法:
#   ./test2dev.sh [--redis] [--dry-run]
#
# 行为:
#   1. 安全断言（fail-fast）：来源库必须为 aid_work_agent2、目标库必须为
#      aid_work_agent3（current_database 断言，方向接反立即拒绝执行）
#   2. 整库同步：DROP 重建开发库 + pg_dump 全量还原（结构+数据真镜像，天然规避
#      加表/加列/列类型变更/索引约束增减等一切结构漂移），必须交互输入 yes 确认。
#      仅还原主库 aid_work_agent2，不含日志库 aid_work_logs2（开发日志库独立保留）
#   3. 渠道配置「清测试、保开发」：tenant_channel_configs 含企微/钉钉等真实回调
#      凭证，各环境独立维护（开发侧回调指向 agent3.aidingyi.cn）。同步前备份开发库
#      自有配置，还原后删除从测试带过来的渠道行并回填开发自有配置
#      （防开发环境持测试凭证外呼，同时避免每次同步后重新配渠道）
#   4. 停启开发容器：DROP 库前停 aid-agent-api3 / aid-agent-background3 / runner
#      容器（.env 指向目标库，不踩测试容器），还原完成后拉起并做健康检查
#   5. --redis: 按键模式白名单把测试 Redis prefix（aid-agent2）镜像到开发 prefix
#      （aid-agent3），保留 TTL；清理仅按 prefix SCAN+DEL，禁止 FLUSHALL（共用实例）
#   6. 附件不复制：开发容器与测试环境共享挂载宿主附件目录
#      （/var/www/qb3_upload/agent2_uploads / agent2_storage，见 docker-compose.dev.yml），
#      同步来的数据引用的附件文件直接可见
#
# 与 sim.sh 的关系：sim.sh 负责 生产(243 aid_work_agent) -> 仿真(agent1)，本脚本
# 负责 测试(254 aid_work_agent2) -> 在线开发(aid_work_agent3)，结构对齐其整库模式。
# 在 254 服务器上执行。连接走 docker exec aid-postgres（同实例多库）。
# ============================================================================
set -euo pipefail

# 渠道配置备份临时文件（退出时清理）
CHANNEL_BACKUP_FILE=""
trap '[[ -n "$CHANNEL_BACKUP_FILE" ]] && rm -f "$CHANNEL_BACKUP_FILE"' EXIT

# ---------- 可配置项（环境变量覆盖） ----------
PG_CONTAINER="${PG_CONTAINER:-aid-postgres}"
TEST_DB="${TEST_DB:-aid_work_agent2}"      # 来源：测试库（只读使用）
DEV_DB="${DEV_DB:-aid_work_agent3}"        # 目标：开发库（会被 DROP 重建）
PG_USER="${PG_USER:-aid_user}"             # 实例超管（CREATE EXTENSION / DROP CREATE）
PG_PASSWORD="${PG_PASSWORD:-Aid_2026}"
# 同实例同账号，无独立只读账号可断言；来源/目标防呆靠 current_database 断言 +
# 目标库名称白名单（见下方断言段），绝不允许目标库等于测试/生产库名

REDIS_CONTAINER="${REDIS_CONTAINER:-aid-redis}"
REDIS_PASSWORD="${REDIS_PASSWORD:-magento2017}"
REDIS_SRC_PREFIX="${REDIS_SRC_PREFIX:-aid-agent2}"
REDIS_DST_PREFIX="${REDIS_DST_PREFIX:-aid-agent3}"
# 按键模式白名单复制（避免测试全局状态原样带入开发）
REDIS_KEY_PATTERNS="${REDIS_KEY_PATTERNS:-uploaded_file:*}"

# ---------- 渠道凭证置空规则（表 -> SQL 前缀，随表结构演进人工维护） ----------
# tenant_channel_configs.config 为 JSON 文本，含 token/secret/aes_key 全部凭证。
# 正常路径下开发自有配置会在「渠道配置备份/恢复」两节先备份、后回填覆盖；
# 此规则是开发侧无自有配置时的兜底（防测试凭证残留在开发库）
declare -A WIPE_RULES=(
  ["tenant_channel_configs"]="UPDATE tenant_channel_configs SET config = '{}', verified = 0"
)

# ---------- 参数解析 ----------
DO_REDIS=0 ; DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --redis)   DO_REDIS=1 ; shift ;;
    --dry-run) DRY_RUN=1 ; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//' ; exit 0 ;;
    *) echo "未知参数: $1（--help 查看用法）" ; exit 1 ;;
  esac
done

log()  { echo "[$(date '+%H:%M:%S')] $*" ; }
warn() { echo "[$(date '+%H:%M:%S')] ⚠️  $*" ; }
die()  { echo "[$(date '+%H:%M:%S')] ✗ $*" ; exit 1 ; }

# ---------- psql 封装 ----------
test_psql() {
  docker exec -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$PG_USER" -d "$TEST_DB" -v ON_ERROR_STOP=1 -q "$@"
}
dev_psql() {
  docker exec -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$PG_USER" -d "$DEV_DB" -v ON_ERROR_STOP=1 -q "$@"
}
admin_psql() {  # 连 postgres 库（DROP/CREATE 不能在事务块里）
  docker exec -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 -q "$@"
}
admin_psql_any() {  # 容错版（collation 修复等失败不阻断）
  docker exec -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=0 -q "$@"
}

# ---------- 安全断言 ----------
log "安全断言..."
TEST_DB_ACTUAL=$(test_psql -t -A -c "SELECT current_database()")
[[ "$TEST_DB_ACTUAL" == "$TEST_DB" ]] || die "来源库为 $TEST_DB_ACTUAL，断言失败（期望 $TEST_DB）。拒绝执行。"
DEV_DB_ACTUAL=$(dev_psql -t -A -c "SELECT current_database()")
[[ "$DEV_DB_ACTUAL" == "$DEV_DB" ]] || die "目标库为 $DEV_DB_ACTUAL，断言失败（期望 $DEV_DB）。拒绝执行。"
# 目标库名称白名单：脚本任何变量被误配成测试/生产库名时在此拦截
case "$DEV_DB" in
  aid_work_agent|aid_work_agent2|aid_work_logs|aid_work_logs2|postgres)
    die "目标库 $DEV_DB 属于禁止写入的保护库名单。拒绝执行。" ;;
esac
log "断言通过：来源 $TEST_DB（只读使用） -> 目标 $DEV_DB（整库 DROP 重建）"

# ---------- dev compose 封装 ----------
DEV_DIR="/var/www/agent3"
dev_compose() {
  local args=(-f docker-compose.dev.yml)
  [[ -f "$DEV_DIR/docker-compose.agent-runner.yml" ]] && args+=(-f docker-compose.agent-runner.yml)
  (cd "$DEV_DIR" && docker compose "${args[@]}" "$@")
}

# ---------- dry-run：只输出行数统计 ----------
if [[ $DRY_RUN -eq 1 ]]; then
  log "== DRY-RUN 行数预览（整库 DROP+全量还原，仅主库 $TEST_DB -> $DEV_DB）=="
  TABLES=$(test_psql -t -A -c "
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY 1")
  [[ -n "$TABLES" ]] || die "测试库未枚举到任何表"
  for t in $TABLES; do
    CNT=$(test_psql -t -A -c "SELECT COUNT(*) FROM \"$t\"")
    printf "  %-45s %s\n" "$t" "$CNT"
  done
  log "DRY-RUN 结束。实际执行会 DROP 重建 $DEV_DB 并需交互输入 yes；--redis 镜像 Redis 状态。"
  exit 0
fi

# ---------- 强制确认（破坏性大，必须显式输入 yes） ----------
DEV_TABLES=$(dev_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
echo ""
echo "=============================================================="
echo "⚠️  整库镜像模式确认"
echo "  测试主库 $TEST_DB 全量还原 -> 开发库 $DEV_DB（当前 $DEV_TABLES 张表，数据将不可恢复）"
echo "  执行时将 DROP 重建开发库并从测试库 pg_dump 全量还原（结构+数据真镜像）；"
echo "  开发自有渠道配置（tenant_channel_configs）会先备份、还原后回填；日志库不动"
echo "  确认请输入 yes，其他任意输入放弃:"
echo "=============================================================="
read -r CONFIRM_ANSWER || die "无法读取确认输入（非交互环境），放弃执行"
[[ "$CONFIRM_ANSWER" == "yes" ]] || die "未输入 yes，放弃执行"
log "已确认，开始执行"

START_TS=$(date +%s)

# ---------- 备份开发库自有渠道配置（须在 DROP 之前） ----------
log "备份开发库自有渠道配置 tenant_channel_configs..."
if [[ "$(dev_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='tenant_channel_configs'")" == "0" ]]; then
  warn "开发库不存在 tenant_channel_configs 表，跳过备份"
  CHANNEL_BACKUP_FILE=""
else
  CHANNEL_BACKUP_FILE=$(mktemp)
  dev_psql -c "\copy (SELECT * FROM tenant_channel_configs) TO STDOUT WITH (FORMAT csv, HEADER)" > "$CHANNEL_BACKUP_FILE"
  CHANNEL_BACKUP_COLS=$(head -n1 "$CHANNEL_BACKUP_FILE")
  BACKUP_ROWS=$(( $(wc -l < "$CHANNEL_BACKUP_FILE") - 1 ))
  log "  已备份 $BACKUP_ROWS 行开发自有渠道配置"
fi

# ---------- 停止开发容器（避免 DROP 库后应用半路连接报错） ----------
if docker ps --filter name=^aid-agent-api3$ --filter status=running -q | grep -q .; then
  log "停止开发容器（aid-agent-api3 / aid-agent-background3 / runner 容器，还原完成后拉起）..."
  dev_compose stop
else
  log "开发容器未运行，跳过停止"
fi

# ---------- DROP 重建开发库 ----------
log "重建开发库 $DEV_DB（DROP + CREATE，编码/排序规则对齐测试库）..."
# template collation version 异常修复（glibc 升级后常见，参考 restore_postgres.sh）
admin_psql_any -c "ALTER DATABASE template1 REFRESH COLLATION VERSION" >/dev/null || true
admin_psql_any -c "ALTER DATABASE template0 REFRESH COLLATION VERSION" >/dev/null || true
admin_psql_any -c "
  SELECT pg_terminate_backend(pid) FROM pg_stat_activity
  WHERE datname='$DEV_DB' AND pid <> pg_backend_pid()" >/dev/null 2>&1 || true
admin_psql -c "DROP DATABASE IF EXISTS \"$DEV_DB\";"
DB_ENC=$(test_psql -t -A -c "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname='$TEST_DB'")
DB_COLLATE=$(test_psql -t -A -c "SELECT datcollate FROM pg_database WHERE datname='$TEST_DB'")
DB_CTYPE=$(test_psql -t -A -c "SELECT datctype FROM pg_database WHERE datname='$TEST_DB'")
admin_psql -c "CREATE DATABASE \"$DEV_DB\" OWNER \"$PG_USER\" TEMPLATE=template0 ENCODING '$DB_ENC' LC_COLLATE='$DB_COLLATE' LC_CTYPE='$DB_CTYPE';"

# 预建扩展：pg_dump 会输出 CREATE EXTENSION，先在空库建好（CREATE EXTENSION
# 需超管权限，本脚本统一用 aid_user），还原时过滤掉扩展相关语句（参考 restore_postgres.sh）
log "预建测试库扩展到开发库..."
while IFS= read -r ext; do
  [[ -z "$ext" || "$ext" == "plpgsql" ]] && continue
  if ! dev_psql -c "CREATE EXTENSION IF NOT EXISTS \"$ext\"" >/dev/null 2>&1; then
    warn "扩展 $ext 预建失败（连 $DEV_DB），还原该扩展相关语句时可能报错"
  fi
done < <(docker exec -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
  psql -U "$PG_USER" -d "$TEST_DB" -t -A -c "SELECT extname FROM pg_extension ORDER BY 1")

# ---------- 整库还原 ----------
# pg_dump 全量还原（结构+数据真镜像）。
# --schema=public：业务表全在 public；timescaledb 内部 schema（_timescaledb_catalog 等）
# 无需还原（日志库才用 timescaledb，主库若装了 vector 等普通扩展已预建）。
# sed 过滤两类语句：
#   1) 扩展语句（已由超管预建，CREATE/COMMENT ON EXTENSION 重复执行可能失败）
#   2) CREATE SCHEMA public（--schema=public 时 pg_dump 会显式输出，而新库 public 天然存在）
log "从测试库 $TEST_DB 全量还原到 $DEV_DB（pg_dump 结构+数据，约 3GB，耗时 1~3 分钟）..."
docker exec -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
  pg_dump -U "$PG_USER" -d "$TEST_DB" --schema=public --no-owner --no-privileges \
| sed -E '/^(CREATE[[:space:]]+EXTENSION|COMMENT[[:space:]]+ON[[:space:]]+EXTENSION|CREATE[[:space:]]+SCHEMA[[:space:]]+(IF[[:space:]]+NOT[[:space:]]+EXISTS[[:space:]]+)?public)/Id' \
| docker exec -i -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
  psql -U "$PG_USER" -d "$DEV_DB" -v ON_ERROR_STOP=1 -q --no-psqlrc

# 还原后表数量核对（镜像一致性快查）
TEST_T=$(test_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
DEV_T=$(dev_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
[[ "$TEST_T" == "$DEV_T" ]] || warn "还原后表数不一致: 测试 $TEST_T vs 开发 $DEV_T"
log "整库还原完成: $DEV_T 张表（测试 $TEST_T）"

# ---------- 渠道凭证置空（兜底）+ 恢复开发自有渠道配置 ----------
for t in "${!WIPE_RULES[@]}"; do
  # 测试库无此表时跳过（还原后开发库结构与测试库一致，表必然同生同灭）
  if [[ "$(dev_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='$t'")" == "0" ]]; then
    continue
  fi
  dev_psql -c "${WIPE_RULES[$t]}" >/dev/null
  warn "已清空 $t 的测试凭证（防开发环境持测试凭证外呼）"
done

if [[ -n "$CHANNEL_BACKUP_FILE" ]]; then
  log "恢复开发自有渠道配置..."
  # 置空规则只清 config 内容不删行，先整段删除再回填，避免与备份行重复
  dev_psql -c "DELETE FROM tenant_channel_configs" >/dev/null
  docker exec -i -e PGPASSWORD="$PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$PG_USER" -d "$DEV_DB" -v ON_ERROR_STOP=1 -q \
    -c "\copy tenant_channel_configs ($CHANNEL_BACKUP_COLS) FROM STDIN WITH (FORMAT csv, HEADER)" < "$CHANNEL_BACKUP_FILE"
  dev_psql -c "SELECT setval(pg_get_serial_sequence('tenant_channel_configs', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM tenant_channel_configs), 1), 1))" >/dev/null
  log "  已回填 $BACKUP_ROWS 行开发自有渠道配置（测试带过来的渠道配置已清除）"
fi

# ---------- 启动开发容器（渠道配置就绪后再拉起；up -d 幂等） ----------
log "启动开发容器（aid-agent-api3 / aid-agent-background3 / runner 容器）..."
dev_compose up -d --remove-orphans
HEALTH_OK=0
for i in $(seq 1 24); do
  curl -sf http://localhost:8004/health >/dev/null 2>&1 && { HEALTH_OK=1; break; }
  sleep 5
done
[[ $HEALTH_OK -eq 1 ]] && log "开发环境健康检查通过 (localhost:8004/health)" \
  || warn "健康检查超时（120s），请查看 docker logs aid-agent-api3"

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
log "整库镜像完成（DROP 重建 + pg_dump 全量还原）/ 耗时 ${ELAPSED}s"
log "开发环境已就绪: http://localhost:8004（agent3.aidingyi.cn；用完可 cd $DEV_DIR && docker compose -f docker-compose.dev.yml [-f docker-compose.agent-runner.yml] stop 停止）"
