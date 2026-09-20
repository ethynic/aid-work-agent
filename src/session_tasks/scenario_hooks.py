"""session_tasks 通用层 → 场景实现的受信决策钩子注册表（C3，设计 §3）。

通用 worker（claim/租约/预算/状态权威）不反向 import 微信实现；场景侧提供：
- 模型 prompt 构造与受限输出校验（build/validate）；
- 完成判定辅助（peer_confirmation 硬校验、完成评估入参语义由场景定义）。

钩子对象按 scenario_key 注册（进程内显式注册，与 TrustedAdapterRegistry 同范式）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class ScenarioDecisionHooks(Protocol):
    """场景决策钩子契约（weixin.conversation.v1 为首个实现）。"""

    scenario_key: str
    execution_lane: str

    def build_decision_messages(
        self, spec: Dict[str, Any], transcript: List[Dict[str, Any]], decision_kind: str,
        *, repair_feedback: Optional[str] = None,
    ) -> List[Dict[str, str]]: ...

    # task（B2 可选上下文，设计 §5.4）：任务行数据（tenant_id/conversation_binding_id
    # 等），供需要服务端取值的场景（BOSS resume_field 槽位经绑定 resume_id 从简历库
    # 取值）使用；微信等不需要的场景忽略。keyword-only 缺省 None 保持旧签名兼容。
    def validate_decision_output(
        self, spec: Dict[str, Any], content: str, peer_message_ids: List[str], decision_kind: str,
        *,
        task: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...

    def build_review_messages(
        self, spec: Dict[str, Any], transcript: List[Dict[str, Any]], proposal: Dict[str, Any],
    ) -> List[Dict[str, str]]: ...

    def validate_review_output(self, content: str) -> Dict[str, Any]: ...

    def validate_review_conclusion(
        self, spec: Dict[str, Any], review: Dict[str, Any], peer_message_texts: Dict[str, str],
    ) -> None: ...

    def validate_peer_confirmation(
        self, rule_fields: List[Dict[str, Any]], confirmation: Dict[str, Any],
        peer_messages: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]: ...

    def evaluate_completion(self, **facts: Any) -> Optional[Dict[str, str]]:
        """完成/终止判定：输入任务事实，返回 None=维持 active 或 {status, reason}。"""
        ...

    def build_payload_ref(self, decision_id: str) -> str:
        """决策冻结正文的受控 payload_ref（场景规范，服务端生成）。"""
        ...


class ScenarioHooksError(Exception):
    """场景钩子缺失/非法。"""


_REGISTRY: Dict[str, ScenarioDecisionHooks] = {}


def register_hooks(hooks: ScenarioDecisionHooks) -> None:
    _REGISTRY[hooks.scenario_key] = hooks


def unregister_hooks(scenario_key: str) -> None:
    _REGISTRY.pop(scenario_key, None)


def get_hooks(scenario_key: str) -> Optional[ScenarioDecisionHooks]:
    return _REGISTRY.get(scenario_key)


def registered_hook_keys() -> List[str]:
    return list(_REGISTRY.keys())


def require_hooks(scenario_key: str) -> ScenarioDecisionHooks:
    hooks = _REGISTRY.get(scenario_key)
    if hooks is None:
        raise ScenarioHooksError(f"场景决策钩子未注册: {scenario_key}")
    return hooks
