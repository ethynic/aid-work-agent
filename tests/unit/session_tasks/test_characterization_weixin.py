"""B1.0 微信端侧会话任务现状特征测试（零回归基线，设计 §9.1 / 计划 §3）。

目的：在 B1.1–B1.3 通用层泛化（ScenarioDescriptor/九处去微信化/envelope 分派）之前，
固化微信链路"现在是什么样"的可执行快照。特征测试如实锁定现状——包括硬编码
（submitted 回执策略只认 weixin.conversation.v1）与宽松匹配（verified 证据仅结构
绑定校验）——后续泛化以"本文件全绿"为硬放行条件；标注【B1.2 泛化点】处即计划
预期的行为切换位置。

与既有测试的分工（不重复覆盖）：
- 设备协议（claim/fence/租约/events/决策幂等/能力拒绝）：test_device_protocol.py
- 决策 worker 全矩阵（reply/wait/完成判定/supersede/预算/人工介入）：test_c3_decisions.py
- prepare-send 幂等物化/定向 claim/execution_lane 隔离/适配器授权链：test_c3_execution.py
- operation-result 通用落账矩阵（fake 适配器 harness，R27/R28 全分支）：
  tests/unit/desktop_automation/test_operation_result.py
- submitted 证据结构反例（mock cursor 单点）：tests/unit/desktop_automation/test_submission_receipt.py
- API envelope/幂等重放/401：test_api.py
本文件只补缺口 + 写引用性薄测试固化关键点：
  1. 缺省 scenario_key 全链（create 场景归属、claim 响应键集现状、PATCH 忽略 scenario、renew stale 409）
  2. submitted 回执微信真链路接纳 + 三类拒绝原因值 + validate_submission_evidence 精确匹配
  3. evidence-invalid 现状（ACK/attempt 保留原值/delivery 归一 unknown 三元组/审计）
  3b. 旧 verified 回执（weixin 命名空间接纳登记 + 同操作重复回执 ON CONFLICT 幂等）
  4. spec 非法三类的 HTTP 状态码与 pydantic loc 路径快照
  5. prepare-send 判别语义缺口（pending→409 CONFLICT/任务暂停→409/租约过期→409/spec 版本失配→superseded）
  6. 能力缺失逐项拒绝（REQUIRED_DEVICE_CAPABILITIES 三能力）
  7. action 词汇 {reply,wait,handoff,done} 锁定 + 模型 handoff 转人工真链路
  8. execution_links UNIQUE(tenant,decision) DB 级约束幂等
"""

import json
import uuid
from dataclasses import replace

import pytest

from src.session_tasks import decisions as decisions_mod
from src.session_tasks import service
from src.session_tasks.constants import (
    ERR_CAPABILITY_MISSING,
    ERR_LEASE_EXPIRED,
    ERR_STALE_ASSIGNMENT,
    SessionTaskError,
)

from tests.unit.session_tasks.conftest import (
    build_draft_payload,
    build_spec,
    publish_task_helper,
)
from tests.unit.session_tasks.test_c3_decisions import (
    _batch_seq_by_assignment,
    _cfg,
    _decision_row,
    _device_dict,
    _fake_model,
    _feed_batch,
    _publish_and_claim,
    _submit_reply,
    _task_row,
)

# desktop_automation/local_tool 行族（本链路产生；conftest 租户清理不含，测试后补清）
_DA_TABLES = (
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_deliveries",
    "desktop_automation_runs",
    "desktop_automation_occurrences",
    "desktop_automation_outbox",
    "desktop_automation_events",
    "desktop_automation_audit_events",
    "desktop_automation_quota_buckets",
    "local_tool_operation_permits",
    "local_tool_events",
    "local_tool_invocations",
)


@pytest.fixture()
def da_tenant(tenant_id):
    """扩展 conftest tenant_id：测后补清本链路产生的底座表行（不进共享库）。"""
    yield tenant_id
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        for table in _DA_TABLES:
            try:
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            except Exception:  # noqa: BLE001 单表失败不阻断其余清理
                conn.rollback()
        conn.commit()


