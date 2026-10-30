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
    # Deterministic failures: retrying the same transaction can never succeed
    # (the session/record row is gone or re-owned, the tenant no longer exists,
    # or usage cost would regress). Abandon immediately instead of burning
    # FINALIZE_MAX_FAILURES lease cycles.
    FINALIZE_DETERMINISTIC_ERRORS = frozenset({
        'FINALIZE_SESSION_OWNER_CHANGED', 'FINALIZE_CLAIM_OWNER_CHANGED',
        'RECORD_OWNER_MISMATCH', 'BILLING_TENANT_NOT_FOUND', 'USAGE_COST_REGRESSION'})
    FINALIZE_MAX_FAILURES = 5

    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def _settle(self, cursor, runner, result):
        from .continuous import feature_enabled
        feature = feature_enabled(runner.get('checkpoint'))
        if feature:
            cursor.execute('SELECT usage_breakdown FROM chat_records WHERE record_id=%s FOR UPDATE',(runner['record_id'],))
            record = cursor.fetchone()
            previous = (record or {}).get('usage_breakdown') or {}
            if isinstance(previous,str):
                previous = json.loads(previous)
            previous = previous.get('runner_accumulator')
            from .usage_pricing import accumulate_cost
            cost,totals,breakdown = accumulate_cost(UsageRepository.observed_in_tx(
                cursor,runner['runner_id'],unapplied_only=previous is not None),previous)
        else:
            receipts = UsageRepository.facts_in_tx(cursor, runner['runner_id'])
            cost, totals, breakdown = grouped_cost(receipts)
        delta = settle_record(cursor, runner, result, cost, totals, breakdown)
        cursor.execute('''UPDATE agent_runner_usage_receipts SET applied=TRUE,record_id=%s,applied_at=clock_timestamp()
            WHERE runner_id=%s AND phase IN ('observed','no_usage') AND NOT applied''',
            (runner['record_id'],runner['runner_id']))
        cursor.execute('''SELECT EXISTS(SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s
            AND NOT applied AND phase IN ('started','unknown')
            AND price_snapshot->>'covered_cost' IS DISTINCT FROM 'resume_recognition') AS pending''',(runner['runner_id'],))
        pending = cursor.fetchone()['pending']
        # cost 一并返回：finalize 把本次结算积分补进终态公共 result.credit_cost
        # （设计 §5.5，chat/scheduled 通用；权威仍在 chat_records）。
        return 'pending' if pending else 'settled', delta, cost

    def settle_active(self, attempt):
        """Charge the original record without finalizing history or releasing its claim."""
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            runner = lock_runner(cursor, attempt.runner_id, attempt)
            previous = runner
            state = (runner.get('checkpoint') or {}).get('execution') or {}
            result = {'status':runner['status'],'output':state.get('output',''),
                'duration_ms':(runner.get('checkpoint') or {}).get('execution_duration_ms',0)}
            settlement, delta, _ = self._settle(cursor, runner, result)
            from .usage_projection import usage_in_tx
            snapshot = {**runner['public_snapshot'], 'usage':usage_in_tx(cursor,runner,settlement)}
            cursor.execute('''UPDATE agent_runners SET settlement_status=%s,public_snapshot=%s::jsonb,
                view_revision=view_revision+1,updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
                (settlement,capped_snapshot_dumps(snapshot),runner['runner_id']))
            runner = decoded(cursor.fetchone())
            runner = EventRepository.notify_in_tx(cursor,previous,after=runner)
            conn.commit()
        self._after_commit(runner,delta)
        return runner

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
            settlement, delta, cost = self._settle(cursor, runner, result)
            snapshot,result=project_terminal_public_state(cursor,runner,result)
            from .usage_projection import usage_in_tx
            snapshot['usage'] = usage_in_tx(cursor,runner,settlement)
            # 已接入 Runner 的渠道以最终合并 owner 写入历史，
            # 避免 Runner 与渠道 process_and_persist 双写 channel_messages。
            if runner['source'] not in ('wecom_kf', 'feishu', 'dingtalk', 'wecom'):
                write_history(cursor, runner, result)
            cursor.execute('''DELETE FROM agent_runner_session_claims WHERE owner_runner_id=%s
                    AND scope_key=%s AND session_kind=%s AND session_id=%s RETURNING owner_runner_id''',
                    (runner['runner_id'],runner['scope_key'],runner['session_kind'],runner['session_id']))
            if not cursor.fetchone():
                raise CheckpointFailure('FINALIZE_CLAIM_OWNER_CHANGED')
            public_result = {key:result.get(key) for key in ('status','output','images','error_code')}
            public_result['credit_cost'] = float(cost)
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

    def finalize_failed(self, attempt, error):
        """Terminal exit for a finalizing row whose transaction keeps failing.

        A finalizing row is re-leased forever otherwise: interrupt is a no-op for
        it and every retry raises the same error. Retry in place up to
        FINALIZE_MAX_FAILURES, then abandon to a terminal state so the claim,
        receipts and leases are released.
        """
        cause = str(error)
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            try:
                runner = lock_runner(cursor, attempt.runner_id, attempt)
            except CheckpointFailure:
                # Lost ownership: another attempt owns the row now.
                return None
            if runner['status'] != 'finalizing':
                # Nothing committed here: the caller keeps its normal path.
                return None
            checkpoint = runner.get('checkpoint') or {}
            failures = int(checkpoint.get('finalize_failures') or 0) + 1
            if cause in self.FINALIZE_DETERMINISTIC_ERRORS or failures >= self.FINALIZE_MAX_FAILURES:
                return self._abandon(cursor, conn, attempt, runner, cause)
            checkpoint = {**checkpoint,'finalize_failures':failures,'last_finalize_error':cause}
            cursor.execute('''UPDATE agent_runners SET checkpoint=%s::jsonb,revision=revision+1,
                view_revision=view_revision+1,updated_at=clock_timestamp()
                WHERE runner_id=%s AND attempt=%s''',
                (capped_checkpoint_dumps(checkpoint),runner['runner_id'],attempt.number))
            conn.commit()
            logger.warning('AgentRunner finalize retry runner={} attempt={} failures={} error={}',
                           runner['runner_id'],attempt.number,failures,cause)
            return runner

    def _abandon(self, cursor, conn, attempt, runner, cause):
        previous = runner
        pending = (runner.get('checkpoint') or {}).get('pending_finalization') or {}
        # A completed run whose history could not be committed is not a
        # completed run from the public record's point of view; only an
        # explicit user cancellation keeps its status.
        status = 'cancelled' if pending.get('status') == 'cancelled' else 'failed'
        result = {'status':status,'output':pending.get('output') or '',
                  'images':pending.get('images') or [],
                  'error_code':pending.get('error_code') or cause,'credit_cost':0.0}
        from .control_repository import close_pending_controls
        close_pending_controls(cursor,runner['runner_id'],'CONTROL_EXECUTION_CLOSED')
        cursor.execute('DELETE FROM agent_runner_session_claims WHERE owner_runner_id=%s',(runner['runner_id'],))
        cursor.execute('''UPDATE agent_runner_usage_receipts SET applied=TRUE,applied_at=clock_timestamp()
            WHERE runner_id=%s AND NOT applied''',(runner['runner_id'],))
        # The projection reads related rows; isolate it so a failure cannot
        # abort this terminal transaction.
        cursor.execute('SAVEPOINT finalize_abandon')
        try:
            snapshot,_ = project_terminal_public_state(cursor,runner,result)
            cursor.execute('RELEASE SAVEPOINT finalize_abandon')
        except Exception:
            cursor.execute('ROLLBACK TO SAVEPOINT finalize_abandon')
            logger.opt(exception=True).warning('AgentRunner abandon projection failed runner={}',runner['runner_id'])
            snapshot = dict(runner.get('public_snapshot') or {})
            snapshot.pop('browserAssistance',None)
        checkpoint = {**(runner.get('checkpoint') or {}),
                      'released_attempt':release_proof(attempt,status,'finalized'),
                      'finalize_abandoned':cause}
        cursor.execute('''UPDATE agent_runners SET status=%s,result=%s::jsonb,settlement_status='settled',
            checkpoint=%s::jsonb,public_snapshot=%s::jsonb,
            worker_id=NULL,lease_until=NULL,pause_requested=FALSE,resume_control_id=NULL,
            revision=revision+1,view_revision=view_revision+1,
            updated_at=clock_timestamp(),finished_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
            (status,json.dumps(result,ensure_ascii=False),capped_checkpoint_dumps(checkpoint),
             capped_snapshot_dumps(snapshot),runner['runner_id']))
        runner = decoded(cursor.fetchone())
        # Event fan-out is not terminal authority: keep it from rolling back
        # the only exit this row will get.
        cursor.execute('SAVEPOINT finalize_abandon_event')
        try:
            runner = EventRepository.notify_in_tx(cursor,previous,after=runner)
            cursor.execute('RELEASE SAVEPOINT finalize_abandon_event')
        except Exception:
            cursor.execute('ROLLBACK TO SAVEPOINT finalize_abandon_event')
            logger.opt(exception=True).warning('AgentRunner abandon event failed runner={}',previous['runner_id'])
        conn.commit()
        logger.error('AgentRunner finalize abandoned runner={} attempt={} status={} error={}',
                     previous['runner_id'],attempt.number,status,cause)
        self._after_commit(runner, None)
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
            settlement, delta, cost = self._settle(cursor, runner, runner['checkpoint']['pending_finalization'])
            from .usage_projection import usage_in_tx
            snapshot = {**runner['public_snapshot'],'usage':usage_in_tx(cursor,runner,settlement)}
            # 补结算同步公开费用：迟到 receipt 累计进权威 chat_records 后，
            # 公开 result.credit_cost 一并更新，避免展示永久少于实际扣费
            result = dict(runner.get('result') or {})
            result['credit_cost'] = float(cost)
            cursor.execute('''UPDATE agent_runners SET settlement_status=%s,result=%s::jsonb,public_snapshot=%s::jsonb,
                view_revision=view_revision+1,
                updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
                (settlement,json.dumps(result,ensure_ascii=False),capped_snapshot_dumps(snapshot),runner_id))
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
                from .continuous import feature_enabled
                feature = feature_enabled(runner.get('checkpoint'))
                trace_id = 'tr_'+runner['runner_id']+(':'+str(runner['attempt']) if feature else '')
                if not feature:
                    update_total_cost(trace_id,float(record.get('credit_cost') or 0))
                update_user_message_id(trace_id,runner['runner_id']+':user')
        except Exception:
            logger.opt(exception=True).warning('AgentRunner committed trace projection unavailable')
