"""desktop_automation 测试假适配器（P1-A：不依赖任何真实场景模块）

经 TrustedAdapterRegistry 进程内受信注册（仅测试 import 本模块并显式 register）。

R27 validate_evidence：默认严格命名空间校验——evidence_ref 必须形如
``<namespace>:<request_id>:<seq>`` 且中段精确等于本次 request_id（乱串/错误命名空间/
编码他 operation 的 request_id 一律 False）。``evidence_namespace=None`` 为测试专用
放行逃生门（跳过适配器环），**生产适配器禁止照抄此放行实现，必须实现真实校验**。
R27 反例用例以此构造拒绝；绑定仲裁类用例用 evidence_require_request_id=False
（仅校验命名空间格式）使「他操作的证据」到达 INSERT 冲突比对环节。
"""

import hashlib
import re
from typing import Any, Dict, List, Optional

from src.desktop_automation.adapters import (
    AdapterContext,
    AuthorizeDecision,
    CompiledOperation,
    EvidenceContext,
    QuotaScopeSpec,
    RevisionValidation,
    RunBusinessResult,
    TargetResolution,
)

# R27 反例用例使用的场景证据命名空间（与契约样例 v2-fake-evidence:<request_id>:<seq> 对齐）
FAKE_EVIDENCE_NAMESPACE = "v2-fake-evidence"
_EVIDENCE_REF_RE = re.compile(r"^[^:\s]+:[^:\s]+:\d+$")


def namespaced_evidence_ref(
    namespace: str, request_id: str, seq: int = 1
) -> str:
    """按场景证据命名空间编码 evidence_ref（<namespace>:<request_id>:<seq>）"""
    return f"{namespace}:{request_id}:{seq}"