@pytest.fixture(autouse=True)
def char_env(monkeypatch):
    """注册微信场景 + 放开决策/许可链门控 + 屏蔽真实计费（照 test_c3_gate 范式）。"""
    import src.session_tasks.config as st_config
    import src.weixin_conversation.config as wx_config
    import src.weixin_conversation.registration as registration

    monkeypatch.setattr(registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
    monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
    monkeypatch.setattr(st_config, "tenant_allowed", lambda tenant: True)
    registration.ensure_registered()
    monkeypatch.setattr(decisions_mod, "get_session_tasks_config", lambda: _cfg())
    monkeypatch.setattr(decisions_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr(
        "src.services.session_record.record_background_llm_usage", lambda usage, **kw: None
    )
    monkeypatch.setattr(
        "src.services.billing.calculate_credit_cost_with_breakdown",
        lambda p, c, m, cached_input_tokens=0: (0.01, {}),
    )
    _batch_seq_by_assignment.clear()
    yield
    registration.reset_registration()


@pytest.fixture()
def client(monkeypatch, tenant_id):
    """API 冒烟客户端（照 test_api.client 范式；不启动整个 src.main）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.session_tasks import api as session_tasks_api

    app = FastAPI()
    app.include_router(session_tasks_api.router)
    app.include_router(session_tasks_api.device_router)

    @app.middleware("http")
    async def _inject_tenant(request, call_next):  # noqa: ANN202
        tid = request.headers.get("X-Tenant-Id")
        if tid:
            request.state.tenant_id = tid
        return await call_next(request)

    monkeypatch.setattr(session_tasks_api, "_get_current_user_sync", lambda request: {"user_id": "user-1"})
    with TestClient(app) as tc:
        yield tc


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------


def _payload_model(payload: dict):
    from src.session_tasks.models import TaskDraftCreatePayload

    return TaskDraftCreatePayload.model_validate(payload)


def _device_with_token(tenant_id, device_row, raw_token):
    """为既有设备行写入已知 token（API 设备认证用；conftest device_row 只存随机 hash）。"""
    from src.db.database import get_db_connection
    from src.local_tools.security import sha256_hex

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE local_tool_devices SET token_hash=%s WHERE tenant_id=%s AND id=%s",
            (sha256_hex(raw_token), tenant_id, device_row["id"]),
        )
        conn.commit()


def _resolution_invocation(tenant_id, device_row, target_name="特征测试-联系人"):
    """合成只读名称定位 invocation（照 test_name_contexts.resolved 范式；
    清理由 da_tenant 统一做 local_tool_invocations 删除）。

    finished_at 写成"NOW() - 30 秒"：name_contexts.from_resolution 要求
    finished 落在 [now-5min, now] 且以 Python 时钟比对（源码现状）；本机环境
    DB 时钟略快于宿主机时钟（约 5s），NOW() 直插会触发 finished > now 被拒，
    回拨 30 秒可吸收时钟偏差并在两种环境（容器 UTC / 宿主机 Asia/Shanghai
    会话时区）下都落在窗口内。
    """
    from src.db.database import get_db_connection

    invocation_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO local_tool_invocations
            (id,tenant_id,user_id,device_id,tool_name,provider_key,arguments_json,state,effect,result_json,finished_at)
            VALUES (%s,%s,'user-1',%s,'weixin_name_resolve','weixin',%s,'succeeded','none',%s,
                    NOW() - INTERVAL '30 seconds')""",
            (
                invocation_id,
                tenant_id,
                device_row["id"],
                json.dumps({"target_name": target_name}),
                json.dumps({"data": {"target_name": target_name, "title_exact": True,
                                     "unique_match": True, "evidence_ref": "dpapi:synthetic_evidence"}}),
            ),
        )
        conn.commit()
    return invocation_id


def _create_name_context_task(tenant_id, device_row):
    """名称定位 → 名称上下文绑定任务（verification_status=resolved）。"""
    from src.session_tasks.models import TaskDraftCreatePayload

    resolution_id = _resolution_invocation(tenant_id, device_row)
    payload = TaskDraftCreatePayload(
        device_id=str(device_row["id"]), resolution_invocation_id=resolution_id, spec=build_spec()
    )
    created = service.create_draft(tenant_id, "user-1", payload)
    confirmation = service.issue_publish_confirmation(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
    )
    service.publish_task(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
        uuid.UUID(confirmation["confirmation_id"]),
    )
    return created["task_id"]


def _ready_reply_by_name_context(tenant_id, device_row):
    """名称上下文任务 → ready reply（claim 响应含 _runtime_target）。"""
    task_id = _create_name_context_task(tenant_id, device_row)
    claimed = service.claim_task(_device_dict(tenant_id, device_row), "rt-char")
    assert claimed is not None and task_id
    # 现状锁定：名称上下文 claim 下发 _runtime_target（当前登录名路由策略）
    assert claimed["spec"]["_runtime_target"] == {
        "policy": "current_login_name", "target_name": "特征测试-联系人"
    }
    _, decision = _submit_reply(tenant_id, device_row, claimed, {
        "conversation_binding_id": claimed["conversation_binding_id"]
    }, 1, [
        {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
    ])
    model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好，周五14:00可以吗"}, ensure_ascii=False)])
    decisions_mod.run_decision_tick(model_call=model)
    return claimed, decision["decision_id"]


def _materialize_running(tenant_id, device_row, claimed, decision_id):
    """prepare-send → 定向 claim → started；返回 (invocation_id, args, claim_token_hash)。"""
    from src.local_tools import repository
    from src.local_tools.security import generate_claim_token, sha256_hex

    prepared = decisions_mod.prepare_send(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
    )
    assert prepared["invocation_id"]
    token = generate_claim_token()
    token_hash = sha256_hex(token)
    claimed_inv = decisions_mod.claim_session_invocation(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
        uuid.UUID(prepared["invocation_id"]), token_hash, 120, None,
    )
    assert claimed_inv["invocation"] is not None
    repository.mark_started(prepared["invocation_id"], tenant_id, token_hash)
    args = repository.get_invocation(prepared["invocation_id"], tenant_id)["arguments_json"]
    return prepared["invocation_id"], args, token_hash


def _authorize_permit(tenant_id, device_row, invocation_id, args, token_hash):
    from src.local_tools.permits import write_authorize

    return write_authorize(
        tenant_id=tenant_id, device_id=str(device_row["id"]),
        invocation_id=invocation_id, claim_token_hash=token_hash,
        request_id=args["request_id"], target_version=args.get("target_version"),
        payload_hash=args.get("payload_hash"),
    )


def _delivery_of(tenant_id, invocation_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT state, effect, phase FROM desktop_automation_deliveries WHERE tenant_id=%s "
            "AND id=(SELECT delivery_id FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id=%s)",
            (tenant_id, tenant_id, invocation_id),
        )
        return dict(cursor.fetchone())


def _attempt_of(tenant_id, invocation_id):
    from src.desktop_automation import attempts as da_attempts

    return da_attempts.get_attempt_by_invocation(invocation_id, tenant_id)


def _audits(tenant_id, kind):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT detail FROM desktop_automation_audit_events WHERE tenant_id=%s AND kind=%s ORDER BY id",
            (tenant_id, kind),
        )
        return [dict(r)["detail"] for r in cursor.fetchall()]


def _evidence_rows(tenant_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT evidence_ref, attempt_id FROM desktop_automation_evidence WHERE tenant_id=%s",
            (tenant_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


# ---------------------------------------------------------------------------
# 1. 缺省场景全链（scenario_key=weixin.conversation.v1）
# ---------------------------------------------------------------------------


class TestDefaultScenarioChain:
    def test_create_without_scenario_key_defaults_weixin(self, tenant_id, device_row, verified_binding):
        """POST 不带 scenario_key → 任务行落 weixin.conversation.v1（models.py 默认值现状锁定）。"""
        payload = build_draft_payload(verified_binding)
        payload.pop("scenario_key")  # 现状锁定：省略即默认微信场景
        created = service.create_draft(tenant_id, "user-1", _payload_model(payload))
        assert _task_row(tenant_id, created["task_id"])["scenario_key"] == "weixin.conversation.v1"

    def test_patch_scenario_key_is_not_part_of_contract(self, tenant_id, device_row, verified_binding):
        """PATCH draft 契约只有 spec：任务场景归属只来自 create，且无法经 PATCH 改变。

        【B1.2 泛化点】计划 §5.2：envelope 分派后 PATCH 读任务行权威 scenario_key。
        """
        created = service.create_draft(tenant_id, "user-1", _payload_model(build_draft_payload(verified_binding)))
        updated = service.update_draft(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"], build_spec()
        )
        assert updated["status"] == "draft"
        assert _task_row(tenant_id, created["task_id"])["scenario_key"] == "weixin.conversation.v1"

    def test_claim_response_carries_task_scenario_key(self, client, tenant_id, device_row, verified_binding):
        """设备 claim 响应键集快照（CR 阻断 3）：scenario_key 为权威任务场景。

        B1.2 起响应携带 scenario_key（缺省微信链路恒为 weixin.conversation.v1，
        多场景下由任务行权威字段承载）；本用例由 B1.0 的"无 scenario_key"断言
        按计划预期转为新行为断言。
        """
        _device_with_token(tenant_id, device_row, f"tok-{tenant_id}")
        publish_task_helper(tenant_id, verified_binding, build_spec(opening=True))
        resp = client.post(
            "/api/local-tools/runtime/session-tasks/claim",
            json={"runtime_instance_id": "rt-char-1"},
            headers={"Authorization": f"Bearer tok-{tenant_id}", "X-Tenant-Id": tenant_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert set(data.keys()) == {
            "assignment_id", "task_id", "scenario_key", "spec", "spec_revision", "fence",
            "control_epoch", "server_control_seq", "lease_seconds", "input_version_base",
            "fresh_baseline", "conversation_binding_id", "binding_version",
            "account_identity_version",
        }
        assert data["scenario_key"] == "weixin.conversation.v1"
        assert data["fence"] == 1 and data["lease_seconds"] == 60
        assert data["spec"]["goal"].startswith("确认对方")

    def test_renew_stale_fence_conflict_409(self, client, tenant_id, device_row, verified_binding):
        """引用性薄测试：过时 fence 的 renew → 409 STALE_ASSIGNMENT（服务层断言点
        与 test_device_protocol.TestClaimAndLease 相同，此处固化 HTTP 状态码映射）。"""
        _device_with_token(tenant_id, device_row, f"tok-{tenant_id}")
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        resp = client.post(
            f"/api/local-tools/runtime/session-tasks/{claimed['assignment_id']}/renew",
            json={"fence": claimed["fence"] + 1, "control_epoch": claimed["control_epoch"]},
            headers={"Authorization": f"Bearer tok-{tenant_id}", "X-Tenant-Id": tenant_id},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == ERR_STALE_ASSIGNMENT


# ---------------------------------------------------------------------------
# 2. submitted 回执接纳全链（operation_result.py:125 硬编码 + adapters.py:373 精确匹配）
# ---------------------------------------------------------------------------


class TestSubmissionReceiptChain:
    def test_validate_submission_evidence_exact_match(self):
        """adapters.py:373：证据 ref 必须逐字符等于 weixin-submission:{request_id}:1。

        【B1.2 泛化点】硬编码模板 + 序号 1；场景策略将移入描述器。
        """
        from src.desktop_automation.adapters import EvidenceContext
        from src.weixin_conversation.adapters import WeixinConversationAdapter

        adapter = WeixinConversationAdapter()
        kw = {"tenant_id": "t", "scenario_key": "weixin.conversation.v1", "request_id": "req-1"}
        assert adapter.validate_submission_evidence(EvidenceContext(evidence_ref="weixin-submission:req-1:1", **kw))
        for bad in ("weixin-submission:req-1:2", "weixin-submission:req-2:1",
                    "weixin-evidence:req-1:1", "weixin-submission:req-1:1:1", ""):
            assert not adapter.validate_submission_evidence(EvidenceContext(evidence_ref=bad, **kw))
        # 场景不匹配即拒（BOSS 场景现状必拒）
        assert not adapter.validate_submission_evidence(EvidenceContext(
            evidence_ref="weixin-submission:req-1:1", tenant_id="t",
            scenario_key="boss.chat_reply.v1", request_id="req-1"))

    def test_submitted_receipt_accepted_full_chain(self, da_tenant, device_row):
        """真链路：名称上下文任务 → prepare-send → 许可 → applied/submitted + 精确证据 → 接纳。

        现状锁定：delivery state=succeeded 但 phase 保持 submitted（"已提交"不冒充
        verified 送达）；证据落 desktop_automation_evidence；无 evidence_invalid 审计。
        """
        from src.local_tools import operation_result
        from src.weixin_conversation.adapters import WeixinConversationAdapter

        tenant_id = da_tenant
        claimed, decision_id = _ready_reply_by_name_context(tenant_id, device_row)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        # 现状锁定：名称上下文绑定 → 服务端冻结 submission 回执策略（非 provider 输入）
        assert args["receipt_mode"] == "submission" and args["receipt_context"] == "weixin_name"
        permit = _authorize_permit(tenant_id, device_row, invocation_id, args, token_hash)
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"weixin-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["acked"] is True and result["late"] is False
        assert result["state"] == "succeeded" and result["effect"] == "applied"
        delivery = _delivery_of(tenant_id, invocation_id)
        assert delivery == {"state": "succeeded", "effect": "applied", "phase": "submitted"}
        # attempt 保留原始上报值（phase=submitted，不被拔高为 verified）
        attempt = _attempt_of(tenant_id, invocation_id)
        assert attempt["effect"] == "applied" and attempt["phase"] == "submitted"
        # 证据恰好登记一行
        rows = _evidence_rows(tenant_id)
        assert len(rows) == 1 and rows[0]["evidence_ref"] == f"weixin-submission:{args['request_id']}:1"
        assert _audits(tenant_id, "evidence_invalid") == []
        # 业务聚合：submitted 不冒充送达（reply_submitted ≠ reply_delivered）
        verdict = WeixinConversationAdapter().aggregate_result(None, [delivery]).verdict
        assert verdict == "reply_submitted"

    @pytest.mark.parametrize("mutation,expected_reason", [
        ("scenario", "submission_not_authorized"),
        ("receipt_mode", "submission_not_authorized"),
        ("namespace", "adapter_rejected"),
    ])
    def test_submitted_rejections_normalize_unknown(self, da_tenant, device_row, mutation, expected_reason):
        """三类拒绝（operation_result.py:125-131 硬编码现状锁定）：
        - business_ref.scenario_key ≠ weixin.conversation.v1（BOSS 场景现状必拒）
        - arguments.receipt_mode 非 submission（冻结回执策略被篡改）
        - 证据命名空间错误（weixin-evidence:* 在 submitted 分支被适配器精确匹配拒绝）
        共同现状：回执仍 ACK（2xx）+ delivery 归一 unknown 三元组 + evidence_invalid
        审计 + attempt 保留原始上报 + 证据表零登记。
        【B1.2 泛化点】场景名/回执上下文硬编码将移入描述器。
        """
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        tenant_id = da_tenant
        claimed, decision_id = _ready_reply_by_name_context(tenant_id, device_row)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        permit = _authorize_permit(tenant_id, device_row, invocation_id, args, token_hash)
        with get_db_connection() as conn:
            cur = conn.cursor()
            if mutation == "scenario":
                # 现状锁定：场景白名单硬编码在 operation_result，改 business_ref 即拒
                cur.execute(
                    "UPDATE local_tool_invocations SET business_ref = business_ref || %s "
                    "WHERE tenant_id=%s AND id=%s",
                    (json.dumps({"scenario_key": "boss.chat_reply.v1"}), tenant_id, invocation_id),
                )
            elif mutation == "receipt_mode":
                cur.execute(
                    "UPDATE local_tool_invocations SET arguments_json = arguments_json || %s "
                    "WHERE tenant_id=%s AND id=%s",
                    (json.dumps({"receipt_mode": "legacy"}), tenant_id, invocation_id),
                )
            conn.commit()
        evidence_ref = (
            f"weixin-evidence:{args['request_id']}:1" if mutation == "namespace"
            else f"weixin-submission:{args['request_id']}:1"
        )
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=evidence_ref,
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        # 现状：拒绝仅表现为证据失效收敛 unknown，回执本身仍 ACK
        assert result["acked"] is True
        assert result["state"] == "unknown" and result["effect"] == "unknown"
        delivery = _delivery_of(tenant_id, invocation_id)
        assert delivery == {"state": "unknown", "effect": "unknown", "phase": "unknown"}
        attempt = _attempt_of(tenant_id, invocation_id)
        assert attempt["effect"] == "applied" and attempt["phase"] == "submitted"
        invalids = _audits(tenant_id, "evidence_invalid")
        assert len(invalids) == 1 and invalids[0]["reason"] == expected_reason
        assert _evidence_rows(tenant_id) == []


# ---------------------------------------------------------------------------
# 3. evidence-invalid 现状 + 3b. 旧 verified 回执（operation_result.py:384-414）
# ---------------------------------------------------------------------------


class TestVerifiedReceiptBaseline:
    """applied+verified 路径真链路（普通 verified 绑定 → 无 submission 回执策略）。

    已知覆盖差异：test_operation_result.py 用 fake 适配器 harness 覆盖 R27 全分支；
    本类以微信真适配器锁定关键现状点。
    """

    def _materialize(self, tenant_id, device_row, binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, binding)
        claimed_binding = {"conversation_binding_id": claimed["conversation_binding_id"]}
        _, decision = _submit_reply(tenant_id, device_row, claimed, claimed_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好，周五14:00可以吗"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision["decision_id"])
        # 现状锁定：非名称上下文的 verified 绑定不冻结 submission 回执策略
        assert "receipt_mode" not in args and "receipt_context" not in args
        permit = _authorize_permit(tenant_id, device_row, invocation_id, args, token_hash)
        return invocation_id, args, token_hash, permit

    def test_verified_garbage_evidence_ack_but_unknown(self, da_tenant, device_row, verified_binding):
        """现状快照（operation_result.py:384-414）：applied+verified + 非法证据 →
        回执 ACK、attempt 保留 applied/verified 原始值、delivery 归一
        {unknown, unknown, unknown}、审计写 evidence_invalid(reason=adapter_rejected)。
        """
        from src.local_tools import operation_result

        tenant_id = da_tenant
        invocation_id, args, token_hash, permit = self._materialize(tenant_id, device_row, verified_binding)
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="verified",
            evidence_ref="totally-invalid-evidence",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["acked"] is True
        assert result["state"] == "unknown"
        delivery = _delivery_of(tenant_id, invocation_id)
        # delivery 归一化三元组（operation_result.py:413-414）
        assert delivery == {"state": "unknown", "effect": "unknown", "phase": "unknown"}
        attempt = _attempt_of(tenant_id, invocation_id)
        # attempt 保留原始上报（原始 effect/phase 与机器归一化分离存储）
        assert attempt["effect"] == "applied" and attempt["phase"] == "verified"
        invalids = _audits(tenant_id, "evidence_invalid")
        assert len(invalids) == 1 and invalids[0]["reason"] == "adapter_rejected"
        assert _evidence_rows(tenant_id) == []

    def test_verified_weixin_namespace_accepted_and_replay_idempotent(self, da_tenant, device_row, verified_binding):
        """旧 verified 回执现状：weixin-evidence:{request_id}:{seq} 结构匹配即接纳登记
        （adapters.py:401-411 仅做"命名空间+请求绑定"结构校验，不查证据存在性——
        【B1.2 泛化点】宽松匹配现状）；同操作重复回执经 evidence 表 ON CONFLICT
        仲裁幂等放行（late ACK，不重复登记）。
        """
        from src.local_tools import operation_result

        tenant_id = da_tenant
        invocation_id, args, token_hash, permit = self._materialize(tenant_id, device_row, verified_binding)
        ref = f"weixin-evidence:{args['request_id']}:1"
        first = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="verified",
            evidence_ref=ref,
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert first["acked"] is True and first["late"] is False
        assert first["state"] == "succeeded"
        assert _delivery_of(tenant_id, invocation_id) == {
            "state": "succeeded", "effect": "applied", "phase": "verified"
        }
        rows = _evidence_rows(tenant_id)
        assert len(rows) == 1 and rows[0]["evidence_ref"] == ref
        # 重复回执（结果 outbox 重投）：迟到 ACK，不改判、不重复登记
        replay = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="verified",
            evidence_ref=ref,
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert replay["acked"] is True and replay["late"] is True
        assert _evidence_rows(tenant_id) == rows


# ---------------------------------------------------------------------------
# 4. spec 非法错误三类快照（HTTP 状态码 + pydantic loc 路径）
# ---------------------------------------------------------------------------


class TestSpecInvalidSnapshots:
    """POST /api/session-tasks spec 非法三类：多余字段/缺字段/类型错误。

    pydantic 错误 loc 快照（TaskDraftCreatePayload + TaskSpecPayload extra=forbid）：
    - 多余字段 → loc ("spec", "<字段名>")，extra_forbidden
    - 缺字段   → loc ("spec", "goal")，missing
    - 类型错误 → loc ("spec", "limits", "max_replies")，int_parsing
    统一 400 + code=VALIDATION_FAILED + field_errors[].field 为 ".".join(loc)。
    """

    def _post_with_binding(self, client, tenant_id, binding, mutate):
        spec = build_spec()
        mutate(spec)
        body = build_draft_payload(binding)
        body["spec"] = spec
        key = f"char-{uuid.uuid4().hex[:8]}"
        return client.post("/api/session-tasks", json=body,
                           headers={"Idempotency-Key": key, "X-Tenant-Id": tenant_id})

    @staticmethod
    def _field_errors(resp):
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False and body["code"] == "VALIDATION_FAILED"
        return [(fe["field"], fe["message"]) for fe in body["field_errors"]]

    def test_extra_field_rejected(self, client, tenant_id, verified_binding):
        resp = self._post_with_binding(client, tenant_id, verified_binding,
                                       lambda s: s.update({"unexpected_extra": 1}))
        fields = self._field_errors(resp)
        assert fields[0][0] == "spec.unexpected_extra"
        assert "Extra inputs are not permitted" in fields[0][1]

    def test_missing_goal_rejected(self, client, tenant_id, verified_binding):
        resp = self._post_with_binding(client, tenant_id, verified_binding, lambda s: s.pop("goal"))
        fields = self._field_errors(resp)
        assert fields[0][0] == "spec.goal"
        assert "Field required" in fields[0][1]

    def test_type_error_rejected(self, client, tenant_id, verified_binding):
        resp = self._post_with_binding(client, tenant_id, verified_binding,
                                       lambda s: s["limits"].update({"max_replies": "abc"}))
        fields = self._field_errors(resp)
        assert fields[0][0] == "spec.limits.max_replies"
        assert "Input should be a valid integer" in fields[0][1]


# ---------------------------------------------------------------------------
# 5. prepare-send 判别语义（decisions.py:1694-1719；缺口补齐）
#    superseded→200 已由 test_c3_execution.TestPrepareSend 覆盖，不重复。
# ---------------------------------------------------------------------------


class TestPrepareSendSemantics:
    def test_pending_decision_conflict_409(self, da_tenant, device_row, verified_binding):
        """决策非 ready 非 superseded（pending 未决）→ 409 CONFLICT（现状：决策尚未
        经模型物化，prepare-send 不排队、直接冲突拒绝）。"""
        tenant_id = da_tenant
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
                uuid.UUID(decision["decision_id"])
            )
        assert exc.value.code == "CONFLICT" and exc.value.status_code == 409

    def test_paused_task_conflict_409(self, da_tenant, device_row, verified_binding):
        """任务非 active（暂停）→ 409 CONFLICT。现状：prepare-send 校验链不查
        control_epoch（fence 不变即可达任务状态检查），暂停后旧 fence 仍进到
        status!=active 分支被拒。【B1.2 泛化点】Phase A 门禁将绑定 binding 行锁。
        """
        tenant_id = da_tenant
        published, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "pause", published["version"])
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
                uuid.UUID(decision["decision_id"])
            )
        assert exc.value.code == "CONFLICT" and exc.value.status_code == 409
        assert "任务状态不可发送" in str(exc.value)

    def test_expired_lease_conflict_409(self, da_tenant, device_row, verified_binding):
        """租约过期 → 409 LEASE_EXPIRED（prepare-send 不自动重领）。

        过期时刻用宿主机 Python 时钟回拨 5 分钟写入：prepare_send 的过期判定
        是「DB 存储租约时刻 ≤ 进程内 _now()（宿主机时钟）」。本环境 DB 时钟快
        约 5s（见 _resolution_invocation 注释），conftest 的 DB CURRENT_TIMESTAMP
        回拨 5s 恰落在判定边缘（实测 flaky），故此处绕开该 helper 直写。
        """
        from datetime import datetime, timedelta, timezone

        from src.db.database import get_db_connection

        tenant_id = da_tenant
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_assignments SET lease_expires_at = %s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(minutes=5), tenant_id, claimed["assignment_id"]),
            )
            conn.commit()
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
                uuid.UUID(decision["decision_id"])
            )
        assert exc.value.code == ERR_LEASE_EXPIRED and exc.value.status_code == 409

    def test_spec_revision_mismatch_returns_superseded_200(self, da_tenant, device_row, verified_binding):
        """决策 ready 但 spec_revision 失配 → 200 {"invocation_id": None, "decision_status":
        "superseded"}（decisions.py:1717-1719；与批次 supersede 同一 200 判别语义）。
        """
        tenant_id = da_tenant
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_decisions SET spec_revision = spec_revision + 1 "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, decision["decision_id"]),
            )
            conn.commit()
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(decision["decision_id"])
        )
        # CR 阻断 12：三分支统一 status 词汇（superseded 分支新增 status 字段，
        # 旧 Runtime 忽略新增字段）
        assert result == {
            "status": "superseded", "invocation_id": None, "decision_status": "superseded"
        }


