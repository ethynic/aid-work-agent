"""内容冻结 + 适配器契约测试（R41：authorize 拒绝矩阵 / aggregate 全分支 /
serve_payload 哈希校验 / validate_evidence 结构与 fail-closed）"""

from dataclasses import replace
from datetime import timedelta

import pytest

from src.desktop_automation.adapters import AdapterContext, EvidenceContext
from src.desktop_automation.payload_resolver import resolve_payload
from src.weixin_marketing import content
from src.weixin_marketing.constants import SCENARIO_KEY
from tests.unit.weixin_marketing.conftest import (
    create_and_publish,
    make_create_payload,
    utcnow,
)

pytestmark = pytest.mark.unit


def _ctx(tenant_id, automation_id, revision_id, user_id="owner-1"):
    return AdapterContext(
        tenant_id=tenant_id, user_id=user_id, scenario_key=SCENARIO_KEY,
        task_ref=automation_id, revision_ref=revision_id,
    )


class TestContentFreeze:
    def test_payload_ref_roundtrip(self):
        ref = content.build_payload_ref("rev-abc", 3)
        assert ref == "da:weixin.fixed_content.v1:rev-abc:3"
        assert content.parse_payload_ref(ref) == ("rev-abc", 3)

    def test_payload_ref_rejects_foreign(self):
        with pytest.raises(content.ContentError):
            content.parse_payload_ref("da:other.scenario:rev:1")
        with pytest.raises(content.ContentError):
            content.parse_payload_ref("da:weixin.fixed_content.v1:rev:notanumber")

    def test_block_bytes_and_hash_stability(self):
        text_block = {"kind": "text", "text_content": "你好 world"}
        link_block = {"kind": "link", "url": "https://example.com/x"}
        assert content.block_payload_bytes(text_block) == "你好 world".encode("utf-8")
        assert content.block_payload_bytes(link_block) == b"https://example.com/x"
        import hashlib

        assert content.payload_hash_of(text_block) == hashlib.sha256("你好 world".encode()).hexdigest()
        # 内容指纹：内容敏感（按 position 规整后摘要；改文即变）
        blocks = [dict(text_block, position=1), dict(link_block, position=2)]
        h1 = content.content_hash_of(blocks)
        tampered = [dict(text_block, text_content="被改的正文", position=1), dict(link_block, position=2)]
        assert h1 != content.content_hash_of(tampered)
        # 同内容同 position → 指纹稳定（幂等重算一致）
        assert h1 == content.content_hash_of([dict(b) for b in blocks])

    def test_serve_payload_hash_mismatch_fail_closed(self, service, tenant_id, bindings, adapter):
        """篡改存储行 text_content（不动 payload_hash）→ serve 抛错（fail-closed）"""
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_weixin_marketing_content_blocks SET text_content = '被篡改的正文' "
                "WHERE tenant_id = %s AND revision_id = %s AND position = 1",
                (tenant_id, revision_id),
            )
            conn.commit()
        ctx = _ctx(tenant_id, automation_id, revision_id)
        with pytest.raises(content.ContentError):
            adapter.serve_payload(ctx, content.build_payload_ref(revision_id, 1))

    def test_payload_resolver_end_to_end(self, service, tenant_id, bindings, adapter):
        """da: 前缀 → TrustedAdapterRegistry → 冻结字节 + mime/hash 校验"""
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
        resolution = resolve_payload(
            tenant_id, content.build_payload_ref(revision_id, 1),
            expectation_hash=content.payload_hash_of({"kind": "text", "text_content": "第一条内容"}),
        )
        assert resolution.data == "第一条内容".encode("utf-8")
        assert resolution.mime.startswith("text/plain")


