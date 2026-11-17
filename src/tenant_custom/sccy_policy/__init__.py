"""水城创业网政策同步模块（sccy_policy，租户定制）。

为就业咨询智能体客户定制的第二个 MySQL 数据源：gt_sccy 库（水城创业网·双创
平台）zc_article → 知识库「创业政策」分类（决议 10，独立分类）。每日全量，
全量即对账（千级行，无水位；删除/下架/恢复走状态位 audit/closed/hidden）。

落库与账本复用 `src/tenant_custom/lyjy_employment/base.py` 共用底层（同属
本客户项目的两数据源，耦合已在设计文档声明），文章渲染复用 lyjy 的共用
文章模板（来源标注「水城创业网」）。
"""