# ---------------------------------------------------------------------------
# 6. 能力缺失拒绝（REQUIRED_DEVICE_CAPABILITIES 三能力逐项）
# ---------------------------------------------------------------------------


class TestCapabilityMissingRejection:
    @pytest.mark.parametrize("missing", ["session_task_v1", "session_observer_v1", "weixin_message_send_v2"])
    def test_claim_rejected_when_any_capability_missing(self, tenant_id, device_row, verified_binding, missing):
        """现状锁定：session_task_v1/session_observer_v1/weixin_message_send_v2 任一缺失
        → claim 不分配（409 CAPABILITY_MISSING）。weixin_message_send_v2 是
        constants.py:48 REQUIRED 三元组之一（发布/领取同检查）。
        引用关系：test_device_protocol.test_claim_missing_capability_rejected 覆盖
        providers=[]（全缺）汇总路径；此处逐项锁定三元组完备性。"""
        from src.db.database import get_db_connection

        publish_task_helper(tenant_id, verified_binding)
        caps = ["session_task_v1", "session_observer_v1", "weixin_message_send_v2"]
        caps.remove(missing)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE local_tool_devices SET capabilities_json = %s WHERE tenant_id=%s AND id=%s",
                (json.dumps({"providers": ["weixin"], "capabilities": caps}), tenant_id, device_row["id"]),
            )
            conn.commit()
        with pytest.raises(SessionTaskError) as exc:
            service.claim_task(_device_dict(tenant_id, device_row), "rt-char")
        assert exc.value.code == ERR_CAPABILITY_MISSING and exc.value.status_code == 409
        assert missing in str(exc.value)


