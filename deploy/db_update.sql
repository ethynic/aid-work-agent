-- 数据库加表、加字段等SQL语句，记录在本文件，以便升级部署

-- 2026-4-21，添加租户初始管理员信息
ALTER TABLE tenants add column initial_admin_name TEXT default '',add column initial_admin_phone TEXT default '';

