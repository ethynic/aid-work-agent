#!/usr/bin/env bash
# ============================================================================
# 一键同步：生产租户数据 -> 仿真环境（严格单向，无任何反向路径）
#
# 用法:
#   ./sim.sh --tenant <tenant_id> [--wipe] [--redis] [--skip-code] [--dry-run]
#   ./sim.sh [--wipe] [--redis] [--skip-code] [--dry-run]   # 不传 --tenant = 整库复制模式
#
# 行为:
#   0. 代码同步（默认执行，--skip-code 跳过）: 纯 rsync 整体替换为生产版本，便于复现 bug
#      - rsync 生产工作区 /var/www/agent -> /var/www/agent1（--delete 整体替换，排除
#        .env/.git/log/configs/plans/storage/uploads/frontend/node_modules，configs 仿真侧独立维护）
#      - 仿真侧不保留任何自有代码改动，只有两种状态: 复现模式（本脚本，与生产一致）/
#        验证模式（agent1_update.sh，git reset 到 master 或指定提交）
#   1. 安全断言（fail-fast）：写入目标库必须为 aid_work_agent1；
#      生产侧账号必须为只读账号 aid_readonly
#   2. 数据同步：
#      - 整库模式（不传 --tenant）：DROP 重建仿真库 + pg_dump 全量还原（结构+数据
#        真镜像，天然规避加表/加列/列类型变更/索引约束增减等一切结构漂移），
#        必须交互输入 yes 确认。仅还原主库 aid_work_agent，不含日志库
#        aid_work_logs（同实例独立库，体量大，仿真不需要）
#      - 租户模式（--tenant x）：枚举所有含 tenant_id 列的表，按租户过滤复制；
#        生产加表/加字段自动补齐到仿真库（缺表用 pg_dump 从生产拉建表 DDL、
#        缺列用 ALTER ADD 补齐；类型漂移无法自动修复时跳过该表并告警）
#   3. 渠道配置「清生产、保仿真」：tenant_channel_configs 同步前先备份仿真库
#      自有配置（测试同学在仿真侧自建应用配置的回调凭证，回调地址指向仿真域名
#      agent1.aidingyi.cn，与生产回调天然隔离），同步后删除从生产带过来的渠道行
#      并回填仿真自有配置（防仿真持生产凭证外呼，同时避免每次同步后重新配渠道）
#   4. 旧数据清理 / 整库还原：
#      - 整库模式：DROP 重建仿真库（编码/排序规则对齐生产库），再从生产全量还原
#      - 租户模式：仅 --wipe 时按 tenant_id DELETE 仿真库中该租户旧数据
#   5. 序列校正：租户模式 SERIAL 主键表导入后 setval 到 MAX(id)（整库模式 pg_dump
#      自带 setval，无需处理）
#   6. --redis: 按键模式白名单把生产 Redis prefix 镜像到仿真 prefix（保留 TTL）
#      清理仅按 prefix SCAN+DEL，禁止 FLUSHALL（与生产共用实例）
#   7. 附件不复制：仿真容器共享挂载生产附件目录（见设计文档 §5.4）
#
# 设计文档: docs/system/simulation-env-design.md
# 在 243 生产服务器上执行。连接走 docker exec aid-postgres（同实例双库）。
# ============================================================================
set -euo pipefail

# 渠道配置备份临时文件（退出时清理）
CHANNEL_BACKUP_FILE=""
trap '[[ -n "$CHANNEL_BACKUP_FILE" ]] && rm -f "$CHANNEL_BACKUP_FILE"' EXIT

# ---------- 可配置项（环境变量覆盖） ----------
PG_CONTAINER="${PG_CONTAINER:-aid-postgres}"
PROD_DB="${PROD_DB:-aid_work_agent}"
SIM_DB="${SIM_DB:-aid_work_agent1}"
PROD_USER="${PROD_USER:-aid_readonly}"     # 必须只读，断言强制
SIM_USER="${SIM_USER:-aid_sim_user}"       # 仅授权仿真库
PROD_PG_PASSWORD="${PROD_PG_PASSWORD:-}"
SIM_PG_PASSWORD="${SIM_PG_PASSWORD:-}"
# 仿真库 DROP/CREATE/建扩展需要超级用户（容器内 aid_user 即超管，见 生产环境部署.md）
SIM_ADMIN_USER="${SIM_ADMIN_USER:-aid_user}"
SIM_ADMIN_PASSWORD="${SIM_ADMIN_PASSWORD:-Aid_2026}"

