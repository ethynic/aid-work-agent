"""boss.chat_reply.v1 场景描述器（B2 完整实现，设计 §4.1/§5；替换 B1.3 骨架）。

声明层成员（scenario_key/发送能力/operation descriptor/回执策略）按设计冻结值；
行为层成员全部真实现：
- spec_validator = models.validate_boss_task_spec（BossTaskSpecPayload §5.4 冻结）；
- decision_hooks = hooks.BossConversationHooks（受限决策 select_script|fill_slots|
  handoff + 确定性渲染三级分流；rounds 完成判定）；
- adapter = adapters.BossConversationAdapter（authorize 频控权威复判三分类 +
  SAVEPOINT 结算补建/异常队列 + 双证据命名空间）；
- binding_resolver = bindings.BossBindingResolver（含 list/create 绑定管理面）；
- send_eligibility_gate = gate.boss_send_eligibility_gate（纯计算 V1.9 判别联合）
  与 binding_guard = gate.BossBindingGuard（锁/检查/落库）**成对注册**（§5.5.1
  职责切分；通用层同有同无校验）；
- settle 返回 None / {"status":"anomaly_committed","reason":受控码}（V1.9 冻结契约）。

构建函数内部延迟 import（照 weixin descriptor/registration 范式），避免 docker
冷启动时场景模块 import 顺序问题；每次 build 新实例与新 dict，不共享可变默认值。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .constants import (
    OPERATION_MESSAGE_SEND,
    PROVIDER_KEY,
    RECEIPT_POLICY,
    REQUIRED_SEND_CAPABILITY,
    SCENARIO_KEY,
)


def _operation_descriptor() -> Dict[str, str]:
    """设计 §4.1/§5.1 冻结值：target_ref 取 task.conversation_binding_id（候选人
    绑定 id；BOSS 绑定表以 account_scope_id 化名 account_binding_id）。"""
    return {
        "operation": OPERATION_MESSAGE_SEND,
        "provider_key": PROVIDER_KEY,
        "target_ref_source": "conversation_binding_id",
    }


def _receipt_policy() -> Dict[str, Any]:
    """设计 §5.1/§7.3 冻结值（常量副本；每次 build 新 dict 不共享可变默认）。"""
    return dict(RECEIPT_POLICY)


def resolve_workbench_label(cursor, tenant_id: str, binding_id: str) -> Optional[str]:  # noqa: ANN001
    """工作台 label：候选人绑定 → candidate_name × job_id（缺失返回 None）。"""
    cursor.execute(
        "SELECT candidate_name, job_id FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s",
        (tenant_id, binding_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return f"BOSS·{row['candidate_name']}"


class BossConversationDescriptor:
    """boss.chat_reply.v1 描述器（满足 ScenarioDescriptor 协议；B2 完整实现）。"""

    scenario_key = SCENARIO_KEY

    def __init__(self) -> None:
        from .adapters import BossConversationAdapter
        from .bindings import BossBindingResolver
        from .gate import BossBindingGuard, boss_send_eligibility_gate
        from .hooks import BossConversationHooks
        from .models import validate_boss_task_spec

        self.spec_validator: Callable[[dict], dict] = validate_boss_task_spec
        self.required_send_capability = REQUIRED_SEND_CAPABILITY
        self.operation_descriptor = _operation_descriptor()
        self.receipt_policy = _receipt_policy()
        self.binding_resolver = BossBindingResolver()
        self.decision_hooks = BossConversationHooks()
        self.adapter = BossConversationAdapter()
        self.workbench_label_resolver: Callable[..., Any] = resolve_workbench_label
        # 频控双闸门（设计 §5.5.1/§5.5.2）：gate 纯计算 + guard 锁/检查/落库成对
        # 注册；通用层 gate/guard 同有同无校验，微信为 None 完全绕过扩展
        self.send_eligibility_gate: Optional[Callable[..., Any]] = boss_send_eligibility_gate
        self.binding_guard = BossBindingGuard()
        # P1-5（V1.10 §4.1/§5.3 冻结）：publish 事务内话术版本强校验钩子——
        # service.publish_task 在已锁 task、写 revision 前调用；签名 (conn, tenant_id,
        # spec)。不存在/跨租户/DB hash 不一致 → SessionTaskError 409，发布零
        # revision 副作用（微信描述器本成员为 None，行为不变）
        self.validate_publish_spec: Optional[Callable[..., Any]] = self._validate_publish_spec

    @staticmethod
    def _validate_publish_spec(conn, tenant_id: str, spec: Dict[str, Any]) -> None:  # noqa: ANN001
        from .script_versions import verify_spec_scripts_published

        verify_spec_scripts_published(tenant_id, spec, conn=conn)

    def scenario_enabled(self, tenant_id: str) -> bool:
        """场景热读门控（通用生命周期按 task.scenario_key 分派；
        boss_conversation.enabled+allowlist 热读，默认 false fail-closed）。"""
        from .config import tenant_allowed

        return tenant_allowed(tenant_id)


def build_boss_descriptor() -> BossConversationDescriptor:
    """构建 boss 场景描述器（ensure_registered 调用；每次新实例）。"""
    return BossConversationDescriptor()