# ---------------------------------------------------------------------------
# 7. 模型输出 action 分派现状（decisions.py:1319-1372 / prompts ACTIONS）
# ---------------------------------------------------------------------------


class TestActionDispatchVocabulary:
    def test_allowed_action_vocabulary_locked(self):
        """现状锁定：微信场景允许的模型动作词汇恰好为 {reply, wait, handoff, done}
        （prompts.ACTIONS；decisions 分派分支一一对应，无第五种）。
        【B1.2 泛化点】BOSS 钩子复用同一词汇，由描述器决策钩子归一化。"""
        from src.weixin_conversation import prompts

        assert set(prompts.ACTIONS) == {"reply", "wait", "handoff", "done"}
        spec = {"goal": "g", "completion_rule": {"mode": "judged", "criteria": ["对方明确同意沟通"]}}
        peer_ids = ["m1"]
        for action, content in [
            ("reply", json.dumps({"action": "reply", "reply_text": "您好"}, ensure_ascii=False)),
            ("wait", json.dumps({"action": "wait", "wait_for": "peer"}, ensure_ascii=False)),
            ("handoff", json.dumps({"action": "handoff", "reason_code": "peer_request_human"}, ensure_ascii=False)),
            ("done", json.dumps({"action": "done", "criterion_results": [
                {"criterion": "对方明确同意沟通", "message_ids": ["m1"], "quotes": ["在吗"], "reason": "明确同意"},
            ]}, ensure_ascii=False)),
        ]:
            validated = prompts.validate_decision_output(spec, content, peer_ids, "reply")
            assert validated["action"] == action
        # 词汇外动作在入口即拒（分派层不可达）
        with pytest.raises(Exception, match="action 必须是"):
            prompts.validate_decision_output(
                spec, json.dumps({"action": "pause"}, ensure_ascii=False), peer_ids, "reply"
            )

    def test_model_handoff_transitions_task_human_required(self, da_tenant, device_row, verified_binding):
        """handoff 分派真链路（decisions.py:1334-1342）：决策落库 ready/handoff 与任务
        迁移 human_required 同事务原子；迁移原因 model_handoff:{reason_code}。"""
        tenant_id = da_tenant
        published, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps(
            {"action": "handoff", "reason_code": "peer_request_human"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        # 决策：ready + handoff（decision_status 是 ready，不是 failed）
        assert row["status"] == "ready" and row["action"] == "handoff"
        task = _task_row(tenant_id, published["task_id"])
        # 任务：转人工（human_required；completion_reason 仅 completed/stopped 记录）
        assert task["status"] == "human_required"

    def test_reply_action_freezes_reply_text(self, da_tenant, device_row, verified_binding):
        """引用性薄测试：reply 冻结 reply_text（断言点与
        test_c3_decisions.TestReplyDecision 相同——锁定分派入口输出契约）。"""
        tenant_id = da_tenant
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "冻结文本"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "ready" and row["action"] == "reply"
        assert row["reply_text_hash"]  # 正文经加密文本表冻结，行上保留 hash


# ---------------------------------------------------------------------------
# 8. execution_links 唯一约束（init_tables.py UNIQUE(tenant_id, decision_id)）
# ---------------------------------------------------------------------------


class TestExecutionLinksUniqueness:
    def test_duplicate_decision_link_violates_unique_constraint(self, da_tenant, device_row, verified_binding):
        """引用性薄测试：服务层幂等（同 decision 二次物化返回原 invocation）已由
        test_c3_execution.test_materializes_single_execution_unit_idempotently 覆盖；
        此处锁定 DB 层 UNIQUE(tenant_id, decision_id)（init_tables.py:245）兜底——
        绕过服务层直接插入冲突行必须违反约束。"""
        import psycopg2

        from src.db.database import get_db_connection

        tenant_id = da_tenant
        published, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        decision_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO session_task_execution_links (tenant_id, task_id, decision_id) VALUES (%s, %s, %s)",
                (tenant_id, claimed["task_id"], decision_id),
            )
            conn.commit()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cursor.execute(
                    "INSERT INTO session_task_execution_links (tenant_id, task_id, decision_id) VALUES (%s, %s, %s)",
                    (tenant_id, claimed["task_id"], decision_id),
                )
