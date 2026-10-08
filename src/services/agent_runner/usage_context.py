"""Runner-owned accounting at actual provider boundaries, not presentation callbacks."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
import json
from pathlib import Path
import uuid
from src.core.agent_engine.contracts import CheckpointFailure


@dataclass
class UsageScope:
    attempt: object
    repository: object
    execution_id: str
    fatal_error: BaseException | None = None
    skill_boundary: bool = False
    failure_file: str | None = None
    exit_guard_registered: bool = False
    owned_group: int | None = None
    tool_call_id: str | None = None

    def check(self):
        if self.failure_file:
            try:
                if Path(self.failure_file).stat().st_size:
                    self.fatal_error = self.fatal_error or CheckpointFailure('RUNNER_CHILD_USAGE_STORAGE_FAILED')
            except FileNotFoundError:
                self.fatal_error = self.fatal_error or CheckpointFailure('RUNNER_USAGE_GUARD_MISSING')
            except OSError as error:
                self.fatal_error = self.fatal_error or error
        if self.fatal_error is not None:
            raise CheckpointFailure("RUNNER_USAGE_STORAGE_FAILED") from self.fatal_error

    def fail(self, error):
        from .ownership import StopRequested
        if isinstance(error,StopRequested):
            return
        self.fatal_error = self.fatal_error or error
        if self.skill_boundary and not self.exit_guard_registered:
            import atexit
            atexit.register(lambda: os._exit(87))
            self.exit_guard_registered = True
        if self.failure_file:
            try:
                Path(self.failure_file).write_text(type(error).__name__)
            except OSError as storage_error:
                # A shell may mask the Python process's exit code (`python; true`).
                # Its owned process group must stop if the shared failure signal
                # cannot be written. Never signal the worker's process group.
                if self.skill_boundary and os.name == 'posix' and self.owned_group is not None:
                    import signal
                    group = os.getpgrp()
                    if group == self.owned_group:
                        os.killpg(group,signal.SIGKILL)
                raise CheckpointFailure('RUNNER_USAGE_GUARD_WRITE_FAILED') from storage_error

    def prepare_environment(self, environment, context=None):
        prepare_skill_environment(environment,context)

    def prepare_process(self, environment):
        self.check()
        if self.failure_file is None:
            raise CheckpointFailure('RUNNER_USAGE_GUARD_MISSING')
        registration = Path(self.failure_file).with_name(Path(self.failure_file).name+'.child.'+uuid.uuid4().hex)
        try:
            descriptor = os.open(registration,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            os.close(descriptor)
        except OSError as error:
            self.fail(error)
            raise CheckpointFailure('RUNNER_CHILD_REGISTRATION_FAILED') from error
        environment['AID_RUNNER_PROCESS_FILE'] = str(registration)

    def register_process(self, process_id, environment):
        try:
            group = os.getpgid(process_id) if os.name == 'posix' else None
            if os.name == 'posix' and group != process_id:
                raise CheckpointFailure('RUNNER_CHILD_GROUP_NOT_OWNED')
            Path(environment['AID_RUNNER_PROCESS_FILE']).write_text(json.dumps({'pid':process_id,'group':group}))
        except ProcessLookupError:
            # A non-Python command may already be finished; an unregistered
            # Python bootstrap cannot dispatch provider IO.
            pass
        except BaseException as error:
            try:
                self.fail(error)
            finally:
                if os.name == 'posix':
                    import signal
                    try: os.killpg(process_id,signal.SIGKILL)
                    except ProcessLookupError: pass
            raise CheckpointFailure('RUNNER_CHILD_REGISTRATION_FAILED') from error

    def release_process(self, environment):
        path = environment.get('AID_RUNNER_PROCESS_FILE')
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError as error:
                self.fail(error)
                raise CheckpointFailure('RUNNER_CHILD_REGISTRATION_CLEANUP_FAILED') from error

    def child_exit(self, returncode):
        if returncode is None or returncode == 87 or returncode < 0:
            # A concurrent cancellation is not proof that this abnormal exit
            # originated in the parent user-cancel cleanup. That cleanup already
            # kills/awaits its owned group without reporting a provider failure.
            self.fail(CheckpointFailure('RUNNER_CHILD_USAGE_STORAGE_FAILED'))
        self.check()

    async def call(self, method, **arguments):
        return await provider_call(method, **arguments)

    def call_sync(self, method, *, provider, model, kwargs, normalize, owner, purpose):
        self.check()
        call_id = 'provider_' + uuid.uuid4().hex
        from src.tools.context import current_tool_execution_context
        context = current_tool_execution_context()
        try:
            from .domain_usage import physical_authorization
            execution_id = getattr(context, 'agent_execution_id', None) or self.execution_id
            tool_call_id = getattr(context, 'tool_call_id', None) or self.tool_call_id
            authorization = physical_authorization(self.attempt,execution_id,tool_call_id,purpose,call_id)
            receipt = self.repository.start(self.attempt, call_id=call_id,
                execution_id=execution_id,tool_call_id=tool_call_id, owner=owner,
                purpose=purpose, provider=provider, model=model,
                **({'domain_authorization':authorization} if authorization is not None else {}),
                boundary='covered:resume-recognition:'+call_id if owner=='llm' and purpose=='resume_recognition_covered'
                    else ('skill-call:'+call_id if self.skill_boundary and owner=='llm' else 'main'))
        except BaseException as error:
            self.fail(error)
            raise
        try:
            result = method(**kwargs)
        except BaseException:
            try:
                self.repository.uncertain(self.attempt, receipt['receipt_id'])
            except BaseException as error:
                self.fail(error)
                raise
            raise
        try:
            fact = normalize(result)
        except BaseException:
            try:
                self.repository.uncertain(self.attempt,receipt['receipt_id'])
            except BaseException as error:
                self.fail(error)
                raise
            raise
        try:
            self.repository.observe(self.attempt,receipt['receipt_id'],fact.get('usage'),
                                    request_id=fact.get('request_id'))
        except CheckpointFailure as error:
            if str(error) not in {'PROVIDER_USAGE_NOT_REPORTED','PROVIDER_USAGE_INVALID','PROVIDER_CACHE_USAGE_INVALID'}:
                self.fail(error)
                raise
            try:
                self.repository.uncertain(self.attempt,receipt['receipt_id'])
            except BaseException as storage_error:
                self.fail(storage_error)
                raise
        except BaseException as error:
            self.fail(error)
            raise
        if isinstance(result,dict):
            result['_runner_receipt_id'] = receipt['receipt_id']
            if isinstance(result.get('usage'),dict):
                result['usage']['_runner_receipt_id'] = receipt['receipt_id']
        else:
            try:
                result._runner_receipt_id = receipt['receipt_id']
            except (AttributeError,TypeError):
                pass
        return result


_scope = ContextVar("agent_runner_usage_scope", default=None)
_call = ContextVar("agent_runner_model_call", default=None)


def current_scope():
    return _scope.get()


@contextmanager
def runner_usage_scope(scope):
    from src.llm.call_observer import install_call_observer, reset_call_observer
    token = _scope.set(scope)
    observer_token = install_call_observer(scope)
    try:
        yield
    finally:
        reset_call_observer(observer_token)
        _scope.reset(token)


@contextmanager
def execution_call(execution_id, call_id):
    token = _call.set((execution_id, call_id))
    try:
        yield
    finally:
        _call.reset(token)


async def provider_call(method, *, provider, model, kwargs, owner="llm", purpose="llm"):
    """Persist start before dispatch and usage before returning a real response.

    Failover attempts each own a fact. Missing provider usage leaves finance
    pending, but a failed authoritative write must stop execution.
    """
    scope = current_scope()
    if scope is None:
        return await method(**kwargs)
    scope.check()
    from src.tools.context import current_tool_execution_context
    tool_context = current_tool_execution_context()
    execution_id = (_call.get() or (getattr(tool_context, "agent_execution_id", None) or scope.execution_id, None))[0]
    call_id = "provider_" + uuid.uuid4().hex
    try:
        from .domain_usage import physical_authorization
        tool_call_id = getattr(tool_context, 'tool_call_id', None) or scope.tool_call_id
        authorization = physical_authorization(scope.attempt,execution_id,tool_call_id,purpose,call_id)
        receipt = await asyncio.to_thread(scope.repository.start, scope.attempt,
            call_id=call_id, execution_id=execution_id,
            tool_call_id=tool_call_id, owner=owner,
            purpose=purpose, provider=provider, model=model,
            **({'domain_authorization':authorization} if authorization is not None else {}),
            boundary='covered:resume-recognition:'+call_id if owner=='llm' and purpose=='resume_recognition_covered'
                else ('skill-call:'+call_id if scope.skill_boundary and owner=='llm' else 'main'))
    except BaseException as error:
        scope.fail(error)
        raise
    try:
        result = await method(**kwargs)
    except BaseException:
        # Cancellation/transport errors cannot invent zero usage. This immutable
        # original fact can be reconciled later without granting a new dispatch.
        try:
            await asyncio.shield(asyncio.to_thread(scope.repository.uncertain,
                                                  scope.attempt, receipt["receipt_id"]))
        except BaseException as error:
            scope.fail(error)
            raise
        raise
    usage = result.get("usage") if isinstance(result, dict) and result.get("_provider_usage_reported", True) else None
    try:
        await asyncio.to_thread(scope.repository.observe, scope.attempt,
            receipt["receipt_id"], usage, provider=provider, model=model,
            request_id=result.get("request_id") if isinstance(result, dict) else None)
    except CheckpointFailure as error:
        if str(error) not in {"PROVIDER_USAGE_NOT_REPORTED", "PROVIDER_USAGE_INVALID", "PROVIDER_CACHE_USAGE_INVALID"}:
            scope.fail(error)
            raise
        try:
            await asyncio.to_thread(scope.repository.uncertain, scope.attempt, receipt["receipt_id"])
        except BaseException as storage_error:
            scope.fail(storage_error)
            raise
    except BaseException as error:
        scope.fail(error)
        raise
    if isinstance(result, dict):
        result = {**result, "provider": provider, "model": model,'_runner_receipt_id':receipt['receipt_id']}
        display_usage = result.get('usage')
        if isinstance(display_usage,dict):
            # This helper reference does not upgrade an unknown accounting fact
            # to observed, including provider-normalized display zero counts.
            result['usage'] = {**display_usage,'_runner_receipt_id':receipt['receipt_id']}
    return result


def accounted_background_usage(usage):
    """Legacy helper is an observation, never a second billing owner in a runner."""
    if current_scope() is None:
        return False
    current_scope().check()
    if usage and not usage.get("_runner_receipt_id"):
        error = CheckpointFailure("BACKGROUND_USAGE_WITHOUT_PROVIDER_RECEIPT")
        current_scope().fail(error)
        raise error
    return True


def prepare_skill_environment(env, context=None):
    """Only the trusted parent may pass runner ownership to a child process."""
    keys = ('AID_RUNNER_ID','AID_RUNNER_WORKER_ID','AID_RUNNER_ATTEMPT','AID_RUNNER_EXECUTION_ID',
            'AID_RUNNER_FAILURE_FILE','AID_RUNNER_PARENT_PGID','AID_RUNNER_PROCESS_FILE','AID_RUNNER_TOOL_CALL_ID')
    for key in keys:
        env.pop(key,None)
    scope = current_scope()
    if scope is not None:
        scope.check()
        from src.db.database import DATABASE_URL
        from src.tools.context import current_tool_execution_context
        context = context or current_tool_execution_context()
        env.update(AID_RUNNER_ID=scope.attempt.runner_id,AID_RUNNER_WORKER_ID=scope.attempt.worker_id,
                   AID_RUNNER_ATTEMPT=str(scope.attempt.number),
                   AID_RUNNER_EXECUTION_ID=getattr(context,'agent_execution_id',None) or scope.execution_id,
                   DATABASE_URL=DATABASE_URL)
        if os.name == 'posix':
            env['AID_RUNNER_PARENT_PGID'] = str(os.getpgrp())
        if scope.failure_file:
            env['AID_RUNNER_FAILURE_FILE'] = scope.failure_file
        tool_call_id = getattr(context,'tool_call_id',None) or scope.tool_call_id
        if tool_call_id:
            env['AID_RUNNER_TOOL_CALL_ID'] = tool_call_id
        bootstrap = str(Path(__file__).parent/'skill_bootstrap')
        project = str(Path(__file__).resolve().parents[3])
        env['PYTHONPATH'] = os.pathsep.join([bootstrap,project,env.get('PYTHONPATH','')])


_subprocess_scopes = {}
_subprocess_tool_scopes = {}


def install_subprocess_observer(owned_group=None):
    """Install the neutral observer at the trusted Python skill entry point."""
    from src.db.database import get_postgres_pool, init_postgres_pool
    from .ownership import Attempt
    from .usage_repository import UsageRepository
    from src.llm.call_observer import install_call_observer
    attempt = Attempt(os.environ['AID_RUNNER_ID'],os.environ['AID_RUNNER_WORKER_ID'],int(os.environ['AID_RUNNER_ATTEMPT']))
    key = (attempt.runner_id,attempt.worker_id,attempt.number)
    scope = _subprocess_scopes.setdefault(key,UsageScope(attempt,UsageRepository(),os.environ['AID_RUNNER_EXECUTION_ID'],
        skill_boundary=True,failure_file=os.environ['AID_RUNNER_FAILURE_FILE'],owned_group=owned_group,
        tool_call_id=os.environ.get('AID_RUNNER_TOOL_CALL_ID')))
    _scope.set(scope)
    install_call_observer(scope)
    try:
        scope.check()
        if get_postgres_pool() is None:
            init_postgres_pool()
        if key not in _subprocess_tool_scopes:
            from .repository import RunnerRepository
            from .ownership import LeaseLost
            from src.tools.context import ExecutionContextFactory, tool_execution_scope
            row = RunnerRepository().get(attempt.runner_id)
            if not row or row['attempt']!=attempt.number or row['worker_id']!=attempt.worker_id:
                raise LeaseLost('RUNNER_CHILD_ATTEMPT_NOT_OWNED')
            context = ExecutionContextFactory.for_agent_call(
                tenant_id=row['tenant_id'],user_id=row['user_id'],session_id=row['session_id'],
                channel=row['source'],subagent_id=os.environ.get('AID_SUBAGENT_ID'),
                chat_record_id=row['record_id'],agent_execution_id=scope.execution_id,
                tool_call_id=scope.tool_call_id,request_data={},env_vars={},infer_legacy_identity=False)
            lifetime_scope = tool_execution_scope(context)
            lifetime_scope.__enter__()
            _subprocess_tool_scopes[key] = lifetime_scope
    except BaseException as error:
        scope.fail(error)
        raise
    return scope


@contextmanager
def subprocess_usage_scope():
    """Normal skill subprocess entry; no bearer/service token is persisted here."""
    if current_scope() is not None or not os.environ.get('AID_RUNNER_ID'):
        yield
        return
    from src.db.database import get_postgres_pool, init_postgres_pool
    from .ownership import Attempt
    from .usage_repository import UsageRepository
    if get_postgres_pool() is None:
        init_postgres_pool()
    attempt = Attempt(os.environ['AID_RUNNER_ID'],os.environ['AID_RUNNER_WORKER_ID'],int(os.environ['AID_RUNNER_ATTEMPT']))
    key = (attempt.runner_id,attempt.worker_id,attempt.number)
    if key not in _subprocess_scopes:
        _subprocess_scopes[key] = UsageScope(attempt,UsageRepository(),os.environ['AID_RUNNER_EXECUTION_ID'],
            skill_boundary=True,failure_file=os.environ.get('AID_RUNNER_FAILURE_FILE'))
    scope = _subprocess_scopes[key]
    scope.check()
    with runner_usage_scope(scope):
        yield


def accounted_skill_usage(usage):
    if current_scope() is None and not os.environ.get('AID_RUNNER_ID'):
        return False
    if not usage or not usage.get('_runner_receipt_id'):
        error = CheckpointFailure('SKILL_USAGE_WITHOUT_PROVIDER_RECEIPT')
        if current_scope() is not None:
            current_scope().fail(error)
        raise error
    # The already committed actual provider fact is the only billing owner.
    # A helper callback never creates another record or charges the same fact.
    return True
