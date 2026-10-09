"""Single transaction owner of public completion, history and original-record settlement."""

import json
from loguru import logger
from src.db.database import get_db_connection
from src.core.agent_engine.contracts import CheckpointFailure
from .ownership import lock_runner
from .execution_repository import release_proof
from .persistence_limits import capped_checkpoint_dumps, capped_snapshot_dumps
from .repository import decoded
from .usage_repository import UsageRepository
from .usage_pricing import grouped_cost
from .transaction_repository import write_history, settle_record
from .event_repository import EventRepository
from .application_public_projection import project_terminal_public_state


class RunnerFinalizer:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def _settle(self, cursor, runner, result):
        receipts = UsageRepository.facts_in_tx(cursor, runner['runner_id'])
        cost, totals, breakdown = grouped_cost(receipts)
        delta = settle_record(cursor, runner, result, cost, totals, breakdown)
        cursor.execute('''UPDATE agent_runner_usage_receipts SET applied=TRUE,record_id=%s,applied_at=clock_timestamp()
            WHERE runner_id=%s AND phase IN ('observed','no_usage') AND NOT applied''',
            (runner['record_id'],runner['runner_id']))
        pending = any(receipt['phase'] in ('started','unknown')
            and receipt['price_snapshot'].get('covered_cost')!='resume_recognition' for receipt in receipts)
        return 'pending' if pending else 'settled', delta

    def finalize(self, attempt):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            runner = lock_runner(cursor, attempt.runner_id, attempt)
            previous = runner
            if runner['status'] != 'finalizing':
                raise CheckpointFailure('RUNNER_NOT_FINALIZING')
            result = (runner.get('checkpoint') or {}).get('pending_finalization')
            if not result or result.get('status') not in ('completed','failed','cancelled'):
                raise CheckpointFailure('FINALIZATION_INTENT_NOT_SAVED')
            settlement, delta = self._settle(cursor, runner, result)
            snapshot,result=project_terminal_public_state(cursor,runner,result)
            # 已接入 Runner 的渠道（KF/飞书/钉钉）以最终合并 owner 写入历史，
            # 避免 Runner 与渠道 process_and_persist 双写 channel_messages。
            if runner['source'] not in ('wecom_kf', 'feishu', 'dingtalk'):
                write_history(cursor, runner, result)
            cursor.execute('''DELETE FROM agent_runner_session_claims WHERE owner_runner_id=%s
                    AND scope_key=%s AND session_kind=%s AND session_id=%s RETURNING owner_runner_id''',
                    (runner['runner_id'],runner['scope_key'],runner['session_kind'],runner['session_id']))
            if not cursor.fetchone():
                raise CheckpointFailure('FINALIZE_CLAIM_OWNER_CHANGED')
            public_result = {key:result.get(key) for key in ('status','output','images','error_code')}
            from .control_repository import close_pending_controls
            close_pending_controls(cursor,runner['runner_id'],'CONTROL_EXECUTION_CLOSED')
            checkpoint = {**runner['checkpoint'],'released_attempt':release_proof(attempt,result['status'],'finalized')}
            cursor.execute('''UPDATE agent_runners SET status=%s,result=%s::jsonb,settlement_status=%s,checkpoint=%s::jsonb,
                public_snapshot=%s::jsonb,
                worker_id=NULL,lease_until=NULL,pause_requested=FALSE,resume_control_id=NULL,
                revision=revision+1,view_revision=view_revision+1,
                updated_at=clock_timestamp(),finished_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
                (result['status'],json.dumps(public_result,ensure_ascii=False),settlement,
                 capped_checkpoint_dumps(checkpoint),capped_snapshot_dumps(snapshot),runner['runner_id']))
            runner = decoded(cursor.fetchone())
            runner = EventRepository.notify_in_tx(cursor,previous,after=runner)
            conn.commit()
        self._after_commit(runner, delta)
        return runner

    def settle_pending_usage(self, runner_id):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            runner = lock_runner(cursor, runner_id)
            previous = runner
            if runner['status'] not in ('completed','failed','cancelled'):
                return None
            # Queued cancellation has no execution record or started receipt.
            if not (runner.get('checkpoint') or {}).get('pending_finalization'):
                return runner
            settlement, delta = self._settle(cursor, runner, runner['checkpoint']['pending_finalization'])
            cursor.execute('''UPDATE agent_runners SET settlement_status=%s,view_revision=view_revision+1,
                updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''', (settlement,runner_id))
            runner = decoded(cursor.fetchone())
            runner = EventRepository.notify_in_tx(cursor,previous,after=runner)
            conn.commit()
        self._after_commit(runner, delta)
        return runner

    def settle_ready(self, limit=100):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT r.runner_id FROM agent_runners r WHERE r.status IN ('completed','failed','cancelled')
                AND EXISTS(SELECT 1 FROM agent_runner_usage_receipts u WHERE u.runner_id=r.runner_id
                           AND u.phase='observed' AND NOT u.applied) ORDER BY r.queue_order LIMIT %s''', (limit,))
            identifiers = [row['runner_id'] for row in cursor.fetchall()]
        for identifier in identifiers:
            self.settle_pending_usage(identifier)
        return len(identifiers)

    @staticmethod
    def _after_commit(runner, delta):
        try:
            if runner['session_kind'] == 'web':
                from src.core.cache_utils import CacheKeys, delete_cached_pattern
                delete_cached_pattern(CacheKeys.SESSION_MSGS,runner['session_id'],'')
            if runner['tenant_id'] is not None and delta:
                from src.core.cache_utils import invalidate_tenant_cache
                invalidate_tenant_cache(runner['tenant_id'])
        except Exception:
            logger.opt(exception=True).warning('AgentRunner committed cache invalidation failed')
        try:
            from src.db.models import ChatRecordDB
            from src.core.trace_persist import update_total_cost, update_user_message_id
            record = ChatRecordDB.get_by_id(runner['record_id'])
            if record:
                update_total_cost('tr_'+runner['runner_id'],float(record.get('credit_cost') or 0))
                update_user_message_id('tr_'+runner['runner_id'],runner['runner_id']+':user')
        except Exception as error:
            logger.warning('AgentRunner committed trace projection unavailable kind={}',type(error).__name__)
