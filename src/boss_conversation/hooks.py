"""BOSS 决策钩子（B2，设计 §5.4）：委托 prompts/completion（薄层无状态）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .constants import EXECUTION_LANE_SESSION_TASK, SCENARIO_KEY


class BossConversationHooks:
    """决策钩子实现（ScenarioDecisionHooks 契约）。

    completion_rule 仅 rounds：无 peer_confirmation/judged 审核路径（对应方法
    fail-closed 拒绝）；完成判定走 completion.evaluate_completion（rounds）。
    """

    scenario_key = SCENARIO_KEY
    execution_lane = EXECUTION_LANE_SESSION_TASK

    def build_payload_ref(self, decision_id):  # noqa: ANN001
        from .render import build_payload_ref

        return build_payload_ref(decision_id)

    def build_decision_messages(self, spec, transcript, decision_kind, *, repair_feedback=None):  # noqa: ANN001
        from . import prompts

        return prompts.build_decision_messages(spec, transcript, decision_kind, repair_feedback=repair_feedback)

    def validate_decision_output(self, spec, content, peer_message_ids, decision_kind, *, task=None):  # noqa: ANN001
        from . import prompts

        return prompts.validate_decision_output(spec, content, peer_message_ids, decision_kind, task=task)

    def build_review_messages(self, spec, transcript, proposal):  # noqa: ANN001
        from .prompts import OutputInvalid

        raise OutputInvalid("BOSS 场景（仅 rounds）无完成审核路径")

    def validate_review_output(self, content):  # noqa: ANN001
        from .prompts import OutputInvalid

        raise OutputInvalid("BOSS 场景（仅 rounds）无完成审核路径")

    def validate_review_conclusion(self, spec, review, peer_message_texts):  # noqa: ANN001
        from .prompts import OutputInvalid

        raise OutputInvalid("BOSS 场景（仅 rounds）无完成审核路径")

    def validate_peer_confirmation(self, rule_fields, confirmation, peer_messages):  # noqa: ANN001
        from .prompts import OutputInvalid

        raise OutputInvalid("BOSS 场景（仅 rounds）无 peer_confirmation 路径")

    def evaluate_completion(self, **facts: Any) -> Optional[Dict[str, str]]:
        from . import completion

        return completion.evaluate_completion(**facts)
