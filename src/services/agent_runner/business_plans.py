"""Conversation business-plan handoff independent of live runner context."""

import json
from src.db.database import get_db_connection
from src.core.redis_client import redis_client
from src.models.plan import ExecutionPlan


class BusinessPlanRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def prepare(self, manager, runner):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT checkpoint FROM agent_runners WHERE scope_key=%s AND session_kind=%s
                AND session_id=%s AND user_id IS NOT DISTINCT FROM %s
                AND actor_kind=%s AND actor_id=%s AND source=%s AND queue_order<%s
                AND status IN ('completed','failed','cancelled') AND checkpoint ? 'business_plan'
                ORDER BY queue_order DESC LIMIT 1''',
                (runner['scope_key'],runner['session_kind'],runner['session_id'],runner['user_id'],
                 runner['actor_kind'],runner['actor_id'],runner['source'],runner['queue_order']))
            previous = cursor.fetchone()
            if previous:
                checkpoint = previous['checkpoint']
                if isinstance(checkpoint,str):
                    checkpoint = json.loads(checkpoint)
                data = checkpoint.get('business_plan')
                if data:
                    plan = ExecutionPlan.model_validate(data)
                    manager._save_plan(runner['session_id'],plan)
                    return
            # Once this conversation has entered Runner, legacy Redis is no
            # longer an authority, including after a channel owner rebind.
            cursor.execute('''SELECT 1 FROM agent_runners WHERE scope_key=%s AND session_kind=%s
                AND session_id=%s AND queue_order<%s LIMIT 1''',
                (runner['scope_key'],runner['session_kind'],runner['session_id'],runner['queue_order']))
            if cursor.fetchone():
                return
            # Legacy keys did not contain a tenant or table kind. Promotion is
            # permitted only when exactly one registered session owns this ID.
            cursor.execute('''SELECT 'web' AS kind,tenant_id,user_id FROM chat_sessions WHERE session_id=%s
                UNION ALL SELECT 'channel' AS kind,tenant_id,user_id FROM channel_sessions WHERE session_id=%s''',
                (runner['session_id'],runner['session_id']))
            sessions = cursor.fetchall()
            if len(sessions)!=1 or (sessions[0]['kind'],sessions[0]['tenant_id'],sessions[0]['user_id']) != (
                    runner['session_kind'],runner['tenant_id'],runner['user_id']):
                return
        data = redis_client.get(redis_client.make_key('execution_plan',runner['session_id']))
        if data:
            plan = ExecutionPlan.model_validate(data)
            manager._save_plan(runner['session_id'],plan)
