# 数据库增量升级脚本 YAML 化改造设计

> 状态：待评审
> 制定日期：2026-09-01
> 关联规范：[database_dev.md](../../.claude/rules/database_dev.md)

## 1. 背景与现状

### 1.1 现状

- `deploy/db_update.sql` 现 758 行、31 个日期块（2026-08-05 ~ 2026-09-01）。
- `_apply_db_updates()`（`src/db/database.py:625`）采用**全文件 sha256 哈希**：文件哈希变化即重跑全部语句（靠幂等兜底），成功后记录哈希。

### 1.2 痛点

1. 手动清理过期（超过 1 个月）脚本，容易遗忘。
2. 智能体生成的脚本偶尔遗漏日期注释，清理时无法识别归属。
3. 脚本顺序偶尔颠倒，从顶部手动删时多删/少删。
4. 每次发版全量重跑全部历史脚本，启动时间随文件增长线性变慢（当前已 758 行，趋势是突破 1000 行）。

### 1.3 目标

1. **免清理**：文件可无限累积，运行时只执行新增脚本。
2. **结构强制**：每条必含 `datetime`/`remark`，遗漏与顺序颠倒被启动校验拦截。
3. **增量执行**：只执行 `datetime` 晚于已应用时刻的块。
4. **可读性**：SQL 零转义，智能体生成不易出错。

## 2. 格式选型：YAML + block scalar

| 维度 | JSON | YAML（block scalar） | TOML | 纯 SQL + 注释定界 |
|------|------|---------------------|------|------------------|
| SQL 转义 | 需要（`"`、`\n`、`$$`） | **零转义** | `"""` 与 `\` 仍需转义 | 零转义 |
| 结构强制（date/remark 必填） | 强 | 强 | 中 | 弱（靠解析器自写） |
| 项目生态 | 无 | config.yaml / SKILL.md / SUBAGENT.md 已在用 PyYAML | 无 | 现有 |
| 可读性 | 差 | 好 | 中 | 最好 |

**结论**：YAML + block scalar。它既保留 JSON 的结构强制，又用 `|` 字面块消除 SQL 转义摩擦（智能体生成 SQL 时无需关心转义），且复用项目已有 YAML 生态。

## 3. YAML 文件格式规范

### 3.1 文件位置与结构

`deploy/db_update.yaml`，顶层为一个有序数组，元素为升级块：

```yaml
# deploy/db_update.yaml
# 数据库增量升级脚本：每个逻辑批次一个条目，datetime 必须唯一且严格递增
# 所有 SQL 必须幂等安全（IF NOT EXISTS / DROP ... IF EXISTS / ON CONFLICT）
- datetime: "2026-09-01 10:30:00"
  remark: "新增 xxx 字段，用于 ..."
  statements: |
    ALTER TABLE xxx ADD COLUMN IF NOT EXISTS col TEXT;
    CREATE INDEX IF NOT EXISTS idx_xxx_col ON xxx(tenant_id, col);
```

### 3.2 字段与约束

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `datetime` | string | **必填**，`YYYY-MM-DD HH:MM:SS`（到秒），文件内**唯一**且**严格递增** | 增量判断基准；一个块 = 一个逻辑批次，逻辑相关的脚本放一起，不相关的分开放 |
| `remark` | string | **必填**非空 | 变更说明，取代原 `-- 2026-08-25，...` 注释 |
| `statements` | string | **必填**非空 | block scalar，块内可含多条 SQL（以分号结尾），亦支持 `DO $$ ... $$` 块 |

### 3.3 校验规则（启动时 fail-fast）

用 `PyYAML.safe_load` + pydantic 模型校验，任一不通过则 `logger.error` 并 **raise 阻断启动**（格式错误必须人工修复，绝不静默跳过）：

1. `datetime` 匹配 `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$` 且为合法时间（定长到秒，字符串比较才等于时间比较）；
2. `datetime` 在文件内唯一；
3. `datetime` 严格递增（相邻后项 `datetime >` 前项）——**顺序颠倒、复制上一块忘改时间都会被拦截**；
4. `remark`、`statements` 非空。

### 3.4 SQL 书写要求

- 保持幂等（沿用现状：`IF NOT EXISTS`、`DROP ... IF EXISTS`、`ON CONFLICT`）。
- `statements` 块内语句统一缩进（解析后公共缩进前缀自动剥离），每句以分号结尾。
- **一个块 = 一个逻辑批次**：同一批发布的、逻辑相关的脚本放同一块；逻辑无关的独立新增一个块，`datetime` 取当前时刻并保证严格递增。禁止 `datetime` 重复或倒序。
- YAML 中 `datetime` 必须加引号写字符串（如 `"2026-09-01 10:30:00"`），避免 PyYAML 解析成 datetime 对象；校验层兼容字符串 / datetime 对象两种形态。

## 4. database.py 改造

### 4.1 新执行流程（重写 `_apply_db_updates`）

```text
读 db_update.yaml（不存在 → warning return）
  → PyYAML safe_load + pydantic 校验（失败 → raise，阻断启动）
  → 解析每块 statements 为语句列表（复用现有 $$/注释/分号切分逻辑，抽为 _split_sql_statements）
  → 读 _db_update_applied.last_datetime（无记录视为空）
  → 候选块 = 所有 datetime > last_datetime 的块，按 datetime 排序
  → 无候选 → info 返回
  → 获取 advisory lock（保留 key=123456）
  → 锁内重读 last_datetime（防并发重复执行）
  → 逐块逐语句执行（保留 SAVEPOINT + 锁超时重试）
  → 全部成功 → last_datetime = 本次最大 datetime 并 UPSERT；有失败 → 不更新 last_datetime，下次启动重跑
  → finally 释放 advisory lock