REDIS_CONTAINER="${REDIS_CONTAINER:-aid-redis}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
REDIS_SRC_PREFIX="${REDIS_SRC_PREFIX:-aid-agent}"
REDIS_DST_PREFIX="${REDIS_DST_PREFIX:-aid-agent1}"
# 按键模式白名单复制（避免生产全局状态原样带入仿真）
REDIS_KEY_PATTERNS="${REDIS_KEY_PATTERNS:-uploaded_file:*}"

# ---------- 渠道凭证置空规则（表 -> SQL 前缀，随表结构演进人工维护） ----------
# 设计依据: docs/system/simulation-env-design.md §4.1
# tenant_channel_configs.config 为 JSON 文本，含 token/secret/aes_key 全部凭证
# 租户模式自动追加 WHERE tenant_id = '...'；整库模式不带 WHERE（全表置空）
# 兜底清空（防仿真侧无配置可备份时残留生产凭证）；正常路径下仿真自有配置
# 会在「渠道配置备份/恢复」两节先备份、后回填覆盖
declare -A WIPE_RULES=(
  ["tenant_channel_configs"]="UPDATE tenant_channel_configs SET config = '{}', verified = 0"
)

# ---------- 参数解析 ----------
TENANT="" ; DO_WIPE=0 ; DO_REDIS=0 ; DRY_RUN=0 ; SKIP_CODE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant)  TENANT="$2" ; shift 2 ;;
    --wipe)    DO_WIPE=1 ; shift ;;
    --redis)   DO_REDIS=1 ; shift ;;
    --skip-code) SKIP_CODE=1 ; shift ;;
    --dry-run) DRY_RUN=1 ; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//' ; exit 0 ;;
    *) echo "未知参数: $1（--help 查看用法）" ; exit 1 ;;
  esac
done

