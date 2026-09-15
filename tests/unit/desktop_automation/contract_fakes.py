"""双消费方 v2 契约样例 fake 适配器（P1-D 交付物 2，测试资产——不进真实 CLI）

weixin.fixed_content.v1 与 boss.chat_reply.v1 两个场景消费同一 v2 操作协议：
- compile_operations 产出 R15 统一操作描述（weixin_message_send_v2 / boss_send_to_v2），
  两消费方共享同一字段集，无任何群/候选人专用字段；
- serve_payload 提供冻结字节（1x1 PNG / UTF-8 文本），payload_ref 形态
  da:<scenario_key>:<opaque>，opaque 由适配器解释；
- allowed_tenant 模拟素材租户 ACL：错租户取字节直接拒绝。
"""

import hashlib
from typing import Any, Dict, List, Optional

from src.desktop_automation.adapters import (
    AdapterContext,
    AuthorizeDecision,
    CompiledOperation,
    EvidenceContext,
    RevisionValidation,
    RunBusinessResult,
    TargetResolution,
)

WEIXIN_SCENARIO_KEY = "weixin.fixed_content.v1"
BOSS_SCENARIO_KEY = "boss.chat_reply.v1"
WEIXIN_V2_OPERATION = "weixin_message_send_v2"
BOSS_V2_OPERATION = "boss_send_to_v2"

# R27 场景证据命名空间：evidence_ref 必须形如 v2-contract-evidence:<request_id>:<seq>
CONTRACT_EVIDENCE_NAMESPACE = "v2-contract-evidence"

# R15 v2 统一操作描述顶层字段集（两消费方共享；契约测试断言字段名集合 diff 为空）
V2_OPERATION_FIELDS = (
    "protocol_version",
    "operation",
    "provider_key",
    "target_ref",
    "target_handle",
    "target_version",
    "payload_ref",
    "payload_hash",
    "request_id",
    "delivery_id",
    "authorization_revision",
    "authorization_epoch",
    "resource_key",
    "deadline_at",
)

# 冻结字节：微信消费方为 PNG 签名前缀的冻结字节（契约测试只做 magic 嗅探与
# hash 冻结，不解码图像）；BOSS 消费方为 UTF-8 固定回复文本
FROZEN_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"weixin-fixed-content-frozen-sample"
FROZEN_TEXT_BYTES = "boss-fixed-reply-frozen-sample".encode("utf-8")


class DualConsumerContractFakeAdapter:
    """双消费方契约样例：两实例仅 scenario/operation/provider/字节不同，字段集共享"""

    def __init__(
        self,
        *,
        scenario_key: str,
        operation: str,
        provider_key: str,
        payload_bytes: bytes,
        allowed_tenant: Optional[str] = None,
        opaque: str = "frozen-1",
    ):
        self.scenario_key = scenario_key
        self.operation = operation
        self.provider_key = provider_key
        self.allowed_tenant = allowed_tenant
        self.payload_ref = f"da:{scenario_key}:{opaque}"
        self.payload_bytes = payload_bytes
        self.payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        self.serve_calls: List[Dict[str, Any]] = []

    # ---- ScenarioAdapter 协议 ----

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
    ) -> AuthorizeDecision:
        return AuthorizeDecision(allowed=True)

    def compile_operations(
        self, ctx: AdapterContext, revision_config: Dict[str, Any]
    ) -> List[CompiledOperation]:
        """产出 v2 中立操作描述分量：字段集与另一消费方完全一致，无场景专用字段"""
        return [
            CompiledOperation(
                position=1,
                operation=self.operation,
                provider_key=self.provider_key,
                target_ref="contract-target-1",
                target_handle="handle:contract-target-1",
                target_version="tv-1",
                payload_ref=self.payload_ref,
                payload_hash=self.payload_hash,
            )
        ]

    def aggregate_result(
        self, ctx: AdapterContext, delivery_results: List[Dict[str, Any]]
    ) -> RunBusinessResult:
        return RunBusinessResult(verdict="contract-ok", summary=f"{len(delivery_results)} deliveries")

    def validate_evidence(self, ctx: EvidenceContext) -> bool:
        """R27：契约消费方证据命名空间校验——v2-contract-evidence:<本次 request_id>:<seq>，
        乱串/错误命名空间/编码他 operation 的 request_id 一律 False（fail-closed）"""
        parts = (ctx.evidence_ref or "").split(":")
        return (
            len(parts) == 3
            and parts[0] == CONTRACT_EVIDENCE_NAMESPACE
            and parts[1] == ctx.request_id
            and bool(parts[2])
            and parts[2].isdigit()
        )

    def serve_payload(self, ctx: AdapterContext, payload_ref: str) -> bytes:
        """冻结字节 + 租户 ACL：错租户拒绝、未知 opaque 拒绝（fail-closed）"""
        self.serve_calls.append({"tenant_id": ctx.tenant_id, "payload_ref": payload_ref})
        if self.allowed_tenant is not None and ctx.tenant_id != self.allowed_tenant:
            raise PermissionError(f"cross-tenant payload refused tenant={ctx.tenant_id}")
        if payload_ref != self.payload_ref:
            raise KeyError(payload_ref)
        return self.payload_bytes


def make_weixin_fixed_content_adapter(allowed_tenant: Optional[str] = None):
    return DualConsumerContractFakeAdapter(
        scenario_key=WEIXIN_SCENARIO_KEY,
        operation=WEIXIN_V2_OPERATION,
        provider_key="weixin",
        payload_bytes=FROZEN_PNG_BYTES,
        allowed_tenant=allowed_tenant,
    )


def make_boss_chat_reply_adapter(allowed_tenant: Optional[str] = None):
    return DualConsumerContractFakeAdapter(
        scenario_key=BOSS_SCENARIO_KEY,
        operation=BOSS_V2_OPERATION,
        provider_key="boss-recruiting",
        payload_bytes=FROZEN_TEXT_BYTES,
        allowed_tenant=allowed_tenant,
    )
