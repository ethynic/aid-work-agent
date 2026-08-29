-- ==============================================================================
-- Task Plane 遗留数据库对象清理迁移（显式、可审阅、人工执行）
--
-- 目标环境（仅此一个）：
--   aid_work_agent2  —— 测试环境 agent2 与在线开发环境 agent3 共用的主库
--   （服务器 124.222.3.254，容器 aid-postgres；两环境 DATABASE_URL 均指向本库）
--
-- 禁止事项：
--   1. 严禁对生产库 aid_work_agent 或任何其他库执行本文件（生产库已核实无下列对象）
--   2. 严禁混入应用启动、初始化脚本或任何自动执行链路；只能由运维人工审查后执行
--   3. 严禁使用 CASCADE；遇到外键报错说明环境与盘点时不符，应停下重新盘点
--
-- 盘点结论（2026-08-30，执行前必须重新核对）：
--   六张表全部存在且均为 0 行：enterprise_tasks / task_session_links /
--     agent_executions / agent_release_snapshots / policy_shadow_decisions /
--     task_plane_outbox
--   chat_records 共 4218 行，task_id / execution_id 非空行数为 0
--   idx_chat_records_execution 索引存在
--   结论：无任何业务数据落在待删对象中，删除零数据损失
--
-- 执行方式（服务器上）：
--   docker exec -i aid-postgres psql -U aid_user -d aid_work_agent2 \
--     -v ON_ERROR_STOP=1 < deploy/cleanup-task-plane-aid_work_agent2.sql
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 第 1 步：执行前核对（任何一项与盘点结论不符即中止，重新盘点）
-- ------------------------------------------------------------------------------
SELECT 'precheck_six_tables' AS step, table_name, 0 AS expect_zero_rows
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('enterprise_tasks','task_session_links','agent_executions',
                     'agent_release_snapshots','policy_shadow_decisions','task_plane_outbox')
ORDER BY table_name;

SELECT 'precheck_chat_records_task_refs' AS step, count(*) AS expect_zero_rows
FROM chat_records
WHERE task_id IS NOT NULL OR execution_id IS NOT NULL;

SELECT 'precheck_chat_records_total' AS step, count(*) AS rows_before_cleanup
FROM chat_records;

-- ------------------------------------------------------------------------------
-- 第 2 步：备份（六表均 0 行，仅需留 schema 以备回滚，导出为注释供按需执行）
-- ------------------------------------------------------------------------------
-- docker exec aid-postgres pg_dump -U aid_user -d aid_work_agent2 \
--   --table=enterprise_tasks --table=task_session_links --table=agent_executions \
--   --table=agent_release_snapshots --table=policy_shadow_decisions \
--   --table=task_plane_outbox --schema-only \
--   > /var/backups/task-plane-schema-before-cleanup-$(date +%Y%m%d).sql

-- ------------------------------------------------------------------------------
-- 第 3 步：清理（单事务；删除顺序：索引 → 关联列 → 子表 → 主表）
-- ------------------------------------------------------------------------------
BEGIN;

-- 3.1 chat_records 上的 Task Plane 关联对象
DROP INDEX IF EXISTS idx_chat_records_execution;
ALTER TABLE chat_records DROP COLUMN IF EXISTS execution_id;
ALTER TABLE chat_records DROP COLUMN IF EXISTS task_id;

-- 3.2 六张旁路表（policy_shadow_decisions / task_plane_outbox 的 BIGSERIAL
--     序列随表自动删除；表间无外键，顺序仅为可读性）
DROP TABLE IF EXISTS task_plane_outbox;
DROP TABLE IF EXISTS policy_shadow_decisions;
DROP TABLE IF EXISTS agent_release_snapshots;
DROP TABLE IF EXISTS agent_executions;
DROP TABLE IF EXISTS task_session_links;
DROP TABLE IF EXISTS enterprise_tasks;

COMMIT;

-- ------------------------------------------------------------------------------
-- 第 4 步：执行后验证
-- ------------------------------------------------------------------------------
SELECT 'postcheck_six_tables' AS step, count(*) AS expect_zero
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('enterprise_tasks','task_session_links','agent_executions',
                     'agent_release_snapshots','policy_shadow_decisions','task_plane_outbox');

SELECT 'postcheck_chat_records_cols' AS step, count(*) AS expect_zero
FROM information_schema.columns
WHERE table_name = 'chat_records' AND column_name IN ('task_id','execution_id');

SELECT 'postcheck_chat_records_total' AS step, count(*) AS rows_after_cleanup
FROM chat_records;  -- 必须与 rows_before_cleanup 完全一致

SELECT 'postcheck_sequences' AS step, count(*) AS expect_zero
FROM information_schema.sequences
WHERE sequence_schema = 'public'
  AND sequence_name IN ('policy_shadow_decisions_id_seq','task_plane_outbox_id_seq');

-- ==============================================================================
-- 回滚说明（仅灾难恢复用，不用于恢复业务使用）
--
-- 六表均为 0 行，回滚只需重建空表结构。DDL 原样取自已冻结的
-- feature/task-plane-phase0-1 分支 deploy/init-postgres.sql §4（勿再引用其设计）。
-- 若确需回滚，从该分支检出该节 DDL 手工执行；重建后仍属作废对象，应再次清理。
-- =============================================================================