# 整库模式：不传 --tenant 时全表复制（仅主库，不含日志库 aid_work_logs）
if [[ -n "$TENANT" ]]; then
  FULL_MODE=0
  # 防注入：tenant_id 中的单引号转义
  TENANT_SQL=${TENANT//\'/\'\'}
  TENANT_WHERE=" WHERE tenant_id = '$TENANT_SQL'"
else
  FULL_MODE=1
  TENANT_SQL="" ; TENANT_WHERE=""
fi

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
if [[ $FULL_MODE -eq 1 ]]; then
  log "断言通过：目标库 $SIM_DB，生产只读账号 $PROD_USER，整库复制模式（仅主库 $PROD_DB，不含日志库 aid_work_logs）"
else
  TENANT_EXISTS=$(prod_psql -t -A -c "SELECT COUNT(*) FROM tenants WHERE tenant_id = '$TENANT_SQL' OR tenant_id = 'tenant_$TENANT_SQL'")
  [[ "$TENANT_EXISTS" -ge 1 ]] || die "生产库不存在租户 $TENANT"
  log "断言通过：目标库 $SIM_DB，生产只读账号 $PROD_USER，租户 $TENANT 存在"
fi

# ---------- 代码同步（生产工作区 -> 仿真目录，纯 rsync 整体替换） ----------
# 仿真侧不保留任何自有代码改动，只有两种状态:
#   复现模式（本脚本）= 与生产代码一致；验证模式 = agent1_update.sh（git reset 到 master/指定提交）
PROD_DIR="/var/www/agent"
SIM_DIR="/var/www/agent1"
RSYNC_EXCLUDES=(
  --exclude='.env' --exclude='.git/' --exclude='.db_passwords'
  --exclude='log/' --exclude='configs/' --exclude='plans/'
  --exclude='storage/' --exclude='uploads/'
  --exclude='frontend/node_modules/'
  --exclude='__pycache__/' --exclude='*.pyc'
)

if [[ $SKIP_CODE -eq 1 ]]; then
  log "--skip-code: 跳过代码同步"
else
  [[ -d "$PROD_DIR" && -d "$SIM_DIR" ]] || die "代码同步需存在 $PROD_DIR 与 $SIM_DIR"
  git config --global --add safe.directory "$PROD_DIR" 2>/dev/null || true

  log "代码同步: 生产版本 $(git -C "$PROD_DIR" log -1 --format='%h %s' 2>/dev/null || echo '未知')"

  if [[ $DRY_RUN -eq 1 ]]; then
    log "== DRY-RUN 代码同步预览 =="
    log "  将 rsync 生产工作区整体替换 $SIM_DIR（--delete，排除 .env/.git/log/configs/plans/storage/uploads/frontend/node_modules）"
  else
    log "rsync 生产工作区 -> 仿真目录（--delete 整体替换，清除生产已删除的文件）..."
    sudo rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$PROD_DIR/" "$SIM_DIR/"

    log "清理 Python 字节码缓存..."
    sudo find "$SIM_DIR" -name .git -prune -o -type f -name '*.pyc' -delete 2>/dev/null || true
    sudo find "$SIM_DIR" -name .git -prune -o -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

    # 仿真 compose 文件必须位于仿真根目录（env_file: .env 按 compose 文件所在目录解析），
    # 而它在仓库里位于 deploy/（生产无根级同名文件，rsync --delete 会清掉根级手工副本），
    # 故每次同步后从仓库版本重新部署，根目录副本视为构建产物，不手工维护
    sudo cp "$SIM_DIR/deploy/docker-compose.sim.yml" "$SIM_DIR/docker-compose.sim.yml"

    log "configs 差异核对（仿真独立维护，仅提示不覆盖）:"
    sudo diff -rq "$PROD_DIR/configs" "$SIM_DIR/configs" 2>/dev/null | sed 's/^/  /' || true

    if [[ -n "$(docker ps --filter name=^aid-agent-api1$ --filter status=running -q)" ]]; then
      log "重启仿真容器 aid-agent-api1..."
      (cd "$SIM_DIR" && docker compose -f docker-compose.sim.yml restart)
      HEALTH_OK=0
      for i in $(seq 1 24); do
        if curl -sf http://localhost:8010/health >/dev/null; then HEALTH_OK=1; break; fi
        sleep 5
      done
      [[ $HEALTH_OK -eq 1 ]] && log "仿真环境健康检查通过 (localhost:8010/health)" \
        || warn "健康检查超时（120s），请查看 docker logs aid-agent-api1"
    else
      log "仿真容器未运行，跳过重启（需要时: cd $SIM_DIR && docker compose -f docker-compose.sim.yml up -d）"
    fi
  fi
fi

# ---------- 表清单 ----------
if [[ $FULL_MODE -eq 1 ]]; then
  # 整库模式：主库 public 全部基表（日志库 aid_work_logs 是独立库，天然不在范围内）
  TABLES=$(prod_psql -t -A -c "
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY 1")
  [[ -n "$TABLES" ]] || die "生产库未枚举到任何表"
  log "枚举到 $(echo "$TABLES" | wc -l) 张表（整库模式）"
else
  TABLES=$(prod_psql -t -A -c "
    SELECT c.table_name FROM information_schema.columns c
    JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = 'public'
    WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id'
    GROUP BY c.table_name ORDER BY 1")
  [[ -n "$TABLES" ]] || die "生产库未枚举到任何含 tenant_id 的表"
  log "枚举到 $(echo "$TABLES" | wc -l) 张含 tenant_id 的表"
fi

# ---------- 结构对齐（生产加表/加字段自动补齐到仿真库，仅租户模式） ----------
# 整库模式为 DROP+全量还原，天然规避结构漂移，无需对齐。
# 租户模式：生产迭代新增表/字段后仿真库落后时，先对齐结构再同步，避免整表被跳过：
#   缺表 -> 单次 pg_dump 从生产拉建表 DDL（含索引/序列/外键，--no-owner --no-privileges）
#   缺列 -> ALTER TABLE ADD COLUMN（类型/默认值/非空约束取自生产 pg_catalog）
#   类型漂移（同名列类型不同）无法自动修复 -> 跳过该表并告警
declare -A COLUMN_MISMATCH

table_cols() {  # $1=prod|sim  $2=表名 -> 按序输出 "列名|类型" 行
  local SQL="SELECT string_agg(a.attname || '|' || format_type(a.atttypid, a.atttypmod), E'\n' ORDER BY a.attnum)
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname='public' AND c.relname='$2' AND a.attnum > 0 AND NOT a.attisdropped"
  if [[ "$1" == "prod" ]]; then prod_psql -t -A -c "$SQL"; else sim_psql -t -A -c "$SQL"; fi
}

if [[ $FULL_MODE -eq 0 ]]; then

MISSING_TABLES=()
for t in $TABLES; do
  SIM_EXISTS=$(sim_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='$t'")
  if [[ "$SIM_EXISTS" == "0" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      warn "[dry-run] 表 $t 仿真库不存在，实际执行时将自动建表"
    else
      MISSING_TABLES+=("$t")
      COLUMN_MISMATCH[$t]=1
      warn "表 $t 仿真库不存在，将自动建表"
    fi
    continue
  fi

  COLS_PROD=$(table_cols prod "$t")
  COLS_SIM=$(table_cols sim "$t")
  [[ "$COLS_PROD" == "$COLS_SIM" ]] && continue

  # 仿真缺列自动补齐
  FAILED=0
  while IFS= read -r pc; do
    [[ -z "$pc" ]] && continue
    col="${pc%%|*}"
    if grep -qxF "$pc" <<< "$COLS_SIM"; then continue; fi
    if [[ "$(grep -c "^${col}|" <<< "$COLS_SIM" || true)" != "0" ]]; then
      warn "表 $t 列 $col 类型漂移，无法自动修复，本轮跳过该表"
      COLUMN_MISMATCH[$t]=1 ; FAILED=1 ; break
    fi
    DEFS=$(prod_psql -t -A -c "SELECT format_type(a.atttypid, a.atttypmod) || '|' || COALESCE(pg_get_expr(d.adbin, d.adrelid), '') || '|' || a.attnotnull::text
      FROM pg_attribute a
      LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname='public' AND c.relname='$t' AND a.attname='$col' AND a.attnum > 0 AND NOT a.attisdropped")
    ctype="${DEFS%%|*}" ; rest="${DEFS#*|}" ; cdefault="${rest%%|*}" ; cnotnull="${rest#*|}"
    ADD_DEFAULT=0
    ALT_SQL="ALTER TABLE \"$t\" ADD COLUMN IF NOT EXISTS \"$col\" $ctype"
    if [[ -n "$cdefault" && "$cdefault" != *nextval* ]]; then
      ALT_SQL="$ALT_SQL DEFAULT $cdefault" ; ADD_DEFAULT=1
    fi
    if [[ $DRY_RUN -eq 1 ]]; then
      log "  [dry-run] 将补列: 表 $t 列 $col ($ctype)"
      continue
    fi
    if sim_psql -c "$ALT_SQL" >/dev/null; then
      log "  表 $t 补列 $col ($ctype)"
      if [[ "$cnotnull" == "true" && $ADD_DEFAULT -eq 1 ]]; then
        sim_psql -c "ALTER TABLE \"$t\" ALTER COLUMN \"$col\" SET NOT NULL" >/dev/null 2>&1 \
          || warn "  表 $t 列 $col SET NOT NULL 失败（可空性差异，不影响数据同步）"
      fi
    else
      warn "表 $t 列 $col 自动补列失败，本轮跳过该表"
      COLUMN_MISMATCH[$t]=1 ; FAILED=1 ; break
    fi
  done <<< "$COLS_PROD"

  # 补列后仍不一致（列序漂移、仿真多余列等），跳过
  if [[ $FAILED -eq 0 && $DRY_RUN -eq 0 ]]; then
    COLS_SIM=$(table_cols sim "$t")
    if [[ "$COLS_PROD" != "$COLS_SIM" ]]; then
      warn "表 $t 结构对齐后仍不一致（列序/多余列漂移），本轮跳过"
      COLUMN_MISMATCH[$t]=1
    fi
  fi
done

# 缺表批量建表（单次 pg_dump，外键依赖顺序由 pg_dump 自动处理）
if [[ ${#MISSING_TABLES[@]} -gt 0 && $DRY_RUN -eq 0 ]]; then
  log "从生产 DDL 自动创建 ${#MISSING_TABLES[@]} 张缺表..."
  DUMP_ARGS=()
  for t in "${MISSING_TABLES[@]}"; do DUMP_ARGS+=(--table="public.$t"); done
  if docker exec -e PGPASSWORD="$PROD_PG_PASSWORD" "$PG_CONTAINER" \
      pg_dump -U "$PROD_USER" -d "$PROD_DB" --schema-only --no-owner --no-privileges "${DUMP_ARGS[@]}" \
      | sim_psql -f - ; then
    for t in "${MISSING_TABLES[@]}"; do
      if [[ "$(table_cols sim "$t")" == "$(table_cols prod "$t")" ]]; then
        unset "COLUMN_MISMATCH[$t]"
      else
        warn "表 $t 建表后结构核对不一致，本轮跳过"
      fi
    done
  else
    warn "缺表 DDL 创建失败，相关表本轮跳过"
  fi
fi
fi  # 租户模式结构对齐结束

# ---------- dry-run：只输出行数统计 ----------
if [[ $DRY_RUN -eq 1 ]]; then
  if [[ $FULL_MODE -eq 1 ]]; then
    log "== DRY-RUN 行数预览（整库 DROP+全量还原，仅主库 $PROD_DB）=="
  else
    log "== DRY-RUN 行数预览（租户 $TENANT）=="
  fi
  for t in $TABLES; do
    [[ -n "${COLUMN_MISMATCH[$t]:-}" ]] && continue
    CNT=$(prod_psql -t -A -c "SELECT COUNT(*) FROM \"$t\"$TENANT_WHERE")
    printf "  %-45s %s\n" "$t" "$CNT"
  done
  log "DRY-RUN 结束。加 --wipe 清仿真旧数据、--redis 镜像 Redis 状态。"
  exit 0
fi

# ---------- 整库模式强制确认（破坏性大，必须显式输入 yes） ----------
if [[ $FULL_MODE -eq 1 ]]; then
  [[ $DO_WIPE -eq 1 ]] && warn "整库模式下 --wipe 无效（整库还原为 DROP 重建+全量还原语义）"
  echo ""
  echo "=============================================================="
  echo "⚠️  整库镜像模式确认"
  echo "  生产主库 $PROD_DB 全量还原 -> 仿真库 $SIM_DB（仅主库，不含日志库 aid_work_logs）"
  echo "  执行时将 DROP 重建仿真库并从生产 pg_dump 全量还原（结构+数据真镜像），仿真数据不可恢复！"
  echo "  确认请输入 yes，其他任意输入放弃:"
  echo "=============================================================="
  read -r CONFIRM_ANSWER || die "无法读取确认输入（非交互环境），放弃执行"
  [[ "$CONFIRM_ANSWER" == "yes" ]] || die "未输入 yes，放弃执行"
  log "已确认，开始执行"
fi

START_TS=$(date +%s)

# ---------- 渠道配置备份（同步前；整库模式须在 DROP 仿真库之前） ----------
# 备份仿真库自有的 tenant_channel_configs（含 verified 状态与 id 序列）。
# 整库模式该表随 DROP 一起消失；租户模式 \copy 为追加写入、重跑会撞
# config_id 唯一约束，故租户模式备份后顺带删除该租户行，让后续写入干净执行。
log "备份仿真库自有渠道配置 tenant_channel_configs..."
if [[ "$(sim_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='tenant_channel_configs'")" == "0" ]]; then
  warn "仿真库不存在 tenant_channel_configs 表，跳过备份（无仿真自有渠道配置可保留）"
else
  CHANNEL_BACKUP_FILE=$(mktemp)
  sim_psql -c "\copy (SELECT * FROM tenant_channel_configs$TENANT_WHERE) TO STDOUT WITH (FORMAT csv, HEADER)" > "$CHANNEL_BACKUP_FILE"
  CHANNEL_BACKUP_COLS=$(head -n1 "$CHANNEL_BACKUP_FILE")
  BACKUP_ROWS=$(( $(wc -l < "$CHANNEL_BACKUP_FILE") - 1 ))
  log "  已备份 $BACKUP_ROWS 行仿真自有渠道配置"
  if [[ $FULL_MODE -eq 0 ]]; then
    sim_psql -c "DELETE FROM tenant_channel_configs$TENANT_WHERE" >/dev/null
  fi
fi

# ---------- 整库模式：DROP 重建仿真库（真镜像第一步） ----------
if [[ $FULL_MODE -eq 1 ]]; then
  admin_psql() {     # 超管执行 DDL（连 postgres 库，DROP/CREATE 不能在事务块里）
    docker exec -e PGPASSWORD="$SIM_ADMIN_PASSWORD" "$PG_CONTAINER" \
      psql -U "$SIM_ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 -q "$@"
  }
  admin_psql_any() { # 容错版（collation 修复等失败不阻断）
    docker exec -e PGPASSWORD="$SIM_ADMIN_PASSWORD" "$PG_CONTAINER" \
      psql -U "$SIM_ADMIN_USER" -d postgres -v ON_ERROR_STOP=0 -q "$@"
  }

  # 重建库时编码/排序规则与生产库保持一致（镜像语义 + 避免 collation 行为差异）
  DB_ENC=$(prod_psql -t -A -c "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname='$PROD_DB'")
  DB_COLLATE=$(prod_psql -t -A -c "SELECT datcollate FROM pg_database WHERE datname='$PROD_DB'")
  DB_CTYPE=$(prod_psql -t -A -c "SELECT datctype FROM pg_database WHERE datname='$PROD_DB'")

  # 先停仿真容器，避免 DROP 库后应用半路连接报错
  if [[ -n "$(docker ps --filter name=^aid-agent-api1$ --filter status=running -q)" ]]; then
    log "停止仿真容器 aid-agent-api1（还原完成后自动重启）..."
    (cd "$SIM_DIR" && docker compose -f docker-compose.sim.yml stop api)
  fi

  log "重建仿真库 $SIM_DB（DROP + CREATE，编码/排序规则对齐生产库）..."
  # template collation version 异常修复（glibc 升级后常见，参考 restore_postgres.sh）
  admin_psql_any -c "ALTER DATABASE template1 REFRESH COLLATION VERSION" >/dev/null || true
  admin_psql_any -c "ALTER DATABASE template0 REFRESH COLLATION VERSION" >/dev/null || true
  docker exec -e PGPASSWORD="$SIM_ADMIN_PASSWORD" "$PG_CONTAINER" \
    psql -U "$SIM_ADMIN_USER" -d postgres -v ON_ERROR_STOP=0 -q \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$SIM_DB' AND pid <> pg_backend_pid()" >/dev/null 2>&1 || true
  admin_psql -c "DROP DATABASE IF EXISTS \"$SIM_DB\";"
  admin_psql -c "CREATE DATABASE \"$SIM_DB\" OWNER \"$SIM_USER\" TEMPLATE=template0 ENCODING '$DB_ENC' LC_COLLATE='$DB_COLLATE' LC_CTYPE='$DB_CTYPE';"

  # 预建扩展：pg_dump 会输出 CREATE EXTENSION，非超级用户还原会失败；
  # 以超管在空库先建好，还原时过滤掉扩展相关语句（参考 restore_postgres.sh 的 sed 过滤）
  log "预建生产库扩展到仿真库..."
  while IFS= read -r ext; do
    [[ -z "$ext" || "$ext" == "plpgsql" ]] && continue
    if ! docker exec -e PGPASSWORD="$SIM_ADMIN_PASSWORD" "$PG_CONTAINER" \
        psql -U "$SIM_ADMIN_USER" -d "$SIM_DB" -v ON_ERROR_STOP=1 -q \
        -c "CREATE EXTENSION IF NOT EXISTS \"$ext\"" >/dev/null; then
      warn "扩展 $ext 预建失败，还原该扩展相关语句时可能报错"
    fi
  done < <(prod_psql -t -A -c "SELECT extname FROM pg_extension ORDER BY 1")

elif [[ $DO_WIPE -eq 1 ]]; then
  log "--wipe: 清空仿真库中租户 $TENANT 旧数据..."
  for t in $TABLES; do
    [[ -n "${COLUMN_MISMATCH[$t]:-}" ]] && continue
    sim_psql -c "DELETE FROM \"$t\"$TENANT_WHERE" >/dev/null
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
declare -A SYNCED_COUNT
if [[ $FULL_MODE -eq 1 ]]; then
  # 整库模式：pg_dump 全量还原（结构+数据真镜像）
  # --schema=public：业务表全在 public；timescaledb 内部 schema（_timescaledb_catalog 等）
  # 对只读账号无权限，且仿真环境不需要其内部对象
  # sed 过滤两类语句：
  #   1) 扩展语句（已由超管预建，CREATE/COMMENT ON EXTENSION 非超管执行会失败）
  #   2) CREATE SCHEMA public（--schema=public 时 pg_dump 会显式输出，而新库 public 天然存在）
  NON_PUBLIC_SCHEMAS=$(prod_psql -t -A -c "SELECT nspname FROM pg_namespace
    WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN ('public', 'information_schema',
    '_timescaledb_internal', '_timescaledb_catalog', '_timescaledb_config', '_timescaledb_functions',
    'timescaledb_information', 'timescaledb_experimental')")
  [[ -n "$NON_PUBLIC_SCHEMAS" ]] && warn "发现 public 之外的 schema 未纳入还原: $(echo "$NON_PUBLIC_SCHEMAS" | tr '\n' ' ')"
  log "从生产库 $PROD_DB 全量还原到 $SIM_DB（pg_dump 结构+数据，大库耗时较长）..."
  docker exec -e PGPASSWORD="$PROD_PG_PASSWORD" "$PG_CONTAINER" \
    pg_dump -U "$PROD_USER" -d "$PROD_DB" --schema=public --no-owner --no-privileges \
  | sed -E '/^(CREATE[[:space:]]+EXTENSION|COMMENT[[:space:]]+ON[[:space:]]+EXTENSION|CREATE[[:space:]]+SCHEMA[[:space:]]+(IF[[:space:]]+NOT[[:space:]]+EXISTS[[:space:]]+)?public)/Id' \
  | docker exec -i -e PGPASSWORD="$SIM_PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$SIM_USER" -d "$SIM_DB" -v ON_ERROR_STOP=1 -q --no-psqlrc

  # 还原后表数量核对（镜像一致性快查）
  PROD_T=$(prod_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
  SIM_T=$(sim_psql -t -A -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
  [[ "$PROD_T" == "$SIM_T" ]] || warn "还原后表数不一致: 生产 $PROD_T vs 仿真 $SIM_T"
  log "整库还原完成: $SIM_T 张表（生产 $PROD_T）"
else
  log "开始数据同步（租户 $TENANT）..."
  for t in $TABLES; do
    [[ -n "${COLUMN_MISMATCH[$t]:-}" ]] && continue
    CNT=$(prod_psql -t -A -c "SELECT COUNT(*) FROM \"$t\"$TENANT_WHERE")
    [[ "$CNT" -eq 0 ]] && continue
    docker exec -e PGPASSWORD="$PROD_PG_PASSWORD" "$PG_CONTAINER" \
      psql -U "$PROD_USER" -d "$PROD_DB" -v ON_ERROR_STOP=1 -q \
      -c "\copy (SELECT * FROM \"$t\"$TENANT_WHERE) TO STDOUT WITH (FORMAT csv, HEADER)" \
    | docker exec -i -e PGPASSWORD="$SIM_PG_PASSWORD" "$PG_CONTAINER" \
      psql -U "$SIM_USER" -d "$SIM_DB" -v ON_ERROR_STOP=1 -q \
      -c "\copy \"$t\" FROM STDIN WITH (FORMAT csv, HEADER)"
    SYNCED_COUNT[$t]=$CNT
    log "  $t: $CNT 行"
  done
fi

# ---------- 序列校正（仅租户模式；整库模式 pg_dump 自带 setval 无需处理） ----------
if [[ $FULL_MODE -eq 0 ]]; then
  log "校正仿真库序列..."
  for t in $TABLES; do
    [[ -z "${SYNCED_COUNT[$t]:-}" ]] && continue
    SERIAL_COL=$(prod_psql -t -A -c "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='$t' AND column_default LIKE 'nextval(%' ORDER BY ordinal_position LIMIT 1")
    [[ -z "$SERIAL_COL" ]] && continue
    sim_psql -c "SELECT setval(pg_get_serial_sequence('$t', '$SERIAL_COL'), GREATEST(COALESCE((SELECT MAX(\"$SERIAL_COL\") FROM \"$t\"), 1), 1))" >/dev/null
  done
fi

# ---------- 渠道凭证置空 ----------
for t in "${!WIPE_RULES[@]}"; do
  # 整库模式还原后必清（SYNCED_COUNT 为空）；租户模式仅清同步过的表
  [[ -z "${SYNCED_COUNT[$t]:-}" && $FULL_MODE -eq 0 ]] && continue
  if [[ $FULL_MODE -eq 1 ]]; then
    SQL=${WIPE_RULES[$t]}
  else
    SQL="${WIPE_RULES[$t]} WHERE tenant_id = '$TENANT_SQL'"
  fi
  sim_psql -c "$SQL" >/dev/null
  warn "已清空 $t 的生产凭证（防回调误路由验签通过 + 防仿真持生产凭证外呼）"
done

# ---------- 渠道配置恢复（删生产行，回填仿真自有配置） ----------
# 置空规则兜底已清凭证；此处直接按备份范围整段删除后回填，仿真自有配置
# （含 verified 状态）原样恢复。列清单取备份文件表头，规避整库模式 DROP
# 前后表列序漂移导致的按位错插。
if [[ -n "$CHANNEL_BACKUP_FILE" ]]; then
  log "恢复仿真自有渠道配置..."
  sim_psql -c "DELETE FROM tenant_channel_configs$TENANT_WHERE" >/dev/null
  docker exec -i -e PGPASSWORD="$SIM_PG_PASSWORD" "$PG_CONTAINER" \
    psql -U "$SIM_USER" -d "$SIM_DB" -v ON_ERROR_STOP=1 -q \
    -c "\copy tenant_channel_configs ($CHANNEL_BACKUP_COLS) FROM STDIN WITH (FORMAT csv, HEADER)" < "$CHANNEL_BACKUP_FILE"
  sim_psql -c "SELECT setval(pg_get_serial_sequence('tenant_channel_configs', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM tenant_channel_configs), 1), 1))" >/dev/null
  log "  已回填 $BACKUP_ROWS 行仿真自有渠道配置（生产带过来的渠道配置已清除）"
fi

# ---------- 启动仿真容器（渠道配置就绪后再拉起；up -d 幂等） ----------
if [[ $FULL_MODE -eq 1 ]]; then
  log "启动仿真容器 aid-agent-api1..."
  (cd "$SIM_DIR" && docker compose -f docker-compose.sim.yml up -d api)
  HEALTH_OK=0
  for i in $(seq 1 24); do
    curl -sf http://localhost:8010/health >/dev/null 2>&1 && { HEALTH_OK=1; break; }
    sleep 5
  done
  [[ $HEALTH_OK -eq 1 ]] && log "仿真环境健康检查通过 (localhost:8010/health)" \
    || warn "健康检查超时（120s），请查看 docker logs aid-agent-api1"
fi

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
if [[ $FULL_MODE -eq 1 ]]; then
  log "整库镜像完成（DROP 重建 + pg_dump 全量还原）/ 耗时 ${ELAPSED}s"
else
  TOTAL_ROWS=0
  for c in "${SYNCED_COUNT[@]:-}"; do TOTAL_ROWS=$((TOTAL_ROWS + ${c:-0})) ; done
  log "同步完成: $(echo "${!SYNCED_COUNT[@]}" | wc -w) 张表 / $TOTAL_ROWS 行 / 耗时 ${ELAPSED}s"
fi
log "仿真环境已就绪: http://localhost:8010（用完可 docker compose -f docker-compose.sim.yml stop 停止）"
