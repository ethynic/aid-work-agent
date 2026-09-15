"""weixin.conversation.v1 受信注册点（C3）。

由受信初始化点调用（scheduler 注册 / background_runner / 决策 tick 自愈）：
- session_tasks.enabled + weixin_conversation.enabled 双门控内才注册；
- 同时注册底座场景适配器（TrustedAdapterRegistry）与 session_tasks 决策钩子
  （scenario_hooks），幂等且复核存活性。
"""
from __future__ import annotations

import threading

from loguru import logger

from src.desktop_automation.adapters import TrustedAdapterRegistry
from src.session_tasks import scenario_hooks
from src.session_tasks.config import get_session_tasks_config

from .constants import EXECUTION_LANE_SESSION_TASK, SCENARIO_KEY

_LOCK = threading.Lock()
_REGISTERED = False


class _ConversationHooks:
    """决策钩子实现：直接委托 prompts/completion/render（保持薄层，无状态）。"""

    scenario_key = SCENARIO_KEY
    execution_lane = EXECUTION_LANE_SESSION_TASK

    def build_payload_ref(self, decision_id):  # noqa: ANN001
        from .render import build_payload_ref

        return build_payload_ref(decision_id)

    def build_decision_messages(self, spec, transcript, decision_kind, *, repair_feedback=None):  # noqa: ANN001
        from . import prompts

        return prompts.build_decision_messages(spec, transcript, decision_kind, repair_feedback=repair_feedback)

    def validate_decision_output(self, spec, content, peer_message_ids, decision_kind):  # noqa: ANN001
        from . import prompts

        return prompts.validate_decision_output(spec, content, peer_message_ids, decision_kind)

    def build_review_messages(self, spec, transcript, proposal):  # noqa: ANN001
        from . import prompts

        return prompts.build_review_messages(spec, transcript, proposal)

    def validate_review_output(self, content):  # noqa: ANN001
        from . import prompts

        return prompts.validate_review_output(content)

    def validate_review_conclusion(self, spec, review, peer_message_texts):  # noqa: ANN001
        from . import prompts

        return prompts.validate_review_conclusion(spec, review, peer_message_texts)

    def validate_peer_confirmation(self, rule_fields, confirmation, peer_messages):  # noqa: ANN001
        from . import completion

        return completion.validate_peer_confirmation(rule_fields, confirmation, peer_messages)

    def evaluate_completion(self, **facts):  # noqa: ANN001
        from . import completion

        return completion.evaluate_completion(**facts)


def ensure_registered() -> bool:
    """幂等注册适配器 + 决策钩子（双门控内；tick 每 tick 调用自愈）。"""
    global _REGISTERED
    cfg = get_session_tasks_config()
    from .config import scenario_enabled_gate

    if not cfg.enabled or not scenario_enabled_gate():
        return False
    with _LOCK:
        if (
            _REGISTERED
            and TrustedAdapterRegistry.get(SCENARIO_KEY) is not None
            and scenario_hooks.get_hooks(SCENARIO_KEY) is not None
        ):
            return True
        from .adapters import WeixinConversationAdapter

        TrustedAdapterRegistry.register(WeixinConversationAdapter())
        scenario_hooks.register_hooks(_ConversationHooks())
        _REGISTERED = True
    logger.info("weixin_conversation 适配器与决策钩子已注册（weixin.conversation.v1）")
    return True


def reset_registration() -> None:
    """测试清理用。"""
    global _REGISTERED
    with _LOCK:
        TrustedAdapterRegistry.unregister(SCENARIO_KEY)
        scenario_hooks.unregister_hooks(SCENARIO_KEY)
        _REGISTERED = False