class TestAuthorizeMatrix:
    _group_id = None
    _revision_id = None

    def _setup(self, service, tenant_id, bindings):
        account_id, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
        self._group_id = group_id
        self._revision_id = revision_id
        return automation_id, revision_id, group_id

    def _block_hash(self, tenant_id, revision_id, position=1):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT payload_hash FROM bs_weixin_marketing_content_blocks "
                "WHERE tenant_id = %s AND revision_id = %s AND position = %s",
                (tenant_id, revision_id, position),
            )
            return cur.fetchone()["payload_hash"]

    def test_authorize_success_returns_quota_scopes(self, service, tenant_id, bindings, adapter):
        automation_id, revision_id, group_id = self._setup(service, tenant_id, bindings)
        ctx = _ctx(tenant_id, automation_id, revision_id)
        decision = adapter.authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=group_id,
            target_version="iv-7-se-3", payload_hash=self._block_hash(tenant_id, revision_id),
            authorization_revision=revision_id, authorization_epoch=0,
        )
        assert decision.allowed
        scope_types = [s.scope_type for s in decision.quota_scopes]
        assert scope_types == ["tenant", "task", "target", "account"]
        assert all(s.limit_count > 0 for s in decision.quota_scopes)

    def test_authorize_denial_matrix(self, service, tenant_id, bindings, adapter):
        automation_id, revision_id, group_id = self._setup(service, tenant_id, bindings)
        ctx = _ctx(tenant_id, automation_id, revision_id)
        payload_hash = self._block_hash(tenant_id, revision_id)

        # 未知操作
        d = adapter.authorize_operation(
            ctx, operation="weixin_probe", target_ref=group_id, target_version=None,
            payload_hash=payload_hash, authorization_revision=revision_id, authorization_epoch=0,
        )
        assert not d.allowed and "unsupported_operation" in d.reason
        # 非属主
        d = adapter.authorize_operation(
            _ctx(tenant_id, automation_id, revision_id, user_id="intruder"),
            operation="weixin_message_send_v2", target_ref=group_id, target_version=None,
            payload_hash=payload_hash, authorization_revision=revision_id, authorization_epoch=0,
        )
        assert not d.allowed and d.reason == "not_owner"
        # revision 不匹配
        d = adapter.authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=group_id, target_version=None,
            payload_hash=payload_hash, authorization_revision="00000000-0000-0000-0000-000000000000",
            authorization_epoch=0,
        )
        assert not d.allowed and d.reason == "revision_not_active"
        # payload_hash 不在该 revision
        d = adapter.authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=group_id, target_version=None,
            payload_hash="deadbeef" * 8, authorization_revision=revision_id, authorization_epoch=0,
        )
        assert not d.allowed and d.reason == "payload_hash_not_in_revision"
        # 群绑定 pending
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_weixin_marketing_group_bindings SET state = 'pending' "
                "WHERE tenant_id = %s AND id = %s", (tenant_id, group_id),
            )
            conn.commit()
        d = adapter.authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=group_id, target_version=None,
            payload_hash=payload_hash, authorization_revision=revision_id, authorization_epoch=0,
        )
        assert not d.allowed and d.reason == "group_binding_state:pending"
        # 账号停用
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_weixin_marketing_group_bindings SET state = 'complete' "
                "WHERE tenant_id = %s AND id = %s", (tenant_id, group_id),
            )
            account_id, _ = bindings
            cur.execute(
                "UPDATE bs_weixin_marketing_account_bindings SET status = 'disabled' "
                "WHERE tenant_id = %s AND id = %s", (tenant_id, account_id),
            )
            conn.commit()
        d = adapter.authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=group_id, target_version=None,
            payload_hash=payload_hash, authorization_revision=revision_id, authorization_epoch=0,
        )
        assert not d.allowed and d.reason == "account_binding_disabled"

    def test_authorize_rejected_when_automation_paused(self, service, tenant_id, bindings, adapter):
        from src.weixin_marketing.models import VersionedActionInput

        automation_id, revision_id, group_id = self._setup(service, tenant_id, bindings)
        service.pause(tenant_id, automation_id, "owner-1", VersionedActionInput(expected_version=2))
        ctx = _ctx(tenant_id, automation_id, revision_id)
        d = adapter.authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=group_id, target_version=None,
            payload_hash=self._block_hash(tenant_id, revision_id),
            authorization_revision=revision_id, authorization_epoch=0,
        )
        assert not d.allowed and d.reason == "automation_status:paused"

    def test_authorize_missing_automation(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        ctx = _ctx(tenant_id, "00000000-0000-0000-0000-000000000000", "rev-x")
        d = adapter.authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=group_id, target_version=None,
            payload_hash=None, authorization_revision="rev-x", authorization_epoch=0,
        )
        assert not d.allowed and d.reason == "automation_missing"


