"""通用渠道执行上下文；平台消息处理与投递由原渠道负责。"""
from .source_receipts import SourceUnavailable

class ApplicationSources:
    def __init__(self, config, connection_factory):
        self.connection_factory = connection_factory

    def authorize_row_in_tx(self, cursor, row, credentials=None, **kwargs):
        if (row.get('checkpoint') or {}).get('source_initial_ref'):
            raise SourceUnavailable('RETIRED_CHANNEL_PIPELINE')

    async def prepare_execution(self, row):
        self.authorize_row_in_tx(None, row)


    def runtime_scope(self, row, control):
        from .channel_context import channel_runtime_scope
        return channel_runtime_scope(row, self.connection_factory)

    async def close(self):
        pass


def build_source_capabilities(config, connection_factory):
    return ApplicationSources(config, connection_factory)
