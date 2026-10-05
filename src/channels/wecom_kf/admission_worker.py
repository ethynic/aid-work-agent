"""Explicit text receipt consumer. Only the independent Runner service accepts."""

import argparse
import asyncio
import signal
from src.db.database import get_db_connection
from src.services.agent_runner.source_client import SourceClient
from src.services.agent_runner.source_receipts import SourceLocator,SourceUnavailable
from src.services.agent_runner.contracts import RunnerError
from .ingress_repository import KfIngressRepository
from .ingress_auth import KfIngressError
from .context_repository import ContextRepository
from .context_worker import ContextWorker
from .ingress_worker import _thread, _drain
from .api_client import NativeWriteUnknown,NativeWriteStopped,WeComKfApiClient
from .adapter import WeComKfAdapter
from .admission_repository import KfBatchRepository
from .lifecycle_repository import LifecycleRepository
from .completion_business import KfCompletionBusiness
from .delivery import KfDeliveryWorker


class KfAdmissionWorker:
    def __init__(self, config, connection_factory=get_db_connection, client=None,*,
                 client_factory=WeComKfApiClient,adapter_factory=WeComKfAdapter):
        self.config,self.connection_factory=config,connection_factory
        self.client=client or SourceClient(config)
        self.context=ContextWorker(config.wecom_kf,ContextRepository(connection_factory),
            client_factory=client_factory,source_client=self.client)
        self.batches=KfBatchRepository(connection_factory)
        self.business=KfCompletionBusiness(config,connection_factory,adapter_factory=adapter_factory)
        self.delivery=KfDeliveryWorker(config,self.client,connection_factory,adapter_factory=adapter_factory)
        self.last_context=None
        self.after=0
        self.stopping=False
        self.tasks=set()

    def _candidate(self):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute(f'''SELECT i.account_id,i.namespace,i.message_id,i.receipt_order,i.message_type
                FROM wecom_kf_inbox i WHERE i.receipt_order>%s AND i.receive_seq IS NOT NULL
                AND i.namespace='sync' AND i.origin=3 AND i.message_type IN ('text','voice')
                AND i.accepted_input_ref IS NULL
                AND NOT EXISTS(SELECT 1 FROM wecom_kf_business_facts handled WHERE handled.account_id=i.account_id
                    AND handled.namespace=i.namespace AND handled.message_id=i.message_id
                    AND handled.payload_digest=i.payload_digest AND handled.business_kind IN ('stale','hidden_command','media_filtered','account_blocked')
                    AND (handled.phase IN ('known','suppressed') OR handled.business_kind='hidden_command'))
                AND EXISTS(SELECT 1 FROM wecom_kf_business_facts ready WHERE ready.account_id=i.account_id
                    AND ready.namespace=i.namespace AND ready.message_id=i.message_id
                    AND ready.payload_digest=i.payload_digest AND ready.business_kind='ready' AND ready.phase='known')
                AND NOT EXISTS (SELECT 1 FROM wecom_kf_input_batch_members m WHERE m.account_id=i.account_id
                    AND m.namespace=i.namespace AND m.message_id=i.message_id AND m.ordinal>0)
                AND NOT EXISTS (SELECT 1 FROM wecom_kf_receipt_classifications c
                    WHERE c.account_id=i.account_id AND c.namespace=i.namespace AND c.message_id=i.message_id
                    AND NOT {LifecycleRepository.ai_candidate_sql()})
                ORDER BY i.receipt_order LIMIT 1''',(self.after,))
            return cursor.fetchone()

    def _batch_ready(self,batch):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            for locator in batch.members:
                cursor.execute('''SELECT 1 FROM wecom_kf_business_facts b JOIN wecom_kf_inbox i
                    USING(account_id,namespace,message_id) WHERE b.account_id=%s AND b.namespace=%s AND b.message_id=%s
                    AND b.payload_digest=i.payload_digest AND b.business_kind='ready' AND b.phase='known' ''',
                    (locator.account_id,locator.namespace,locator.message_id))
                if cursor.fetchone() is None:return False
            return True

    async def run_once(self):
        if self.stopping:
            return None
        task=asyncio.current_task();self.tasks.add(task)
        try:
            for consumer in (self.delivery,self.business):
                if self.stopping:return None
                try:await consumer.run_once()
                except (RunnerError,KfIngressError,SourceUnavailable,NativeWriteUnknown,NativeWriteStopped) as error:
                    from loguru import logger
                    logger.warning('KF candidate retained kind={} code={}',type(error).__name__,getattr(error,'code','retained'))
            if self.stopping:return None
            try:
                self.last_context=await self.context.run_once()
            except (KfIngressError,TimeoutError):
                # This receipt stays durable; other bounded candidates progress.
                self.last_context=None
            if self.stopping or not self.config.wecom_kf.enabled:
                return None
            row=await _thread(self._candidate)
            if self.stopping: return None
            if row is None:
                self.after=0;return None
            self.after=row['receipt_order']
            locator=SourceLocator('wecom_kf',row['account_id'],row['namespace'],row['message_id'])
            if row['message_type']=='text':
                batch=await _thread(self.batches.collect,locator)
                if self.stopping or batch is None: return None
                if not await _thread(self._batch_ready,batch):return None
                return await self.client.accept_batch(batch)
            return await self.client.accept(locator)
        finally:
            self.tasks.discard(task)

    def request_stop(self):
        """Stop new dispatch immediately; existing owned tasks still drain."""
        self.stopping=True
        self.business.request_stop()
        self.delivery.request_stop()
        self.context.stopping=True
        self.context.voice.stopping=True

    async def close(self):
        self.request_stop()
        for task in tuple(self.tasks):
            await _drain(task)
        await self.business.close()
        await self.delivery.close()
        await self.context.close()
        await self.client.close()

    async def run(self, max_inputs=None):
        count=0
        while not self.stopping and (max_inputs is None or count<max_inputs):
            try:
                if await self.run_once(): count+=1
            except (RunnerError,KfIngressError,SourceUnavailable,NativeWriteUnknown,NativeWriteStopped):
                # Durable receipt remains pending; the finite scan advances.
                pass
            if not self.stopping: await asyncio.sleep(self.config.wecom_kf.poll_seconds)


async def run_worker(arguments):
    from src.config.settings import settings
    from src.db.database import init_postgres_pool,close_postgres_pool
    config=settings.agent_runner
    if not config.enabled:
        raise RuntimeError('SOURCE_SERVICE_UNAVAILABLE')
    await _thread(init_postgres_pool)
    worker=KfAdmissionWorker(config)
    try:
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGINT,signal.SIGTERM): loop.add_signal_handler(sig,worker.request_stop)
        if arguments.once: await worker.run_once()
        else: await worker.run(arguments.max_inputs)
    finally:
        try: await worker.close()
        finally: await _thread(close_postgres_pool)


def main():
    parser=argparse.ArgumentParser(description='Drain fixed KF context and submit enabled AI receipts to the original Runner service')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--max-inputs',type=int,default=None)
    args=parser.parse_args()
    if args.max_inputs is not None and args.max_inputs<1: parser.error('--max-inputs must be positive')
    asyncio.run(run_worker(args))


if __name__=='__main__': main()
