-- ==============================================================================
-- Task Plane 遗留数据库对象清理迁移（显式、可审阅、人工执行、自带安全断言）
--
-- 目标环境（仅此一个）：
--   aid_work_agent2  —— 测试环境 agent2 与在线开发环境 agent3 共用的主库
--   （服务器 124.222.3.254，容器 aid-postgres；两环境 DATABASE_URL 均指向本库）
--
-- 禁止事项：
--   1. 严禁对生产库 aid_work_agent 或任何其他库执行本文件（生产库已核实无下列对象；
--      本文件第 0 步有 current_database() 强制断言，连错库会自动中止）
--   2. 严禁混入应用启动、初始化脚本或任何自动执行链路；只能由运维人工审查后执行
--   3. 严禁使用 CASCADE；遇到外键报错说明环境与盘点时不符，应停下重新盘点
--
-- 安全断言（任一不符自动中止并回滚，DROP 不会执行）：
--   A0. 当前库必须是 aid_work_agent2
--   A1. 六张表若存在必须为 0 行（存在即加 ACCESS EXCLUSIVE 锁，防检查后写入）
--   A2. chat_records 若存在关联列，非空行数必须为 0（列删除 DDL 自身会取
--       ACCESS EXCLUSIVE，检查不再额外锁表，见第 1 步说明）
--   断言与 DROP 在同一事务内，RAISE EXCEPTION 触发整体回滚
--
-- 盘点结论（2026-08-30 首次执行前核实；本迁移已于 2026-08-30 在 aid_work_agent2
-- 成功执行一次，重跑应为无害 no-op，断言依旧生效）：
--   六张表全部存在且均为 0 行；chat_records 4218 行中 task 关联非空 0 行
--
-- 执行方式（服务器上）：
--   docker exec -i aid-postgres psql -U aid_user -d aid_work_agent2 \
--     -v ON_ERROR_STOP=1 < deploy/cleanup-task-plane-aid_work_agent2.sql
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 第 0 步：目标库强制断言（独立于事务，最先执行）
-- ------------------------------------------------------------------------------
DO $$
BEGIN
    IF current_database() <> 'aid_work_agent2' THEN
        RAISE EXCEPTION '安全断言失败：当前库为 %，本迁移仅允许在 aid_work_agent2 执行（严禁生产库）', current_database();
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 可选备份（六表均 0 行，仅需留 schema 以备回滚；按需去掉注释执行）
-- ------------------------------------------------------------------------------
-- docker exec aid-postgres pg_dump -U aid_user -d aid_work_agent2 \
--   --table=enterprise_tasks --table=task_session_links --table=agent_executions \
--   --table=agent_release_snapshots --table=policy_shadow_decisions \
--   --table=task_plane_outbox --schema-only \
--   > /var/backups/task-plane-schema-before-cleanup-$(date +%Y%m%d).sql

-- ------------------------------------------------------------------------------
-- 第 1 步：断言 + 删除（同一事务；顺序：锁表断言 → 索引 → 关联列 → 子表 → 主表）
--
-- lock_timeout：目标库与运行中的测试环境共享，任何锁等待超过 10s 立即失败回滚，
-- 避免迁移锁与业务写入互相排队；列删除 DDL 自身会短暂取 ACCESS EXCLUSIVE，
-- 故 A2 断言不再显式锁 chat_records。
-- ------------------------------------------------------------------------------
SET lock_timeout = '10s';

BEGIN;

-- A1：六张旁路表——存在则锁表并断言 0 行（不存在视为已清理，跳过）
DO $$
DECLARE
    t text;
    n bigint;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'enterprise_tasks','task_session_links','agent_executions',
        'agent_release_snapshots','policy_shadow_decisions','task_plane_outbox'
    ] LOOP
        IF to_regclass(format('public.%I', t)) IS NOT NULL THEN
            EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', t);
            EXECUTE format('SELECT count(*) FROM public.%I', t) INTO n;
            IF n <> 0 THEN
                RAISE EXCEPTION '安全断言失败：public.% 含 % 行数据，禁止删除，请先人工盘点', t, n;
            END IF;
        END IF;
    END LOOP;
END $$;

-- A2：chat_records 关联列——若存在任一列，锁表并断言非空行数为 0
DO $$
DECLARE
    has_task boolean;
    has_exec boolean;
    cond text;
    n bigint;
BEGIN
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='chat_records' AND column_name='task_id')
        INTO has_task;
    SELECT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='chat_records' AND column_name='execution_id')
        INTO has_exec;
    IF has_task OR has_exec THEN
        cond := CASE
            WHEN has_task AND has_exec  THEN 'task_id IS NOT NULL OR execution_id IS NOT NULL'
            WHEN has_task               THEN 'task_id IS NOT NULL'
            ELSE 'execution_id IS NOT NULL'
        END;
        EXECUTE 'SELECT count(*) FROM public.chat_records WHERE ' || cond INTO n;
        IF n <> 0 THEN
            RAISE EXCEPTION '安全断言失败：chat_records 有 % 行携带 task 关联，禁止删除列，请先人工盘点', n;
        END IF;
    END IF;
END $$;

-- 1.1 chat_records 上的关联对象
DROP INDEX IF EXISTS idx_chat_records_execution;
ALTER TABLE chat_records DROP COLUMN IF EXISTS execution_id;
ALTER TABLE chat_records DROP COLUMN IF EXISTS task_id;

-- 1.2 六张旁路表（policy_shadow_decisions / task_plane_outbox 的 BIGSERIAL
--     序列随表自动删除；表间无外键，顺序仅为可读性）
DROP TABLE IF EXISTS task_plane_outbox;
DROP TABLE IF EXISTS policy_shadow_decisions;
DROP TABLE IF EXISTS agent_release_snapshots;
DROP TABLE IF EXISTS agent_executions;
DROP TABLE IF EXISTS task_session_links;
DROP TABLE IF EXISTS enterprise_tasks;

COMMIT;

-- ------------------------------------------------------------------------------
-- 第 2 步：执行后验证（信息性输出，供操作者核对；期望全部为 0，
--          rows_after 必须与清理前的 chat_records 总行数一致）
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
FROM chat_records;

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