class FakeScenarioAdapter:
    """可配置假适配器：payload/操作清单/授权决策/额度层级/证据校验均可注入"""

    scenario_key = "fake-scenario"
    PROVIDER_KEY = "fake-provider"

    def __init__(
        self,
        *,
        scenario_key: str = "fake-scenario",
        operations: Optional[List[Dict[str, Any]]] = None,
        payloads: Optional[Dict[str, bytes]] = None,
        quota_limit: int = 10,
        quota_window: int = 3600,
        authorize_allowed: bool = True,
        authorize_reason: Optional[str] = None,
        authorize_decisions: Optional[List[AuthorizeDecision]] = None,
        evidence_namespace: Optional[str] = FAKE_EVIDENCE_NAMESPACE,
        evidence_require_request_id: bool = True,
    ):
        self.scenario_key = scenario_key
        # operations: [{position, operation, target_ref, target_version, payload_ref}]
        self.operations = operations or []
        self.payloads = payloads or {}
        self.quota_limit = quota_limit
        self.quota_window = quota_window
        self.authorize_allowed = authorize_allowed
        self.authorize_reason = authorize_reason
        # 按调用次序弹出的授权决策（区分 executor 预检与 permit 事务两次调用的测试）
        self.authorize_decisions: List[AuthorizeDecision] = list(authorize_decisions or [])
        self.authorize_calls: List[Dict[str, Any]] = []
        # R27：默认严格命名空间校验（v2-fake-evidence:<本次 request_id>:<seq>）；
        # evidence_namespace=None 为测试专用放行逃生门——生产适配器禁止照抄此放行
        # 实现，必须实现真实存在性/归属校验；evidence_require_request_id=False 仅校验
        # 命名空间格式（供绑定仲裁类反例把「他操作证据」送到 INSERT 冲突比对环节）
        self.evidence_namespace = evidence_namespace
        self.evidence_require_request_id = evidence_require_request_id
        self.validate_evidence_calls: List[Dict[str, Any]] = []

    def validate_revision(self, ctx: AdapterContext, revision_config: Dict[str, Any]) -> RevisionValidation:
        return RevisionValidation(ok=True, schedule_specs=revision_config.get("schedule_specs", []))

    def resolve_target(self, ctx: AdapterContext, target_ref: str) -> TargetResolution:
        return TargetResolution(ok=True, target_handle=f"handle:{target_ref}", target_version="tv-1")

    def authorize_operation(
        self,
        ctx: AdapterContext,
        *,
        operation: str,
        target_ref: Optional[str],
        target_version: Optional[str],
        payload_hash: Optional[str],
        authorization_revision: Optional[str],
        authorization_epoch: Optional[int],
        invocation: Optional[Dict[str, Any]] = None,
        cursor: Optional[Any] = None,
    ) -> AuthorizeDecision:
        self.authorize_calls.append(
            {
                "tenant_id": ctx.tenant_id, "operation": operation,
                "target_ref": target_ref, "target_version": target_version,
                "payload_hash": payload_hash,
                "authorization_revision": authorization_revision,
                "authorization_epoch": authorization_epoch,
            }
        )
        if not self.authorize_allowed:
            return AuthorizeDecision(allowed=False, reason=self.authorize_reason or "denied")
        if self.authorize_decisions:
            return self.authorize_decisions.pop(0)
        return AuthorizeDecision(
            allowed=True,
            quota_scopes=[
                QuotaScopeSpec(
                    scope_type="tenant", scope_id=ctx.tenant_id,
                    limit_count=self.quota_limit, window_seconds=self.quota_window,
                ),
                QuotaScopeSpec(
                    scope_type="task", scope_id=f"{ctx.scenario_key}:{ctx.task_ref}",
                    limit_count=self.quota_limit, window_seconds=self.quota_window,
                ),
            ],
        )

    def compile_operations(
        self, ctx: AdapterContext, revision_config: Dict[str, Any]
    ) -> List[CompiledOperation]:
        ops = []
        for spec in self.operations:
            payload_ref = spec.get("payload_ref")
            ops.append(
                CompiledOperation(
                    position=spec["position"],
                    operation=spec["operation"],
                    provider_key=spec.get("provider_key", self.PROVIDER_KEY),
                    target_ref=spec["target_ref"],
                    target_handle=spec.get("target_handle"),
                    target_version=spec.get("target_version", "tv-1"),
                    payload_ref=payload_ref,
                    payload_hash=spec.get("payload_hash") or (
                        hashlib.sha256(self.payloads.get(payload_ref, b"")).hexdigest()
                        if payload_ref else None
                    ),
                )
            )
        return ops

    def aggregate_result(
        self, ctx: AdapterContext, delivery_results: List[Dict[str, Any]]
    ) -> RunBusinessResult:
        return RunBusinessResult(
            verdict="fake-ok", summary=f"{len(delivery_results)} deliveries"
        )

    def validate_evidence(self, ctx: EvidenceContext) -> bool:
        """R27 证据存在性/归属校验：默认严格命名空间 + 本次 request_id 编码；
        evidence_namespace=None 放行仅为测试逃生门（生产适配器禁止照抄）。"""
        self.validate_evidence_calls.append(
            {
                "tenant_id": ctx.tenant_id, "scenario_key": ctx.scenario_key,
                "request_id": ctx.request_id, "evidence_ref": ctx.evidence_ref,
            }
        )
        if not self.evidence_namespace:
            return True
        if not ctx.evidence_ref or not _EVIDENCE_REF_RE.match(ctx.evidence_ref):
            return False
        namespace, request_component, _seq = ctx.evidence_ref.split(":", 2)
        if namespace != self.evidence_namespace:
            return False
        if self.evidence_require_request_id and request_component != ctx.request_id:
            return False
        return True

    def serve_payload(self, ctx: AdapterContext, payload_ref: str) -> bytes:
        return self.payloads.get(payload_ref, b"")

    # 可选可见范围方法（api.py 侧 hasattr 探测）
    def visible_to_user(self, delivery: Dict[str, Any], user_id: Optional[str], role: Optional[str]) -> bool:
        return delivery.get("user_id") == user_id
