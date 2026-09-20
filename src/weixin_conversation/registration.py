"""weixin.conversation.v1 受信注册点（C3）。

由受信初始化点调用（scheduler 注册 / background_runner / 决策 tick 自愈）：
- session_tasks.enabled + weixin_conversation.enabled 双门控内才注册；
- 经 register_scenario 原子注册底座场景适配器（TrustedAdapterRegistry）、
  session_tasks 决策钩子（scenario_hooks）与场景描述器（scenario_descriptor，
  B1.1），幂等且复核存活性；注册的 registry 集合与对象与 B1.1 前完全一致，
  仅多登记描述器注册表。
"""
from __future__ import annotations

import threading

from loguru import logger

from src.desktop_automation.adapters import TrustedAdapterRegistry
from src.session_tasks import scenario_descriptor, scenario_hooks
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

    def validate_decision_output(self, spec, content, peer_message_ids, decision_kind, *, task=None):  # noqa: ANN001
        # task：B2 通用层可选任务上下文（设计 §5.4，服务端取值场景用）；微信决策
        # 不使用，仅透传保持调用点签名兼容（行为零变化，B1.0 特征锁定不受影响）。
        from . import prompts

        return prompts.validate_decision_output(spec, content, peer_message_ids, decision_kind, task=task)

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
    """幂等注册描述器（含适配器 + 决策钩子）（双门控内；tick 每 tick 调用自愈）。"""
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
            and scenario_descriptor.get_descriptor(SCENARIO_KEY) is not None
        ):
            return True
        from .descriptor import build_weixin_descriptor

        scenario_descriptor.register_scenario(build_weixin_descriptor())
        _REGISTERED = True
    logger.info("weixin_conversation 适配器与决策钩子已注册（weixin.conversation.v1）")
    return True


def reset_registration() -> None:
    """测试清理用（描述器/决策钩子/适配器三处注册表，逆注册序清理）。"""
    global _REGISTERED
    with _LOCK:
        scenario_descriptor.unregister_descriptor(SCENARIO_KEY)
        scenario_hooks.unregister_hooks(SCENARIO_KEY)
        TrustedAdapterRegistry.unregister(SCENARIO_KEY)
        _REGISTERED = False
