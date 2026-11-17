"""聊易就业数据库同步模块（lyjy_employment，租户定制）。

为就业咨询智能体客户定制的 MySQL 数据源同步：gt_lyjy 库（聊易就业平台）
qs_jobs/qs_guide/qs_article/qs_notice/qs_explain 五张表 → 知识库五分类
（在招职位/就业指导/政策资讯/通知公告/办事指南），零私有表，账本/配置复用
`src/services/content_sync/` 通用五表（module='lyjy_employment' 维度隔离）。

与平台层的边界（照 src/tenant_custom/__init__.py 约定）：
- 平台入口不静态 import 本目录；P2 由 configs/config.yaml tenant_custom_modules
  按需加载（api.py/scheduler.py 暴露挂载点，P1 仅有 cli.py 手动入口）；
- 对通用层只调用（content_sync 五表、计费链路、knowledge 表），不改通用服务代码；
- 连接配置 Fernet 整包加密存 source 行 connection_encrypted，日志/异常不打印凭据。

sccy_policy 模块复用本包的共享底层（base.py 落库机制 + renderer.py 文章模板），
属同一客户项目的两数据源，耦合已在设计文档声明。
"""