class TestResolveTarget:
    def test_resolve_complete_binding(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
        resolution = adapter.resolve_target(_ctx(tenant_id, automation_id, revision_id), group_id)
        assert resolution.ok
        assert resolution.target_handle
        assert resolution.target_version == "iv-7-se-3"

    def test_resolve_denials(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
        ctx = _ctx(tenant_id, automation_id, revision_id)
        assert not adapter.resolve_target(ctx, "00000000-0000-0000-0000-000000000000").ok
        # 跨租户/不存在 → not found
        r = adapter.resolve_target(ctx, "not-a-uuid")
        assert not r.ok and r.reason == "group_binding_not_found"
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_weixin_marketing_group_bindings SET state = 'pending' "
                "WHERE tenant_id = %s AND id = %s", (tenant_id, group_id),
            )
            conn.commit()
        r = adapter.resolve_target(ctx, group_id)
        assert not r.ok and r.reason == "group_binding_state:pending"


class TestAggregate:
    def test_all_branches(self, adapter):
        ctx = AdapterContext(tenant_id="t", user_id="u", scenario_key=SCENARIO_KEY,
                             task_ref="task", revision_ref="rev")
        all_ok = [
            {"state": "succeeded", "effect": "applied", "phase": "verified"},
            {"state": "succeeded", "effect": "applied", "phase": "verified"},
        ]
        assert adapter.aggregate_result(ctx, all_ok).verdict == "all_delivered"
        with_unknown = [
            {"state": "succeeded", "effect": "applied", "phase": "verified"},
            {"state": "unknown", "effect": "unknown", "phase": "unknown"},
            {"state": "skipped", "effect": None, "phase": None},
        ]
        assert adapter.aggregate_result(ctx, with_unknown).verdict == "needs_manual_review"
        partial = [
            {"state": "succeeded", "effect": "applied", "phase": "verified"},
            {"state": "failed", "effect": "none", "phase": "prepared"},
        ]
        assert adapter.aggregate_result(ctx, partial).verdict == "partially_delivered"
        none_ok = [{"state": "failed", "effect": "none", "phase": "prepared"}]
        assert adapter.aggregate_result(ctx, none_ok).verdict == "none_delivered"


class TestValidateEvidence:
    def _ctx(self, request_id, evidence_ref):
        return EvidenceContext(
            tenant_id="t", scenario_key=SCENARIO_KEY,
            request_id=request_id, evidence_ref=evidence_ref,
        )

    def test_structural_accept(self, adapter):
        assert adapter.validate_evidence(self._ctx("req-1", "weixin-evidence:req-1:1"))
        assert adapter.validate_evidence(self._ctx("req-1", "weixin-evidence:req-1:12"))

    def test_structural_rejections(self, adapter):
        assert not adapter.validate_evidence(self._ctx("req-1", "weixin-evidence:req-2:1"))
        assert not adapter.validate_evidence(self._ctx("req-1", "other-ns:req-1:1"))
        assert not adapter.validate_evidence(self._ctx("req-1", "weixin-evidence:req-1"))
        assert not adapter.validate_evidence(self._ctx("req-1", ""))
        assert not adapter.validate_evidence(self._ctx("req-1", "weixin-evidence:req-1:abc"))
        assert not adapter.validate_evidence(self._ctx("req-1", "随便乱串"))

    def test_real_mode_fail_closed_without_verifier(self, wx_config):
        from src.weixin_marketing.adapters import WeixinFixedContentAdapter

        strict = WeixinFixedContentAdapter(config=replace(wx_config, evidence_real_mode=True))
        # 结构完全正确，但真实校验器缺失 → fail-closed（R43）
        assert not strict.validate_evidence(self._ctx("req-1", "weixin-evidence:req-1:1"))

    def test_pluggable_verifier(self, wx_config):
        from src.weixin_marketing.adapters import WeixinFixedContentAdapter

        class FakeVerifier:
            def __init__(self, ok):
                self.ok = ok
                self.calls = []

            def verify(self, ctx):
                self.calls.append(ctx.evidence_ref)
                return self.ok

        ok_verifier = FakeVerifier(True)
        adapter_ok = WeixinFixedContentAdapter(
            config=replace(wx_config, evidence_real_mode=True), evidence_verifier=ok_verifier,
        )
        assert adapter_ok.validate_evidence(self._ctx("req-1", "weixin-evidence:req-1:1"))
        assert ok_verifier.calls == ["weixin-evidence:req-1:1"]

        bad_verifier = FakeVerifier(False)
        adapter_bad = WeixinFixedContentAdapter(config=wx_config, evidence_verifier=bad_verifier)
        assert not adapter_bad.validate_evidence(self._ctx("req-1", "weixin-evidence:req-1:1"))

    def test_verifier_exception_fail_closed(self, wx_config):
        from src.weixin_marketing.adapters import WeixinFixedContentAdapter

        class ExplodingVerifier:
            def verify(self, ctx):
                raise RuntimeError("boom")

        adapter = WeixinFixedContentAdapter(
            config=wx_config, evidence_verifier=ExplodingVerifier(),
        )
        assert not adapter.validate_evidence(self._ctx("req-1", "weixin-evidence:req-1:1"))


class TestCompileOperations:
    def test_compile_skips_images_and_orders(self, service, tenant_id, bindings, adapter):
        from dataclasses import asdict

        _, group_id = bindings
        blocks = [
            {"type": "text", "text_content": "A"},
            {"type": "link", "url": "https://e.com/b"},
            {"type": "text", "text_content": "C"},
        ]
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, blocks=blocks
        )
        revision_config = service.load_revision_config(tenant_id, revision_id)
        ops = adapter.compile_operations(_ctx(tenant_id, automation_id, revision_id), revision_config)
        assert [op.position for op in ops] == [1, 2, 3]
        dicts = [asdict(op) for op in ops]
        assert all(d["operation"] == "weixin_message_send_v2" for d in dicts)
        assert all(d["provider_key"] == "weixin" for d in dicts)
        assert dicts[0]["payload_ref"] == f"da:{SCENARIO_KEY}:{revision_id}:1"
        assert all(d["target_ref"] == group_id for d in dicts)
        # 编译产物 hash 与冻结块一致（serve 字节可复算）
        assert dicts[1]["payload_hash"] == content.payload_hash_of(
            {"kind": "link", "url": "https://e.com/b"}
        )


