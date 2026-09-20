"""weixin.conversation.v1 场景描述器（B1.1，设计 §4.1）。

单一注册对象的微信实现：逐字复刻当前通用层的微信硬编码（发送能力、operation
descriptor、receipt 双命名空间、绑定查询面），B1.2 泛化切换调用点前保持同语义：

- spec_validator = session_tasks.models.validate_task_spec（原函数直引，零包装）；
- operation_descriptor / receipt_policy 与 decisions.py prepare-send、
  operation_result.py 现状逐键一致；verified_evidence_namespace=None 如实声明
  现状宽松结构校验（通用层对 verified 证据不做命名空间存在性校验，仅适配器
  validate_evidence 结构匹配），B1.2 接线时保持同语义（B1.0 特征测试锁定）；
- send_eligibility_gate=None：微信不注册频控门禁，通用层直走既有 prepare-send，
  不额外锁场景 binding、不新增门禁写操作（设计 §4.1）；
- settle_operation_result 为适配器 no-op（微信结算现状无场景账本）。

构建函数内部延迟 import（照 registration.py 范式），避免 docker 冷启动时场景
模块 import 顺序问题；每次 build 新实例与新 dict，不共享可变默认值。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .constants import SCENARIO_KEY

REQUIRED_SEND_CAPABILITY = "weixin_message_send_v2"


def _operation_descriptor() -> Dict[str, str]:
    """逐字复刻 decisions.py prepare-send revision_config 现状（B1.2 切换前不动）。

    target_ref 现状取 task.conversation_binding_id；payload_ref/payload_hash 由
    prepare 阶段冻结，不属于描述器静态声明。
    """
    return {
        "operation": "weixin_message_send_v2",
        "provider_key": "weixin",
        "target_ref_source": "conversation_binding_id",
    }


def _receipt_policy() -> Dict[str, Any]:
    """逐字复刻现状回执策略（B1.0 特征锁定，B1.2 接线保持同语义）。

    mode/context：submitted 证据接纳仅认 receipt_mode=submission +
    receipt_context=weixin_name（operation_result._check_evidence_and_register
    与适配器 invocation_receipt_arguments 现状）；submission 证据命名空间
    weixin-submission（适配器 validate_submission_evidence 精确匹配
    weixin-submission:<request_id>:1）；verified_evidence_namespace=None 表示
    现状对 verified 证据不做通用层命名空间存在性校验。
    """
    return {
        "mode": "submission",
        "context": "weixin_name",
        "submission_evidence_namespace": "weixin-submission",
        "verified_evidence_namespace": None,
    }


class _WeixinBindingResolver:
    """微信绑定查询面：语义复刻 session_tasks.service 现查 SQL。

    B1.1 只登记契约，B1.2 才切换 service/workbench 调用点（成员粒度允许后续
    微调）；只读查询，写操作仍由通用层持有（设计 §5.5.1 职责切分）。
    """

    def get_binding_by_id(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        """按 id 取绑定行（列集 = service._verify_bindings 与
        _binding_valid_for_allocation 两处 SELECT 的并集；不存在返回 None）。"""
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, device_id, account_binding_id, verification_status,
                   identity_version, verified_at, verifier_version, expires_at
            FROM bs_weixin_conversation_bindings WHERE tenant_id=%s AND id=%s
            """,
            (tenant_id, binding_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_runtime_identity(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        """运行时身份字段（claim 响应身份段现状查询：绑定验证版本/会话 label +
        LEFT JOIN 账号 scope 的 session_epoch；不存在返回 None）。"""
        cursor.execute(
            """
            SELECT b.identity_version,b.verifier_version,b.conversation_label,
                   a.session_epoch AS account_version
            FROM bs_weixin_conversation_bindings b
            LEFT JOIN bs_weixin_marketing_account_bindings a ON a.tenant_id=b.tenant_id AND a.id=b.account_binding_id
            WHERE b.tenant_id=%s AND b.id=%s
            """,
            (tenant_id, binding_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def is_valid_for_allocation(self, conn, tenant_id: str, binding_id: str) -> bool:  # noqa: ANN001
        """领取/分配时的绑定复核：语义 = service._binding_valid_for_allocation。

        名称定位上下文（verifier_version=current-login-name-v1）走 resolved
        有效性；常规绑定 verified + identity_version≥1 + 验证字段完整且未过期
        （expires_at=NULL 不视为无限有效）。
        """
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT verification_status, identity_version, verified_at, verifier_version, expires_at
            FROM bs_weixin_conversation_bindings WHERE tenant_id=%s AND id=%s
            """,
            (tenant_id, binding_id),
        )
        row = cursor.fetchone()
        from .name_contexts import is_name_context, name_context_valid

        if is_name_context(row):
            return name_context_valid(row)
        if row is None or row["verification_status"] != "verified":
            return False
        if int(row["identity_version"] or 0) < 1:
            return False
        if not row["verified_at"] or not row["verifier_version"] or row["expires_at"] is None:
            return False
        expires_at = row["expires_at"]
        expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

    def resolve_draft_targets(self, conn, tenant_id: str, user_id: str, device_id: str,
                              resolution_invocation_id: str):  # noqa: ANN001
        """名称定位结果 → (account_binding_id, conversation_binding_id)。

        语义 = service.create_draft 现状直调 name_contexts.from_resolution
        （B1.2 切换调用点，函数本体零改动）；仅微信场景支持 resolution 路径
        （models.py 约束 resolution_invocation_id 与非微信场景互斥）。"""
        from .name_contexts import from_resolution

        return from_resolution(conn, tenant_id, user_id, device_id, resolution_invocation_id)

    def ensure_valid_for_publish(self, binding: Dict[str, Any]) -> None:  # noqa: ANN001
        """发布有效性校验（service._verify_bindings require_verified 分支原样迁移）：
        逐条错误文案/状态码与 B1.1 前一致；名称定位上下文走 resolved 有效性。"""
        from src.session_tasks.constants import ERR_VALIDATION_FAILED, SessionTaskError

        from .name_contexts import is_name_context, name_context_valid

        if is_name_context(binding):
            if not name_context_valid(binding):
                raise SessionTaskError("名称定位上下文已失效", ERR_VALIDATION_FAILED, 409)
            return
        if binding["verification_status"] != "verified":
            raise SessionTaskError("会话绑定尚未通过真机验证（pending 绑定不可发布生产任务）", ERR_VALIDATION_FAILED, 409)
        if int(binding["identity_version"] or 0) < 1:
            raise SessionTaskError("会话绑定 identity_version 无效（须有已接纳的真机证据版本）", ERR_VALIDATION_FAILED, 409)
        if not binding["verified_at"] or not binding["verifier_version"] or binding["expires_at"] is None:
            raise SessionTaskError("会话绑定验证字段不完整（verified_at/verifier_version/expires_at 必填）", ERR_VALIDATION_FAILED, 409)
        expires_at = binding["expires_at"]
        expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise SessionTaskError("会话绑定验证已过期，需重新核验", ERR_VALIDATION_FAILED, 409)

    def runtime_target_policy(self, binding_row: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:  # noqa: ANN001
        """claim 响应的运行时目标策略（service.claim_task 原样迁移）：
        名称定位上下文 → current_login_name 路由；常规绑定 → None（无 _runtime_target）。"""
        from .name_contexts import is_name_context

        if binding_row and is_name_context(binding_row):
            return {"policy": "current_login_name", "target_name": binding_row["conversation_label"]}
        return None

    def account_identity_version(self, binding_row: Optional[Dict[str, Any]]) -> int:  # noqa: ANN001
        """claim 响应的账号身份版本（service.claim_task 原样迁移）：
        名称定位上下文或缺失行 → 0；否则取账号 scope 的 session_epoch。"""
        from .name_contexts import is_name_context

        if not binding_row or is_name_context(binding_row):
            return 0
        return int(binding_row["account_version"] or 0)

    def list_bindings(self, tenant_id: str, user_id: str, device_id: str, limit: int):  # noqa: ANN001
        """绑定管理 API 列表（api.bindings_router 按 scenario_key 路由；B1.2 九处 #7）。"""
        from . import bindings as bindings_service

        return bindings_service.list_bindings(tenant_id, user_id, device_id, limit)

    def create_binding(self, tenant_id: str, user_id: str, device_id: str,
                       account_binding_id: str, binding_type: str, label: str):  # noqa: ANN001
        """绑定管理 API 创建（同上）。"""
        from . import bindings as bindings_service

        return bindings_service.create_binding(
            tenant_id, user_id, device_id, account_binding_id, binding_type, label
        )


def resolve_workbench_label(cursor, tenant_id: str, binding_id: str) -> Optional[str]:  # noqa: ANN001
    """工作台 binding_label（语义 = workbench.projection 现查 SQL；缺失返回 None）。"""
    cursor.execute(
        "SELECT conversation_label FROM bs_weixin_conversation_bindings WHERE tenant_id=%s AND id=%s",
        (tenant_id, binding_id),
    )
    row = cursor.fetchone()
    return row["conversation_label"] if row else None


class WeixinScenarioDescriptor:
    """weixin.conversation.v1 描述器（满足 ScenarioDescriptor 协议的纯声明对象）。"""

    scenario_key = SCENARIO_KEY

    def __init__(self) -> None:
        from src.session_tasks.models import validate_task_spec

        # spec 校验入口：原函数直接引用（零包装零行为变化，B1.0 三类特征用例锁定）
        self.spec_validator: Callable[[dict], dict] = validate_task_spec
        self.required_send_capability = REQUIRED_SEND_CAPABILITY
        self.operation_descriptor = _operation_descriptor()
        self.receipt_policy = _receipt_policy()
        self.binding_resolver = _WeixinBindingResolver()
        from .registration import _ConversationHooks

        self.decision_hooks = _ConversationHooks()
        from .adapters import WeixinConversationAdapter

        self.adapter = WeixinConversationAdapter()
        self.workbench_label_resolver: Callable[..., Any] = resolve_workbench_label
        # 微信不注册频控门禁（设计 §4.1）：通用层完全绕过 gate 扩展
        self.send_eligibility_gate: Optional[Callable[..., Any]] = None
        # 微信无场景 binding 同步门禁（设计 §5.5.2）：prepare-send 锁面不扩大、
        # 不新增 binding 行锁或门禁写操作（B1.0 特征锁定）
        self.binding_guard = None
        # 微信无发布事务内 spec 强校验钩子（V1.10 §4.1）：既有发布校验与锁面
        # 不变（spec 白名单/版本存在性校验为 BOSS 话术版本表语义，P1-5）
        self.validate_publish_spec = None

    def scenario_enabled(self, tenant_id: str) -> bool:
        """场景热读门控（B1.2 通用生命周期分派；weixin_conversation.enabled 节点）。"""
        from .config import scenario_enabled

        return scenario_enabled(tenant_id)


def build_weixin_descriptor() -> ScenarioDescriptor:
    """构建微信场景描述器（ensure_registered 调用；每次新实例）。"""
    return WeixinScenarioDescriptor()
