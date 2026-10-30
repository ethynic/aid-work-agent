"""Active settlement and bounded checkpoints at durable execution boundaries."""

import asyncio
from .finalizer import RunnerFinalizer
from .ownership import StopRequested


async def before_dispatch(control, state=None, *, quiescent=False):
    if not _enabled(control,state):
        return
    row = await asyncio.to_thread(RunnerFinalizer(control.repository.connection_factory).settle_active,control.attempt)
    # Active settlement changes only public state, never the checkpoint revision.
    control.snapshot['usage'] = row['public_snapshot']['usage']
    if state is None:
        return
    current = dispatch_name(state)
    if not quiescent and current in {'agent_sleep','ask_user','clarify','create_plan','use_skill','skill_complete','present_options'}:
        return
    from .contracts import RunnerError
    try:
        await asyncio.to_thread(assert_credit,control.repository.connection_factory,row)
        if quiescent or not active_children(state):
            from .continuous_context import prepare_compaction
            async def mark_action(summary_state):
                await control._authorize_execution(summary_state.profile_id)
                activation = control.envelope.get('timer_activation')
                if isinstance(activation,dict) and activation.get('attempt')==control.attempt.number:
                    activation['action_started'] = True
            await prepare_compaction(state,before_summary=mark_action)
            from .continuous_history import archive_and_compact
            await asyncio.to_thread(archive_and_compact,control,state)
        row = await asyncio.to_thread(RunnerFinalizer(control.repository.connection_factory).settle_active,control.attempt)
        control.snapshot['usage'] = row['public_snapshot']['usage']
        await asyncio.to_thread(assert_credit,control.repository.connection_factory,row)
    except RunnerError:
        state.resources['credit_blocked'] = True
        control.credit_paused = True
        raise StopRequested('pause')
    state.resources.pop('credit_blocked',None)


def dispatch_name(state):
    node = state.checkpoint()
    while node.get('pending'):
        call = node['pending'][0]
        child = (node.get('children') or {}).get(call['id'],{}).get('checkpoint')
        if not isinstance(child,dict) or child.get('outcome') in {'completed','failed','cancelled','iteration_limit'}:
            return call['name']
        node = child
    return None


def active_children(state):
    return any((child.get('checkpoint') or {}).get('outcome')
        not in {'completed','failed','cancelled','iteration_limit'} for child in state.children.values())


def assert_credit(connection_factory, row):
    from src.config.settings import settings
    if row.get('tenant_id') is None or settings.deployment.private_cloud:
        return
    from .contracts import RunnerError
    with connection_factory() as connection:
        cursor = connection.cursor()
        cursor.execute('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(row['tenant_id'],))
        tenant = cursor.fetchone()
        if tenant is None or tenant['credit_balance'] <= 0:
            raise RunnerError('CREDIT_BLOCKED',402)


def _enabled(control,state=None):
    # Only new feature states activate this lifecycle. Ordinary legacy tasks keep
    # their accepted finalization and history semantics.
    from .continuous import feature_enabled
    state = state or control.state
    return feature_enabled({**control.envelope,
        **({'execution':state.checkpoint()} if state else {})})


async def before_park(control, state):
    if not _enabled(control):
        return
    row = await asyncio.to_thread(RunnerFinalizer(control.repository.connection_factory).settle_active,control.attempt)
    control.snapshot['usage'] = row['public_snapshot']['usage']
    # Saving a sleep/pause/cancel must never depend on a new paid summary. Keep
    # original context; the next authorized dispatch compacts before doing work.
    state.resources.setdefault('history_version',1)
