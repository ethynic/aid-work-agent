"""Finite application authorization for a physical local-domain model call.

Neutral provider-purpose hints carry no pricing permission. The runtime owner
installs this permit only after committing the original model phase; each
receipt transaction verifies that phase and its execution/tool ownership.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib

from src.core.agent_engine.contracts import CheckpointFailure
from .local_invocations import LocalPhase, _execution
from .contracts import canonical_json


@dataclass(frozen=True)
class DomainPermit:
    runner_id: str
    worker_id: str
    attempt: int
    execution_id: str
    tool_call_id: str
    branch: str
    ordinal: int
    purpose: str

    @property
    def phase_key(self):
        return LocalPhase(self.execution_id,self.tool_call_id,self.branch,self.ordinal).key(self.runner_id)


_permit = ContextVar('runner_local_domain_model_permit',default=None)


@contextmanager
def domain_model_permit(attempt, phase, purpose):
    value = DomainPermit(attempt.runner_id,attempt.worker_id,attempt.number,
        phase.execution_id,phase.tool_call_id,phase.branch,phase.ordinal,purpose)
    token = _permit.set(value)
    try:
        yield
    finally:
        _permit.reset(token)


def physical_authorization(attempt, execution_id, tool_call_id, purpose, call_id):
    permit = _permit.get()
    if permit is None:
        if purpose=='resume_recognition_covered':
            raise CheckpointFailure('COVERED_USAGE_OWNER_REQUIRED')
        return None
    if (permit.runner_id!=attempt.runner_id or permit.worker_id!=attempt.worker_id
            or permit.attempt!=attempt.number or permit.execution_id!=execution_id
            or permit.tool_call_id!=tool_call_id or permit.purpose!=purpose):
        raise CheckpointFailure('DOMAIN_USAGE_OWNER_MISMATCH')
    return {**vars(permit),'phase_key':permit.phase_key,'physical_call_id':call_id}


def assert_physical_authorization(cursor, row, attempt, authorization, *, execution_id, tool_call_id, purpose, call_id, provider, model):
    """Called with the original runner lock held and its current lease checked."""
    phase = LocalPhase(execution_id,tool_call_id,authorization['branch'],authorization['ordinal'])
    expected = dict(runner_id=attempt.runner_id,worker_id=attempt.worker_id,
        attempt=attempt.number,execution_id=execution_id,tool_call_id=tool_call_id,
        purpose=purpose,physical_call_id=call_id,phase_key=phase.key(attempt.runner_id))
    if any(authorization.get(key)!=value for key,value in expected.items()):
        raise CheckpointFailure('DOMAIN_USAGE_OWNER_MISMATCH')
    node = _execution(row['checkpoint'],execution_id,row['runner_id'])
    if node.get('identity')!={key:row[key] for key in ('tenant_id','user_id','session_id','source','session_kind')}:
        raise CheckpointFailure('DOMAIN_USAGE_OWNER_MISMATCH')
    tool = (node.get('tools') or {}).get(tool_call_id) or {}
    fact = (node.get('resources',{}).get('local_domain_phases') or {}).get(phase.key(row['runner_id'])) or {}
    if (tool.get('phase') not in {'dispatching','waiting'}
            or (tool.get('call') or {}).get('id')!=tool_call_id
            or any(fact.get(key)!=value for key,value in dict(runner_id=row['runner_id'],
                execution_id=execution_id,tool_call_id=tool_call_id,branch=phase.branch,ordinal=phase.ordinal).items())
            or fact.get('phase')!='started' or fact.get('authorized_attempt')!=attempt.number
            or fact.get('request',{}).get('model_purpose')!=purpose
            or fact.get('request',{}).get('model_phase_version')!=1
            or fact.get('intent_digest')!=hashlib.sha256(canonical_json(fact.get('request') or {}).encode()).hexdigest()):
        raise CheckpointFailure('DOMAIN_MODEL_PHASE_NOT_STARTED')
    if purpose=='resume_recognition_covered':
        from src.local_tools.proxy_tool import BossResumeDetailTool, BossResumeBatchTool
        if phase.branch!='resume.evaluate' or (tool.get('call') or {}).get('name') not in {
                BossResumeDetailTool.name,BossResumeBatchTool.name}:
            raise CheckpointFailure('COVERED_USAGE_OWNER_REQUIRED')
        from .resume_model_proof import assert_resume_model
        assert_resume_model(cursor,row,node,tool_call_id,phase.ordinal,fact['request'],provider,model)
    else:
        from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_CLASSES
        from src.local_tools.domain_flow import supports_owned_device_class
        cls = next((value for value in LOCAL_PROXY_TOOL_CLASSES
            if value.name==(tool.get('call') or {}).get('name')),None)
        if (purpose!='llm' or phase.branch!='heal.choice' or cls is None
                or not supports_owned_device_class(cls) or not cls.heal_eligible):
            raise CheckpointFailure('DOMAIN_MODEL_PURPOSE_INVALID')
    return 'domain:'+phase.key(row['runner_id'])+':'+purpose
