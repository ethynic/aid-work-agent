"""Atomic original-invocation binding for durable local-tool adapters.

The adapter supplies a branch/ordinal for each actual desktop operation. This
repository owns no tool heuristics, polling, healing or local-tool billing.
It is not yet installed in RuntimeFactory; recovery adapters will consume these
facts rather than restarting an entire proxy operation.
"""

import copy
from dataclasses import dataclass
import hashlib
import json
import uuid

from src.db.database import get_db_connection
from src.local_tools.repository import create_invocation_in_tx
from .ownership import LeaseLost, lock_runner
from .repository import decoded
from .persistence_limits import capped_checkpoint_dumps


@dataclass(frozen=True)
class LocalPhase:
    execution_id: str
    tool_call_id: str
    branch: str
    ordinal: int

    def __post_init__(self):
        if not all(isinstance(value, str) and value for value in (
                self.execution_id, self.tool_call_id, self.branch)):
            raise ValueError('LOCAL_PHASE_INVALID')
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError('LOCAL_PHASE_INVALID')

    def key(self, runner_id):
        # JSON avoids ambiguous separators in call ids or adapter phase names.
        owner = json.dumps([runner_id, self.execution_id, self.tool_call_id,
                            self.branch, self.ordinal], separators=(',', ':'))
        return uuid.uuid5(uuid.NAMESPACE_URL, owner).hex


def _execution(checkpoint, expected_id, runner_id):
    root = checkpoint.get('execution')
    if not isinstance(root, dict):
        raise LeaseLost('LOCAL_EXECUTION_NOT_CHECKPOINTED')
    if root.get('execution_id') != runner_id:
        raise LeaseLost('LOCAL_ROOT_OWNER_MISMATCH')
    matches = []

    def visit(node):
        if node.get('execution_id') == expected_id:
            matches.append(node)
        for fact in node.get('children', {}).values():
            child = fact.get('checkpoint')
            if isinstance(child, dict):
                if child.get('execution_id') != fact.get('execution_id'):
                    raise LeaseLost('LOCAL_CHILD_OWNER_MISMATCH')
                visit(child)

    visit(root)
    if len(matches) != 1:
        raise LeaseLost('LOCAL_EXECUTION_OWNER_MISMATCH')
    return matches[0]


class LocalInvocationRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def bind(self, attempt, revision, phase, *, device_id, tool_name, arguments,
             provider_key=None, deadline_at=None, authorization_epoch=None,
             execution_lane='standard', device_policy=None):
        """Fence, insert/dedupe and associate the phase in one transaction.

        A slow invocation unique/device lock may consume the lease. Recheck the
        database clock after those waits and before committing; rollback also
        removes the new queued invocation so no desktop can claim it.
        The composition root must call under its shared checkpoint lock, then
        replace its local revision/envelope with the returned committed row.
        """
        # Freeze the caller's request before any database lock wait. The digest
        # and actual queued invocation must describe exactly the same input.
        arguments = json.loads(json.dumps(arguments, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False, allow_nan=False))
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            if row['revision'] != revision:
                raise LeaseLost('CHECKPOINT_REVISION_CHANGED')
            checkpoint = copy.deepcopy(row.get('checkpoint') or {})
            node = _execution(checkpoint, phase.execution_id, row['runner_id'])
            identity = node.get('identity') or {}
            if identity != {key: row[key] for key in (
                    'tenant_id', 'user_id', 'session_id', 'source', 'session_kind')}:
                raise LeaseLost('LOCAL_EXECUTION_IDENTITY_MISMATCH')
            if not row['tenant_id'] or not row['user_id']:
                raise LeaseLost('LOCAL_BINDING_REQUIRES_USER_TENANT')
            fact = node.get('tools', {}).get(phase.tool_call_id) or {}
            if ((fact.get('call') or {}).get('id') != phase.tool_call_id
                    or fact.get('phase') not in {'dispatching', 'waiting'}):
                raise LeaseLost('LOCAL_TOOL_NOT_DISPATCHED')

            cursor.execute("""SELECT * FROM local_tool_devices WHERE id=%s
                AND tenant_id=%s AND user_id=%s AND status='active' FOR UPDATE""",
                (device_id, row['tenant_id'], row['user_id']))
            device = cursor.fetchone()
            if not device:
                raise LeaseLost('LOCAL_DEVICE_OWNER_MISMATCH')
            if device_policy is not None:
                # last_seen_at is TIMESTAMP, populated from NOW() in this DB
                # session. Compare wall clocks in that same session timezone.
                cursor.execute('SELECT clock_timestamp()::timestamp AS now')
                if device_policy(dict(device), cursor.fetchone()['now']) is not None:
                    raise LeaseLost('LOCAL_DEVICE_NOT_READY')
            key = phase.key(attempt.runner_id)
            intent = dict(device_id=str(device_id), tool_name=tool_name,
                          arguments=arguments, provider_key=provider_key,
                          deadline_at=deadline_at.isoformat() if deadline_at else None,
                          authorization_epoch=authorization_epoch,
                          execution_lane=execution_lane)
            intent_json = json.dumps(intent, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False)
            digest = hashlib.sha256(intent_json.encode()).hexdigest()
            phases = node.setdefault('resources', {}).setdefault('local_invocations', {})
            previous = phases.get(key)
            if previous is not None and previous.get('intent_digest') != digest:
                raise LeaseLost('LOCAL_PHASE_INTENT_CHANGED')
            reference = dict(runner_id=attempt.runner_id, execution_id=phase.execution_id,
                             tool_call_id=phase.tool_call_id, branch=phase.branch,
                             ordinal=phase.ordinal)
            invocation_id = create_invocation_in_tx(
                cursor, row['tenant_id'], row['user_id'], device_id, tool_name,
                arguments, row['session_id'], provider_key=provider_key,
                business_kind='agent_runner', business_ref=reference, dedupe_key=key,
                deadline_at=deadline_at, authorization_epoch=authorization_epoch,
                execution_lane=execution_lane, verify_binding=True,
            )
            if previous is not None and previous.get('invocation_id') != invocation_id:
                raise LeaseLost('LOCAL_PHASE_INVOCATION_CHANGED')
            # Same row lock remains held, but the database wall clock must advance.
            lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            if previous is not None:
                return row
            phases[key] = {**reference, 'invocation_id': invocation_id,
                           'intent_digest': digest, 'request': json.loads(intent_json)}
            cursor.execute("""UPDATE agent_runners SET checkpoint=%s::jsonb,
                revision=revision+1,updated_at=clock_timestamp()
                WHERE runner_id=%s RETURNING *""",
                (capped_checkpoint_dumps(checkpoint), attempt.runner_id))
            committed = decoded(cursor.fetchone())
            conn.commit()
            return committed
