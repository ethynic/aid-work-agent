"""Commit a local-domain DAL operation and its original phase reference together.

The tool's domain adapter owns operation
selection, permissions and prepared files; this repository owns fencing/CAS.
The supplied writer uses this cursor only (no network/file IO or nested commit).
"""

import copy
import hashlib
import json

import psycopg2

from src.core.agent_engine.contracts import CheckpointFailure
from src.db.database import get_db_connection
from .contracts import canonical_json
from .local_invocations import _execution
from .ownership import LeaseLost,lock_runner
from .repository import decoded
from .persistence_limits import capped_checkpoint_dumps


class LocalPhaseRepository:
    def __init__(self,connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def commit(self,attempt,revision,phase,intent,writer, *, observation=False,rearm_model=False,completion_proof=None,dispatch_proof=None):
        """Caller holds the runner's shared checkpoint lock throughout this call.

        Repeating a completed original phase returns its saved result. A changed
        intent is rejected; the operation never selects another device or phase.
        The caller applies the returned envelope/revision to its owning tree.
        """
        immutable = json.loads(canonical_json(intent))
        digest = hashlib.sha256(canonical_json(immutable).encode()).hexdigest()
        try:
            with self.connection_factory() as connection:
                cursor = connection.cursor()
                # A result of an already-started physical operation is not a
                # new dispatch. Cancellation cannot discard its original ACK.
                row = lock_runner(cursor,attempt.runner_id,attempt,dispatch=not observation)
                if row['revision'] != revision:
                    raise LeaseLost('CHECKPOINT_REVISION_CHANGED')
                checkpoint = copy.deepcopy(row.get('checkpoint') or {})
                execution = _execution(checkpoint,phase.execution_id,row['runner_id'])
                if execution.get('identity') != {key:row[key] for key in (
                        'tenant_id','user_id','session_id','source','session_kind')}:
                    raise LeaseLost('LOCAL_EXECUTION_IDENTITY_MISMATCH')
                tool = execution.get('tools',{}).get(phase.tool_call_id) or {}
                if (tool.get('call') or {}).get('id') != phase.tool_call_id or tool.get('phase') not in {'dispatching','waiting'}:
                    raise LeaseLost('LOCAL_TOOL_NOT_DISPATCHED')
                key = phase.key(attempt.runner_id)
                facts = execution.setdefault('resources',{}).setdefault('local_domain_phases',{})
                previous = facts.get(key)
                if completion_proof is not None:
                    if not observation or rearm_model:
                        raise LeaseLost('LOCAL_COMPLETION_PROOF_INVALID')
                    completion_proof(cursor,row,execution,immutable)
                if (observation or rearm_model) and previous is None and completion_proof is None:
                    raise LeaseLost('LOCAL_PHASE_NOT_STARTED')
                if previous is not None:
                    if previous.get('intent_digest') != digest or previous.get('phase') not in {'started','completed'}:
                        raise LeaseLost('LOCAL_PHASE_INTENT_CHANGED')
                    if previous['phase']=='completed' or writer is None and not rearm_model:
                        return row,copy.deepcopy(previous)
                    if rearm_model:
                        if (observation or writer is not None
                                or immutable.get('model_purpose') not in {'llm','resume_recognition_covered'}
                                or immutable.get('model_phase_version')!=1
                                or phase.branch not in {'heal.choice','resume.evaluate'}):
                            raise LeaseLost('LOCAL_MODEL_PHASE_INVALID')
                        cursor.execute('''SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s
                            AND execution_id=%s AND tool_call_id=%s AND purpose=%s LIMIT 1''',
                            (row['runner_id'],phase.execution_id,phase.tool_call_id,
                             'domain:'+key+':'+immutable['model_purpose']))
                        if cursor.fetchone():
                            raise LeaseLost('LOCAL_MODEL_ALREADY_DISPATCHED')
                    if observation and completion_proof is None and previous.get('authorized_attempt')!=attempt.number:
                        raise LeaseLost('LOCAL_PHASE_ORIGINAL_ATTEMPT_REQUIRED')
                if dispatch_proof is not None:
                    if observation or writer is not None:
                        raise LeaseLost('LOCAL_DISPATCH_PROOF_INVALID')
                    dispatch_proof(cursor,row,execution,immutable)
                result = writer(cursor,row,copy.deepcopy(immutable)) if writer is not None else None
                # Verify serializability before committing the domain effect.
                result = json.loads(canonical_json(result))
                lock_runner(cursor,attempt.runner_id,attempt,dispatch=not observation)
                if completion_proof is not None:
                    completion_proof(cursor,row,execution,immutable)
                if dispatch_proof is not None:
                    dispatch_proof(cursor,row,execution,immutable)
                fact = {'runner_id':attempt.runner_id,'execution_id':phase.execution_id,
                    'tool_call_id':phase.tool_call_id,'branch':phase.branch,'ordinal':phase.ordinal,
                    'intent_digest':digest,'request':immutable,
                    'authorized_attempt':previous.get('authorized_attempt') if previous is not None and not rearm_model else attempt.number,
                    'phase':'completed' if writer is not None else 'started','result':result}
                facts[key] = fact
                cursor.execute('''UPDATE agent_runners SET checkpoint=%s::jsonb,revision=revision+1,
                    updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
                    (capped_checkpoint_dumps(checkpoint),attempt.runner_id))
                committed = decoded(cursor.fetchone())
                connection.commit()
                return committed,copy.deepcopy(fact)
        except psycopg2.Error as error:
            raise CheckpointFailure('LOCAL_PHASE_STORAGE_FAILED') from error
