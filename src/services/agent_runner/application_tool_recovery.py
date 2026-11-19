"""Installed local and server tool recovery capabilities for Runner."""

from .contracts import RunnerError
from .local_recovery import LocalRecoveryProof


class ApplicationToolRecovery:
    """Finite installed capabilities; the scheduling/kernel ports stay neutral."""
    def __init__(self,connection_factory,profiles):
        self.connection_factory=connection_factory
        self.profiles=profiles
        self.local=LocalRecoveryProof(connection_factory)

    def can_restore(self,row,state,fact):
        from .server_tool_recovery import (INPUT_UNAVAILABLE, TOOL_UNAVAILABLE, can_repeat_server_call,
            quotation_available, quotation_call, quotation_input, recovery_skill)
        from .server_recovery_reply import (external_recovery, interactive_recovery,
            registered_server_tool, server_skill_recovery)
        if can_repeat_server_call(fact):
            if fact.call.name == 'skill_execute':
                if not quotation_available(state,self.profiles):
                    raise RunnerError(TOOL_UNAVAILABLE,409)
                if not interactive_recovery(state):
                    match = quotation_call(fact.call)
                    if quotation_input(match,state,row['runner_id'],self.connection_factory) is None:
                        raise RunnerError(INPUT_UNAVAILABLE,409)
            return True
        if self.local.can_restore(row,state,fact):
            return True
        if interactive_recovery(state) and fact.phase == 'dispatching':
            if fact.call.name == 'skill_execute':
                name = fact.call.arguments.get('skill')
                if not isinstance(name,str) or not name:
                    return False
                from src.tools.skill.skill_execute_tool import SkillExecuteTool
                return (external_recovery(state,fact,SkillExecuteTool)
                        and server_skill_recovery(state,fact,recovery_skill(state,self.profiles,name)))
            return external_recovery(state,fact,registered_server_tool(fact.call.name))
        return False