class TestInputValidation:
    """P2-7：引用 ID UUID 形态校验（422）+ resolve note 长度上限"""

    def test_group_binding_must_be_uuid(self, wx_config):
        from pydantic import ValidationError

        from src.weixin_marketing.models import AutomationCreateInput

        with pytest.raises(ValidationError):
            AutomationCreateInput(
                name="t",
                trigger={"type": "once", "run_at": "2026-09-09T00:00:00+00:00"},
                blocks=[{"type": "text", "text_content": "x"}],
                group_binding_id="not-a-uuid",
            )

    def test_asset_id_must_be_uuid(self):
        from pydantic import ValidationError

        from src.weixin_marketing.models import parse_blocks

        with pytest.raises(ValidationError):
            parse_blocks([{"type": "image", "asset_id": "not-a-uuid"}])

    def test_resolve_note_max_length(self):
        from pydantic import ValidationError

        from src.weixin_marketing.models import DeliveryResolveInput

        DeliveryResolveInput(verdict="delivered", note="x" * 2000)  # 边界可过
        with pytest.raises(ValidationError):
            DeliveryResolveInput(verdict="delivered", note="x" * 2001)

    def test_draft_update_group_binding_uuid(self):
        from pydantic import ValidationError

        from src.weixin_marketing.models import DraftUpdateInput

        with pytest.raises(ValidationError):
            DraftUpdateInput(expected_version=1, group_binding_id="bad")

    def test_load_revision_config_rejects_draft(self, service, tenant_id, bindings, adapter):
        """P2-10：revision 状态 guard——draft 不可编译（executor 不驱动未发布配置）"""
        from src.weixin_marketing.service import ConflictError

        _, group_id = bindings
        detail = service.create_automation(
            tenant_id, "owner-1", make_create_payload(group_id)
        )
        draft_id = str(detail["automation"]["draft_revision_id"])
        with pytest.raises(ConflictError):
            service.load_revision_config(tenant_id, draft_id)

    def test_once_grace_seconds_custom(self, service, tenant_id, bindings, adapter, wx_config):
        """P2-10：once 可选 grace（默认 300）→ schedule 行冻结该值"""
        _, group_id = bindings
        trigger = {
            "type": "once",
            "run_at": (utcnow() + timedelta(hours=1)).isoformat(),
            "timezone": "UTC",
            "grace_seconds": 90,
        }
        detail = service.create_automation(
            tenant_id, "owner-1", make_create_payload(group_id, trigger=trigger)
        )
        automation = detail["automation"]
        from src.weixin_marketing.models import PublishInput

        service.publish(tenant_id, str(automation["id"]), "owner-1", PublishInput(expected_version=1))
        from src.desktop_automation import schedules as da_schedules

        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, str(automation["id"]))
        assert rows[0]["grace_seconds"] == 90
