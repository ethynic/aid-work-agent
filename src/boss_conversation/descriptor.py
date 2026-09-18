"""boss.chat_reply.v1 场景描述器骨架（B1.3，设计 §4.1/§5.1；计划 §6）。

**可注册的最小骨架**：声明层成员（scenario_key/发送能力/operation descriptor/
回执策略）按设计冻结值逐字声明；行为层成员（spec 校验、决策钩子、适配器授权/
证据校验、绑定查询面）全部 fail-closed 占位，完整实现随 B2 交付：

- spec_validator：占位拒绝一切输入（抛 ValueError）——即使 boss_conversation
  被误开，也无法创建任何 boss 任务，骨架成员不会被运行时触达；
- decision_hooks：scenario_key/execution_lane 正确，其余成员抛 NotImplementedError
  （决策 worker 只按已注册场景取钩子；无 boss 任务 ⇒ 永不调用；B2 交付真实实现）；
- adapter：authorize_operation/validate_submission_evidence/validate_evidence 抛
  NotImplementedError；settle_operation_result 为 no-op（无场景账本，同微信形态）；
  compile_operations 等其余协议成员 fail-closed（拒绝/needs_manual_review/异常）；
- binding_resolver：查询面抛 NotImplementedError（fail-closed；绑定表随 B2 建表）；
- send_eligibility_gate=None、binding_guard=None：频控双闸门与同步阻断随 B2 接入。

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

_SPEC_B2_NOTICE = "boss spec 校验随 B2 交付"
_HOOKS_B2_NOTICE = "boss 决策钩子随 B2 交付"
_ADAPTER_B2_NOTICE = "boss 适配器随 B2 交付"
_BINDING_B2_NOTICE = "boss 绑定查询面随 B2 交付（绑定表随 B2 建表）"


class BossConversationError(Exception):
    """boss 骨架 fail-closed 拒绝（受控载荷/编译等不可执行路径）。"""


def _reject_spec(spec: dict) -> dict:  # noqa: ANN001
    """占位 spec 校验：拒绝一切输入（fail-closed，保证骨架不被错误使用）。

    B2 以 BossTaskSpecPayload（设计 §5.4 冻结）替换；任何 boss 任务创建都会在
    spec 分派处被本占位拒绝，因此骨架的其余运行时成员不可达。
    """
    raise ValueError(_SPEC_B2_NOTICE)


def _operation_descriptor() -> Dict[str, str]:
    """设计 §4.1/§5.1 冻结值：target_ref 取 task.conversation_binding_id（与微信
    同形；BOSS 绑定表以 account_scope_id 化名 account_binding_id，B2 落表）。"""
    return {
        "operation": OPERATION_MESSAGE_SEND,
        "provider_key": PROVIDER_KEY,
        "target_ref_source": "conversation_binding_id",
    }


def _receipt_policy() -> Dict[str, Any]:
    """设计 §5.1/§7.3 冻结值（常量副本；每次 build 新 dict 不共享可变默认）。"""
    return dict(RECEIPT_POLICY)


class _BossSkeletonHooks:
    """决策钩子骨架：标识成员正确，行为成员抛 NotImplementedError（B2 交付）。

    仅在 boss 场景存在待决策任务时被决策 worker 调用；spec_validator 占位拒绝
    一切 boss 任务创建 ⇒ 本类行为成员不可达。保留可注册性以满足
    register_scenario 的三 key 一致预检。
    """

    scenario_key = SCENARIO_KEY
    execution_lane = "session_task"

    def build_decision_messages(self, spec, transcript, decision_kind, *, repair_feedback=None):  # noqa: ANN001
        raise NotImplementedError(_HOOKS_B2_NOTICE)

    def validate_decision_output(self, spec, content, peer_message_ids, decision_kind):  # noqa: ANN001
        raise NotImplementedError(_HOOKS_B2_NOTICE)

    def build_review_messages(self, spec, transcript, proposal):  # noqa: ANN001
        raise NotImplementedError(_HOOKS_B2_NOTICE)

    def validate_review_output(self, content):  # noqa: ANN001
        raise NotImplementedError(_HOOKS_B2_NOTICE)

    def validate_review_conclusion(self, spec, review, peer_message_texts):  # noqa: ANN001
        raise NotImplementedError(_HOOKS_B2_NOTICE)

    def validate_peer_confirmation(self, rule_fields, confirmation, peer_messages):  # noqa: ANN001
        raise NotImplementedError(_HOOKS_B2_NOTICE)

    def evaluate_completion(self, **facts):  # noqa: ANN001
        raise NotImplementedError(_HOOKS_B2_NOTICE)

    def build_payload_ref(self, decision_id):  # noqa: ANN001
        raise NotImplementedError(_HOOKS_B2_NOTICE)


class _BossSkeletonResolver:
    """绑定查询面骨架：fail-closed（全部抛 NotImplementedError；B2 随
    bs_boss_conversation_bindings 建表交付真实实现）。"""

    def get_binding_by_id(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        raise NotImplementedError(_BINDING_B2_NOTICE)

    def get_runtime_identity(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        raise NotImplementedError(_BINDING_B2_NOTICE)

    def is_valid_for_allocation(self, conn, tenant_id: str, binding_id: str) -> bool:  # noqa: ANN001
        raise NotImplementedError(_BINDING_B2_NOTICE)

    def resolve_draft_targets(self, conn, tenant_id: str, user_id: str, device_id: str,
                              resolution_invocation_id: str):  # noqa: ANN001
        raise NotImplementedError(_BINDING_B2_NOTICE)

    def ensure_valid_for_publish(self, binding: Dict[str, Any]) -> None:  # noqa: ANN001
        raise NotImplementedError(_BINDING_B2_NOTICE)

    def runtime_target_policy(self, binding_row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        raise NotImplementedError(_BINDING_B2_NOTICE)

    def account_identity_version(self, binding_row: Optional[Dict[str, Any]]) -> int:  # noqa: ANN001
        raise NotImplementedError(_BINDING_B2_NOTICE)

    def list_bindings(self, tenant_id, user_id, device_id, limit):  # noqa: ANN001
        """BOSS 骨架未实现绑定管理 API：显式受控拒绝（CR 三审 P1-8）。"""
        from src.session_tasks.scenario_descriptor import ScenarioDescriptorError

        raise ScenarioDescriptorError("BOSS 骨架不支持绑定管理 API")

    def create_binding(self, tenant_id, user_id, device_id, account_binding_id, binding_type, label):  # noqa: ANN001
        """BOSS 骨架未实现绑定管理 API：显式受控拒绝（CR 三审 P1-8）。"""
        from src.session_tasks.scenario_descriptor import ScenarioDescriptorError

        raise ScenarioDescriptorError("BOSS 骨架不支持绑定管理 API")

class BossSkeletonAdapter:
    """boss 场景适配器骨架（ScenarioAdapter 协议）。

    授权与证据校验抛 NotImplementedError（fail-closed：即使骨架被误触达也不
    签发许可/不接纳证据）；settle 为 no-op（无场景账本，SAVEPOINT 包裹 no-op
    不改变行为）；其余成员 fail-closed 拒绝。真实实现随 B2/B3 交付。
    """

    scenario_key = SCENARIO_KEY

    def validate_revision(self, ctx, revision_config):  # noqa: ANN001
        return _fail_closed_revision()

    def resolve_target(self, ctx, target_ref):  # noqa: ANN001
        return _fail_closed_target()

    def authorize_operation(
        self,
        ctx,
        *,
        operation: str,
        target_ref,
        target_version,
        payload_hash,
        authorization_revision,
        authorization_epoch,
        invocation=None,
        cursor=None,
    ):  # noqa: ANN001
        raise NotImplementedError(_ADAPTER_B2_NOTICE)

    def validate_submission_evidence(self, ctx) -> bool:  # noqa: ANN001
        raise NotImplementedError(_ADAPTER_B2_NOTICE)

    def validate_evidence(self, ctx) -> bool:  # noqa: ANN001
        raise NotImplementedError(_ADAPTER_B2_NOTICE)

    def settle_operation_result(self, cursor, result) -> None:  # noqa: ANN001
        """结算骨架 no-op：无场景账本（频控账本/异常队列随 B2 建表），SAVEPOINT
        包裹 no-op 零行为；B2 按设计 §5.5.4 顺序 3–6 替换。"""
        return None

    def compile_operations(self, ctx, revision_config):  # noqa: ANN001
        raise BossConversationError(_ADAPTER_B2_NOTICE)

    def aggregate_result(self, ctx, delivery_results):  # noqa: ANN001
        from src.desktop_automation.adapters import RunBusinessResult

        return RunBusinessResult(
            verdict="needs_manual_review",
            summary="boss_chat_reply_skeleton（B2 交付业务判定）",
        )

    def serve_payload(self, ctx, payload_ref: str) -> bytes:  # noqa: ANN001
        raise BossConversationError(_ADAPTER_B2_NOTICE)

    def invocation_receipt_arguments(self, ctx, target_ref) -> Dict[str, str]:  # noqa: ANN001
        """空冻结回执参数（fail-closed）：submitted 接纳要求 invocation 冻结的
        receipt_mode/context 与描述器 receipt_policy 一致，空参必不匹配。"""
        return {}


def _fail_closed_revision():
    from src.desktop_automation.adapters import RevisionValidation

    return RevisionValidation(ok=False, reason="boss_chat_reply_skeleton", schedule_specs=[])


def _fail_closed_target():
    from src.desktop_automation.adapters import TargetResolution

    return TargetResolution(ok=False, reason="boss_chat_reply_skeleton")


def resolve_workbench_label(cursor, tenant_id: str, binding_id: str) -> str:  # noqa: ANN001
    """工作台 label 骨架：固定文案（绑定表随 B2 落地后按绑定行解析）。"""
    return "BOSS 会话"


class BossConversationDescriptor:
    """boss.chat_reply.v1 描述器骨架（满足 ScenarioDescriptor 协议的可注册对象）。"""

    scenario_key = SCENARIO_KEY

    def __init__(self) -> None:
        self.spec_validator: Callable[[dict], dict] = _reject_spec
        self.required_send_capability = REQUIRED_SEND_CAPABILITY
        self.operation_descriptor = _operation_descriptor()
        self.receipt_policy = _receipt_policy()
        self.binding_resolver = _BossSkeletonResolver()
        self.decision_hooks = _BossSkeletonHooks()
        self.adapter = BossSkeletonAdapter()
        self.workbench_label_resolver: Callable[..., Any] = resolve_workbench_label
        # 频控门禁与 binding 同步阻断随 B2 接入（设计 §5.5.1/§5.5.2）：骨架不注册
        # gate/guard，通用层按"gate=None 场景"绕过扩展，prepare-send 锁面不扩大
        self.send_eligibility_gate: Optional[Callable[..., Any]] = None
        self.binding_guard = None

    def scenario_enabled(self, tenant_id: str) -> bool:
        """场景热读门控（CR 阻断 2：通用生命周期按 task.scenario_key 分派；
        boss_conversation.enabled+allowlist 热读，默认 false fail-closed）。"""
        from .config import tenant_allowed

        return tenant_allowed(tenant_id)


def build_boss_descriptor() -> BossConversationDescriptor:
    """构建 boss 场景描述器骨架（ensure_registered 调用；每次新实例）。"""
    return BossConversationDescriptor()
