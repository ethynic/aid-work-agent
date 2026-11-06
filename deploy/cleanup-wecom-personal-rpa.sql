-- =====================================================================
-- wecom_personal_rpa 渠道下线数据库清理脚本
-- =====================================================================
-- 用途：wecom_personal_rpa 渠道已于 2026-09-29 废弃（被 agent runner +
--       wecom-cli 取代），服务端代码已全量删除。本脚本清理该渠道在数据库
--       中遗留的 6 张表、渠道配置行与部分唯一索引。
--
-- 执行方式：由用户在真机手工执行（psql 或等效方式），不随部署自动运行：
--   docker exec -i aid-postgres psql -U aid_user -d aid_work_agent -f 本文件
--
-- 注意：
--   1. 执行前建议先备份数据库（pg_dump）。
--   2. 全部语句幂等（IF EXISTS / 以行数判断），可重复执行。
--   3. 新部署直接使用 deploy/init-postgres.sql，不会再创建这些对象，
--      无需执行本脚本。
-- =====================================================================

-- 1. RPA 客户端注册表（client_id / 加密凭证 / 监听模式）
DROP TABLE IF EXISTS wecom_rpa_clients CASCADE;

-- 2. RPA 账号表（企微个人号账号在线状态/身份核验）
DROP TABLE IF EXISTS wecom_rpa_accounts CASCADE;

-- 3. 动作回执 outbox（发送动作请求/重试/回复匹配）
DROP TABLE IF EXISTS wecom_rpa_action_outbox CASCADE;

-- 4. 会话存档收件箱（archive 事件信封/认领/重试）
DROP TABLE IF EXISTS wecom_rpa_archive_inbox CASCADE;

-- 5. 审计日志（客户端动作审计）
DROP TABLE IF EXISTS wecom_rpa_audit_logs CASCADE;

-- 6. 会话绑定表（账号 × 会话搜索键绑定）
DROP TABLE IF EXISTS wecom_rpa_conversation_bindings CASCADE;

-- 7. 渠道配置行（tenant_channel_configs 中该类型的历史配置，含加密凭证）
DELETE FROM tenant_channel_configs WHERE channel_type = 'wecom_personal_rpa';

-- 8. 该渠道的部分唯一索引（单例约束，init 脚本已不再创建）
DROP INDEX IF EXISTS uq_tenant_channel_configs_wecom_personal_rpa;

-- =====================================================================
-- 验证：以下查询应返回 0 行 / 计数为 0
-- =====================================================================
SELECT count(*) AS remaining_rpa_channel_configs
FROM tenant_channel_configs
WHERE channel_type = 'wecom_personal_rpa';

SELECT table_name
FROM information_schema.tables
WHERE table_name LIKE 'wecom_rpa_%';
