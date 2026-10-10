"""Trusted event registration and cursor-only channel-neutral message writer."""
from dataclasses import dataclass
import json
import uuid
from typing import Literal

from pydantic import Field
from .contracts import SCHEMA_VERSION, StrictDTO
from .repository import protected


class TerminalParameters(StrictDTO):
    session_title: str = Field(min_length=1,max_length=80)
    event_kind: Literal['completed','failed']


class TestParameters(StrictDTO):
    session_title: Literal['浏览器通知测试']
    event_kind: Literal['test']


@dataclass(frozen=True)
class EventDefinition:
    template_key: str
    parameter_model: type[StrictDTO]
    resource_type: str


class EventRegistry:
    def __init__(self):
        self.definitions={}

    def register(self, event_type, definition):
        if event_type in self.definitions:
            raise ValueError('NOTIFICATION_EVENT_ALREADY_REGISTERED')
        self.definitions[event_type]=definition

    def validate(self, event_type, parameters, resource_ref):
        definition=self.definitions.get(event_type)
        if definition is None or resource_ref.get('type') != definition.resource_type:
            raise ValueError('NOTIFICATION_EVENT_UNSUPPORTED')
        if not isinstance(resource_ref.get('id'), str) or not 1<=len(resource_ref['id'])<=256:
            raise ValueError('NOTIFICATION_RESOURCE_INVALID')
        return definition,definition.parameter_model.model_validate(parameters).model_dump()


EVENTS=EventRegistry()
EVENTS.register('task.completed',EventDefinition('runner_terminal',TerminalParameters,'runner_result'))
EVENTS.register('task.failed',EventDefinition('runner_terminal',TerminalParameters,'runner_result'))
EVENTS.register('notification.test',EventDefinition('notification_test',TestParameters,'notification_test'))


def publish_in_tx(cursor, *, subject, producer, event_type, event_key, resource_ref, parameters,
                  occurred_at, accepted_at, expires_at, category='task',priority=0,
                  policy_key='accepted_before_enabled',registry=EVENTS):
    """Only trusted producers invoke this; the caller owns the authoritative transaction."""
    definition,parameters=registry.validate(event_type,parameters,resource_ref)
    if (not producer or len(producer)>128 or not event_key or len(event_key)>512
            or not subject.user_id or expires_at<=occurred_at
            or (policy_key=='accepted_before_enabled' and accepted_at is None)):
        raise ValueError('NOTIFICATION_INTENT_INVALID')
    identifier,ref='notification_'+uuid.uuid4().hex,uuid.uuid4().hex
    cursor.execute('''INSERT INTO notification_messages
        (notification_id,notification_ref,schema_version,producer,event_type,event_key,
         scope_key,tenant_id,recipient_user_id,resource_ref,template_key,template_version,
         parameters_encrypted,occurred_at,accepted_at,expires_at,category,priority,policy_key)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,1,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(scope_key,recipient_user_id,producer,event_key) DO NOTHING
        RETURNING notification_ref''',
        (identifier,ref,SCHEMA_VERSION,producer,event_type,event_key,subject.scope_key,
         subject.tenant_id,subject.user_id,json.dumps(resource_ref),definition.template_key,
         protected(parameters),occurred_at,accepted_at,expires_at,category,priority,policy_key))
    created=cursor.fetchone()
    if created:
        return created['notification_ref']
    cursor.execute('''SELECT notification_ref FROM notification_messages
        WHERE scope_key=%s AND recipient_user_id=%s AND producer=%s AND event_key=%s''',
        (subject.scope_key,subject.user_id,producer,event_key))
    return cursor.fetchone()['notification_ref']
