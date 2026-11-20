"""Apply the existing local invocation lifecycle to registered MCP tools."""

import asyncio

from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.exceptions import Unresolvable

from . import repository
from .lifecycle import current_local_operation, local_operation_scope
from .plugin_manifests import PLUGIN_PHASE
from .proxy_tool import LocalToolProxyTool


PLUGIN_CALL_IDS = 'runtime_plugin_call_ids'
PLUGIN_BINDINGS = 'runtime_plugin_bindings'
_INTERNAL_ARGUMENTS = frozenset({'_trusted_tenant_id', '_trusted_user_id', '_session_id', '_progress_queue'})


def _public_arguments(arguments):
    return {key: value for key, value in arguments.items() if key not in _INTERNAL_ARGUMENTS}


class RuntimePluginTool(LocalToolProxyTool):
    catalog = False
    category = 'local_plugin'
    chat_only = True
    heal_eligible = False
    requires_device_declaration = True
    unknown_effect_notice = ''
    boss_login_recovery = False

    def __init__(self, *, device_id, provider_key, definition, plugin_manifest_digest=None):
        self.device_id = device_id
        self.provider_key = self.invocation_provider_key = provider_key
        self.plugin_manifest_digest = plugin_manifest_digest
        self.name = definition['name']
        self.description = definition.get('description', '')
        self.display_name = definition.get('title') or self.name
        self.parameters_schema = definition.get('inputSchema', {})
        Draft202012Validator.check_schema(self.parameters_schema)
        self._validator = Draft202012Validator(self.parameters_schema, registry=Registry())

    @property
    def manifest_binding(self):
        return [self.device_id, self.provider_key, self.plugin_manifest_digest]

    def validate_parameters(self, **kwargs):
        return not self.get_validation_errors(**kwargs)

    def get_validation_errors(self, **kwargs):
        try:
            error = next(self._validator.iter_errors(_public_arguments(kwargs)), None)
        except Unresolvable:
            return ['工具参数定义包含无法读取的引用']
        if error is None:
            return []
        field = '.'.join(map(str, error.absolute_path)) or '参数'
        # Validation errors may include actual argument values (e.g. a password).
        return [f'{field}不符合工具参数定义（{error.validator}）']

    async def execute(self, **kwargs):
        from src.tools.context import current_tool_execution_context

        context = current_tool_execution_context()
        if context is None or not context.tenant_id or not context.user_id or context.channel != 'chat':
            return _failure('NO_IDENTITY', '当前会话无法使用本机插件工具')
        operation = current_local_operation()
        if operation is None:
            return plugin_not_dispatched()
        devices = await asyncio.to_thread(repository.list_devices, context.tenant_id, context.user_id)
        device = next((item for item in devices if str(item['id']) == self.device_id), None)
        if not device or not device.get('selected') or device.get('status') != 'active':
            return _failure('DEVICE_UNAVAILABLE', '已选 Runtime 发生变化，本次操作未派发')
        if self._tool_credit_price() > 0:
            blocked = await self._tenant_credit_blocked(context.tenant_id)
            if blocked:
                return _failure('NO_CREDIT', blocked)
        # The locked device check compares last_seen_at against PostgreSQL's clock.
        with local_operation_scope(operation.owner, branch=PLUGIN_PHASE):
            return await self._dispatch_and_wait(context.tenant_id, context.user_id, context.session_id,
                device, _public_arguments(kwargs), kwargs.get('_progress_queue'))

    async def recover(self, operation, progress_queue):
        saved = await operation.owner.saved_phase(branch=PLUGIN_PHASE, ordinal=0)
        if saved is None:
            return plugin_not_dispatched()
        invocation = saved['invocation']
        with local_operation_scope(operation.owner, branch=PLUGIN_PHASE):
            return await self._poll_invocation(invocation, invocation['tenant_id'], progress_queue, durable=True)


def _failure(code, message):
    return {'success': False, 'code': code, 'message': message, 'effect': 'none'}


def plugin_not_dispatched():
    return _failure('LOCAL_NOT_DISPATCHED', '本机插件操作未派发')


def plugin_invocation_ref(state, call_id):
    for fact in (state.resources.get('local_invocations') or {}).values():
        if fact.get('tool_call_id') == call_id and fact.get('branch') == PLUGIN_PHASE and fact.get('ordinal') == 0:
            return fact
    return None


def bound_plugin_tool(state, call):
    """A resumed turn polls its original call even after changing devices."""
    ref = plugin_invocation_ref(state, call.id)
    if ref is None:
        return None
    request = ref['request']
    return RuntimePluginTool(device_id=request['device_id'], provider_key=request['provider_key'],
                             definition={'name': call.name}, plugin_manifest_digest=request.get('plugin_manifest_digest'))
