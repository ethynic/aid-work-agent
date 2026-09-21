"""外部内容同步通用基础设施（content_sync）。

平台级通用模块：为「外部数据源 → 知识库」类同步提供**共用**的账本与缓存表
（以公众号 wechat_mp 的 sync_runs/sync_items/articles 三表为蓝本泛化，加 module
维度区分来源）。任何模块（含 src/tenant_custom/ 下的租户定制模块）复用这五张表，
**不再为每个数据源建私有表**（2026-09-21 用户架构决议：避免 N 个租户 × N 张表）。

表清单：
- bs_content_sync_sources：源的租户配置（开关/频率/产品挑选），UNIQUE(tenant_id, module)
- bs_content_sync_runs：运行账本 + 执行队列，(tenant_id, module) WHERE status='running' 部分唯一索引串行
- bs_content_sync_items：批次内逐条任务（action/计费三字段）
- bs_content_sync_records：记录当前态账本（蓝本公众号 articles：external_id/content_hash/
  doc_id/user_deleted/miss_streak/退避；payload JSONB 存各源自定义目录展示字段）
- bs_image_vision_cache：图级 VL 缓存（tenant+URL 键，任何源的图片共用，防重复解析重复计费）

公众号自身不迁移（远期可选）；本模块不 import 租户定制代码。
"""
