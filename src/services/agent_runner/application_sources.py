"""Finite application composition of accepted-source capabilities."""

from .source_receipts import SourceUnavailable


class ApplicationSources:
    def __init__(self, config, connection_factory):
        # Only the application composition knows the installed platform adapter.
        from src.channels.wecom_kf.admission_repository import KfSourceProvider
        from .input_repository import InputRepository
        self.config = config
        self.provider = KfSourceProvider(config,connection_factory)
        self.inputs = InputRepository(connection_factory)
        from .source_preparation import SourcePreparation
        from src.channels.wecom_kf.voice_repository import VoiceRepository
        from src.channels.wecom_kf.voice_media import VoiceMedia
        self.preparation = SourcePreparation(self,VoiceRepository(self.provider,connection_factory),VoiceMedia(self.provider))

    def requires_receipt(self, source, service_id, *, purpose, row=None):
        native = bool(row and (row.get('checkpoint') or {}).get('source_initial_ref'))
        return native or (source=='wecom_kf' and purpose in {'submit','execute'}
                          and (self.config.wecom_kf.enabled or service_id==self.config.wecom_kf.service_id))

    async def prepare_accepted(self,locator,service_id):
        return await self.provider.prepare(locator,service_id,admission=False)

    async def prepare(self, locator, service_id):
        return await self.provider.prepare(locator,service_id)

    async def prepare_batch(self, batch, service_id):
        return await self.provider.prepare_batch(batch, service_id)

    def assert_batch_in_tx(self, cursor, batch, provenances, *, accepted_runner_id=None):
        from src.channels.wecom_kf.admission_repository import KfBatchRepository
        return KfBatchRepository.assert_manifest_in_tx(cursor, batch, provenances,
                                                       accepted_runner_id=accepted_runner_id)

    def question_reply_in_tx(self,cursor,row,provenance):
        from src.channels.wecom_kf.delivery_repository import DeliveryRepository
        return DeliveryRepository.question_reply_in_tx(cursor,row,provenance)

    def finish_delivery_in_tx(self,cursor,locator,fact,row,delivery_id,*,recap_tasks=()):
        from src.channels.wecom_kf.delivery_repository import DeliveryRepository
        return DeliveryRepository.finish_in_tx(cursor,locator,fact,row,delivery_id,recap_tasks=recap_tasks)

    def find_in_tx(self,cursor,locator,service_id):
        self.provider._peer(service_id,admission=False)
        fact=self.inputs.find_in_tx(cursor,locator)
        if fact is None: return None,None
        _,_,_,provenance=self.provider.read_in_tx(cursor,locator)
        return fact,provenance

    def link_in_tx(self, cursor, locator, input_ref):
        self.provider.link_in_tx(cursor, locator, input_ref)

    def authorize_in_tx(self, *args, **kwargs):
        return self.provider.authorize_in_tx(*args,**kwargs)

    def authorize_row_in_tx(self, cursor, row, credentials=None, *, execute=False, prepared=None):
        if row['source']=='wecom_kf':
            self.provider.authorize_row_in_tx(cursor,row,credentials,execute=execute,prepared=prepared)

    async def prepare_execution(self, row):
        if self.requires_receipt(row['source'],row['service_id'],purpose='execute',row=row):
            if not (row.get('checkpoint') or {}).get('source_initial_ref'):
                raise SourceUnavailable('SOURCE_RECEIPT_REQUIRED')
            return await self.provider.prepare_row(row)

    async def assert_preparation_recoverable(self,row):
        if (row.get('checkpoint') or {}).get('source_initial_ref'):
            await self.preparation.assert_recoverable(row)

    async def prepare_inputs(self,row,attempt,dispatch_check):
        if (row.get('checkpoint') or {}).get('source_initial_ref'):
            from .contracts import RunnerError
            try:
                return await self.preparation.prepare_inputs(row,attempt,dispatch_check)
            except RunnerError as error:
                raise SourceUnavailable('SOURCE_PREPARATION_PERMISSION_CHANGED') from error

    def runtime_scope(self,row,control):
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def scope():
            if row['source']=='wecom_kf' and (row.get('checkpoint') or {}).get('source_initial_ref'):
                from src.channels.wecom_kf.completion_business import KfRuntimeScope
                async with KfRuntimeScope(self.provider,row,control).enter():yield
            else:yield
        return scope()

    async def close(self):
        await self.preparation.close()


def build_source_capabilities(config, connection_factory):
    return ApplicationSources(config,connection_factory)


def project_input_in_tx(cursor,fact):
    """Finite installed source composition; no mutable registry or raw SQL here."""
    if fact['source']=='wecom_kf':
        from src.channels.wecom_kf.admission_repository import KfSourceProvider
        return KfSourceProvider.project_input_in_tx(cursor,fact)
    import copy
    return {'model_text':fact['intent']['text'],'history_text':fact['intent']['text'],
            'attachments':copy.deepcopy(fact['intent'].get('attachments') or []),'preparation_ref':None}


def project_ref_in_tx(cursor,runner,input_ref):
    from .source_preparation import project_ref_in_tx as project_ref
    return project_ref(cursor,runner,input_ref,project_input_in_tx)


def preparation_cancel_proof_in_tx(cursor,row,checkpoint):
    if row['source']=='wecom_kf':
        from src.channels.wecom_kf.voice_repository import VoiceRepository
        return VoiceRepository.cancel_proof_in_tx(cursor,row,checkpoint)
    return None