```

### 4.2 last_datetime 增量语义

- 执行粒度 = **datetime 块**（一个块含若干语句）。块内语句沿用现有逐条 SAVEPOINT 隔离 + 锁超时重试。
- `last_datetime` 记录**最后一次全部成功执行的最大 datetime**。`datetime > last_datetime` 的块才是待执行块。
- **事务语义与现状一致**：本次所有待执行块**全部成功**才更新 `last_datetime`；任一失败则本次不更新，下次启动从 `last_datetime` 之后整体重跑（幂等兜底）。不做"逐块成功即记录"的精细粒度，避免复杂度，收益可忽略（失败块本身也是幂等快速 DDL）。

### 4.3 保留机制

- advisory lock（`pg_try_advisory_lock` key=123456 + 重试）+ 锁内重读 last_datetime：多 worker 防并发。
- 逐语句 `SAVEPOINT` / `ROLLBACK TO SAVEPOINT` + 锁超时（55P03）退避重试 3 次。
- 文件缺失仅 warning 不阻断（`_apply_db_updates` 顶部已有）。

### 4.4 `_db_update_applied` 表变更

现有表：

```sql
CREATE TABLE IF NOT EXISTS _db_update_applied (
    id TEXT PRIMARY KEY,
    file_hash TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

改造：建表语句去掉 `file_hash NOT NULL`（新环境直接建新结构），存量环境执行幂等 `ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT`。新代码只读写 `last_datetime`，`file_hash` 列保留不删（避免 DROP 风险）、不再读写。原 `applied_at` 保留记录最近一次应用时间（可选，不参与判断）。

## 5. 上线与切换（无脚本迁移）

用户确认现有 `db_update.sql` 全部脚本会在数据库中手动执行完，**无需把旧 SQL 转为 YAML**，也无需首次全量重跑。切换步骤：

1. 运维在存量数据库手动执行完 `db_update.sql` 全部脚本。
2. `deploy/db_update.sql` 归档改名 `deploy/db_update.sql.bak`（或直接 `git rm`，git 历史可追溯），代码不再读取。
3. 新建 `deploy/db_update.yaml`，**仅包含切换之后**的新增增量脚本（datetime 均为切换时刻及之后）。
4. 代码上线后首次启动：`last_datetime` 为空 → YAML 中全部块（即新脚本）均执行 → 记录 `last_datetime = 最大 datetime` → 之后进入增量模式。

## 6. 涉及文件清单

| 文件 | 改动 |
|------|------|
| `deploy/db_update.sql` | 归档改名 `.bak` 或删除（git 历史可查），代码不再读取 |
| `deploy/db_update.yaml` | **新建**，初始仅含切换后的新增量脚本 |
| `src/db/database.py` | `_apply_db_updates` 重写为 YAML 加载 + 校验 + 增量执行；`_split_sql_statements` 抽为独立函数；`_init_postgresql` docstring 更新 |
| `.claude/rules/database_dev.md` | 「数据库变更与迁移」一节改写为 YAML 规范（结构、字段约束、校验、同批合并规则） |
| `tests/unit/test_database_db_updates.py` | 重写：YAML 加载/校验失败/增量过滤/last_datetime 更新/锁超时重试/失败不记录 |
| `tests/unit/test_desktop_agent_d1_postgres.py:110` | 文件路径断言 `db_update.sql` → `db_update.yaml` |
| `tests/integration/test_scheduled_tasks_tenant.py` | 注释提及 `db_update.sql`，代码独立，仅顺手更新注释（非必需） |
| `docs/system/database_system_table.md` | 如有提及 `_db_update_applied` 则同步（待查） |

## 7. 测试计划

- `test_database_db_updates.py` 重写：
  - YAML 正常解析并增量执行（last_datetime 之前跳过、之后执行，按 datetime 排序）；
  - 校验失败（datetime 缺失 / 格式错 / 重复 / 顺序颠倒 / remark 空 / statements 空）抛异常，不执行任何语句；
  - last_datetime 全成功更新、有失败不更新（下次重跑）；
  - 锁超时重试保留（沿用现有 FakeCursor/FakeConn 模式，mock 文件读取为 YAML）；
  - 无候选块时跳过、无锁时跳过。
- `test_desktop_agent_d1_postgres.py` 路径断言更新后回归。
- 启动安全检查：`_apply_db_updates` import + 语法；前端无改动。

## 8. 投入产出

| 项 | 投入 | 产出 |
|----|------|------|
| `database.py` 改造（加载/校验/增量/表变更） | ~0.5~1 人天 | 每次发版只执行新增块；文件累积到几千行也不影响启动 |
| `db_update.yaml` 初始内容 + 规则文档 | ~0.5 人天 | 消除"忘清理、漏日期、顺序颠倒"三个人为风险 |
| 测试重写 + 回归 | ~0.5 人天 | 校验失败 fail-fast 拦截错误脚本 |

合计约 1.5~2 人天。由于无需旧 SQL 转换和存量迁移，投入低于初始估算。

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `datetime` 被误写为早于已应用时刻（格式合法但语义错），被增量逻辑**静默跳过** | 启动校验只能保证格式/唯一/递增，无法发现意图错误；靠 code review + 脚本幂等兜底。开发时 `datetime` 必须取"当前时刻及之后" |
| 同一逻辑批次拆成多块、或复制上一块忘改 `datetime` | 校验强制 `datetime` 唯一 + 严格递增，命中即 fail-fast 报错；按"一块 = 一个逻辑批次"约定重新组织 |
| YAML 缩进错乱导致解析失败 | fail-fast 启动报错（恰好是安全网），按报错修正 |
| 校验失败 raise 阻断启动 | 这是预期行为：格式错误必须人工修复才能启动，绝不静默跳过 |
