"""Compatibility history is a projection, never the runner's recovery ledger."""

import copy
import json


def waiting_messages(state):
    messages = copy.deepcopy(state.messages[state.initial_len:])
    for pending in state.pending[1:]:
        messages.append({"role": "tool", "tool_call_id": pending.id,
            "content": json.dumps({"success": False,
                "error_code": "TOOL_DEFERRED_BY_HUMAN_ASSISTANCE",
                "error": "浏览器人工协助完成后由 Agent 重新决定是否执行"}, ensure_ascii=False)})
    return messages
