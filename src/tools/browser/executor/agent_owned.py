"""Agent-only authorization around the original fenced Browser executor.

Human input and frame capture keep the original Browser lease executor, not
this wrapper or a parked Runner attempt. No new Browser runtime is created.
"""

from ..owner_port import BrowserOwnerFailure, browser_agent_action_scope


class AgentOwnedBrowserExecutor:
    def __init__(self, executor, owner):
        self.executor, self.owner = executor, owner

    def __getattr__(self, name):
        method = getattr(self.executor, name)
        if name not in {'navigate', 'snapshot', 'click', 'fill', 'select', 'keyboard',
                        'pointer', 'content', 'screenshot', 'export_storage_state'}:
            return method

        async def execute(command):
            with browser_agent_action_scope(self.owner):
                result = await method(command)
            if getattr(result, 'error_code', None) in {
                'OWNER_LEASE_LOST', 'RUN_OWNER_CONFLICT', 'RUN_NOT_FOUND',
                'RUN_CANCEL_REQUESTED', 'RUN_EXPIRED', 'OWNER_FENCE_FAILED', 'CANCELLED',
            }:
                raise BrowserOwnerFailure(result.error_code)
            return result
        return execute
