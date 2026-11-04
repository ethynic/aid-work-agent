"""企微会话旁路日志与日报业务域：三张 bs_wecom_* 业务表、旁路保存、查询与日报。

设计：docs/design/wecom/wecom-conversation-operations-design.md
计划：docs/plans/plan-wecom-conversation-operations.md

聊天记录是旁路日志：由服务器结果受理回调（logsink）异步尽力保存，
监控/回复/发送对账/任务恢复均不依赖这三张业务表。

模块顶层保持无副作用（backend_dev 包初始化规范）；服务与连接均为调用期惰性获取。
"""


def __getattr__(name):
    if name in ('ensure_tables', 'init_tables'):
        from .tables import ensure_tables
        return ensure_tables
    raise AttributeError(f"module 'src.wecom_conversation' has no attribute {name!r}")


__all__ = ['ensure_tables']
