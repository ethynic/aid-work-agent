"""P4-B 事件源管理 + 签名 webhook 接纳测试（R57/R58 事件矩阵）

覆盖：源 CRUD（internal/webhook、密钥生成与掩码、不回旧密钥）、key_id+版本轮换
（旧新并行短窗）、HMAC 验签（正确/无签名/错签名/未知 key_id）、时间戳 ±5min、
nonce 防重放、大 payload 413、租户伪造（identity_field_forbidden）、事件类型
白名单、限流 429、重复有效事件返回原接纳结果不重执行、condition 白名单 DSL。
"""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.weixin_marketing import event_sources as wxm_sources

pytestmark = pytest.mark.unit


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _create_webhook_source(tenant_id, *, allowed=None, source_ref=None):
    return wxm_sources.create_event_source(
        tenant_id=tenant_id, user_id="owner-1",
        source_ref=source_ref or f"hook-{uuid.uuid4().hex[:8]}",
        source_type="webhook", allowed_event_types=allowed,
    )


def _post(tenant_id, source, body: dict, *, signature=None, timestamp=None,
          nonce=None, key_id=None, secret=None, now=None, limiter=None,
          max_body_bytes=None) -> dict:
    """构造签名并调 accept_webhook_event；返回结果 dict 或 WebhookRejectError 属性"""
    now = now or utcnow()
    ts = timestamp if timestamp is not None else str(int(now.timestamp()))
    nc = nonce if nonce is not None else f"n-{uuid.uuid4().hex[:12]}"
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    use_secret = secret if secret is not None else source["secret"]
    sig = signature if signature is not None else wxm_sources.compute_webhook_signature(
        use_secret, ts, nc, raw
    )
    try:
        return wxm_sources.accept_webhook_event(
            tenant_id=tenant_id, source_id=source["source"]["id"],
            timestamp=ts, nonce=nc, signature=sig, key_id=key_id,
            body=raw, now=now,
            max_body_bytes=max_body_bytes or 1024 * 1024,
            rate_limiter=limiter,
        )
    except wxm_sources.WebhookRejectError as e:
        return {"__rejected__": True, "status": e.status_code, "reason": e.reason}


def _rejected(result) -> bool:
    return bool(result.get("__rejected__"))


class TestEventSourceCrud:
    def test_create_internal_source_no_secret(self, tenant_id):
        result = wxm_sources.create_event_source(
            tenant_id=tenant_id, user_id="owner-1",
            source_ref="internal-1", source_type="internal",
            allowed_event_types=["a.b"],
        )
        assert result["secret"] is None and result["key_id"] is None
        view = result["source"]
        assert view["source_type"] == "internal"
        assert view["status"] == "active"
        assert view["allowed_event_types"] == ["a.b"]
        assert "secret" not in view and "keys" not in [k for k in view if k != "keys"]

    def test_create_webhook_source_secret_once(self, tenant_id):
        result = _create_webhook_source(tenant_id)
        assert result["secret"] and result["key_id"]
        view = wxm_sources.get_event_source_view(
            result["source"]["id"], tenant_id
        )
        # 凭据掩码：视图绝不回明文/密文，只回 key 元数据
        assert "secret" not in json.dumps(view, default=str)
        assert view["webhook_url"].endswith(f"/webhooks/{view['id']}")
        assert view["keys"][0]["key_id"] == result["key_id"]
        assert view["keys"][0]["status"] == "active"

    def test_cross_tenant_source_hidden(self, tenant_id):
        result = _create_webhook_source(tenant_id)
        other = wxm_sources.get_event_source_view(result["source"]["id"], "other-tenant")
        assert other is None

    def test_duplicate_source_ref_rejected_no_mutation(self, tenant_id):
        """P1-1 复审：同 ref 重复创建 → 冲突异常，原行/密钥零变化（纯 INSERT）"""
        ref = f"hook-{uuid.uuid4().hex[:6]}"
        r1 = _create_webhook_source(tenant_id, source_ref=ref)
        original_view = wxm_sources.get_event_source_view(r1["source"]["id"], tenant_id)
        with pytest.raises(wxm_sources.EventSourceRefConflictError):
            _create_webhook_source(tenant_id, source_ref=ref)
        # 原行零变化：id/type/密钥版本不变，且未新增任何 key 行
        after = wxm_sources.get_event_source_view(r1["source"]["id"], tenant_id)
        assert after == original_view
        items, _ = wxm_sources.list_event_sources(tenant_id)
        assert len([i for i in items if i["source_ref"] == ref]) == 1

    def test_create_webhook_over_internal_ref_rejected(self, tenant_id):
        """P1-1 复审（复审复现路径）：B 用 A 的 internal 源 ref 创建 webhook →
        冲突拒绝，原 internal 源行/密钥零变化（创建不承担修改语义）"""
        ref = f"int-{uuid.uuid4().hex[:6]}"
        wxm_sources.create_event_source(
            tenant_id=tenant_id, user_id="user-A", source_ref=ref,
            source_type="internal", allowed_event_types=["a.b"],
        )
        with pytest.raises(wxm_sources.EventSourceRefConflictError):
            wxm_sources.create_event_source(
                tenant_id=tenant_id, user_id="user-B", source_ref=ref,
                source_type="webhook",
            )
        view = _get_view_by_ref(tenant_id, ref)
        assert view["source_type"] == "internal"
        assert view["keys"] == []  # internal 源无密钥行；webhook 创建未注入
        assert view["allowed_event_types"] == ["a.b"]

    def test_invalid_source_type_rejected(self, tenant_id):
        with pytest.raises(ValueError):
            wxm_sources.create_event_source(
                tenant_id=tenant_id, user_id="owner-1",
                source_ref="x", source_type="mqtt",
            )


class TestRotateKey:
    def test_rotate_old_key_parallel_window(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        rotated = wxm_sources.rotate_key(
            tenant_id=tenant_id, source_id=source["source"]["id"], user_id="owner-1",
            rotate_window_seconds=900,
        )
        assert rotated["key_id"] != source["key_id"]
        assert rotated["key_version"] == 2
        body = {"event_id": f"ev-{uuid.uuid4().hex[:8]}", "event_type": "x.y"}
        # 旧 key 在并行窗内仍可验签
        r_old = _post(tenant_id, source, body, secret=source["secret"],
                      nonce="n-old-1", key_id=source["key_id"])
        assert not _rejected(r_old) and r_old["accepted"]
        # 新 key 可验签
        r_new = _post(tenant_id, source, body, secret=rotated["secret"],
                      nonce="n-new-1", key_id=rotated["key_id"])
        assert not _rejected(r_new) and r_new["duplicate"] is True
        view = wxm_sources.get_event_source_view(source["source"]["id"], tenant_id)
        statuses = {k["key_id"]: k["status"] for k in view["keys"]}
        assert statuses[source["key_id"]] == "retiring"
        assert statuses[rotated["key_id"]] == "active"

    def test_old_key_fails_after_window(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        rotated = wxm_sources.rotate_key(
            tenant_id=tenant_id, source_id=source["source"]["id"], user_id="owner-1",
            rotate_window_seconds=1,
        )
        body = {"event_id": f"ev-{uuid.uuid4().hex[:8]}", "event_type": "x.y"}
        # 窗口已过（now 推进 2s）：旧 key 失效，新 key 有效
        later = utcnow() + timedelta(seconds=2)
        r_old = _post(tenant_id, source, body, secret=source["secret"],
                      nonce="n-old-2", key_id=source["key_id"], now=later)
        assert _rejected(r_old) and r_old["status"] == 401
        r_new = _post(tenant_id, source, body, secret=rotated["secret"],
                      nonce="n-new-2", key_id=rotated["key_id"], now=later)
        assert not _rejected(r_new)

    def test_rotate_unknown_key_id_rejected(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        with pytest.raises(KeyError):
            wxm_sources.rotate_key(
                tenant_id=tenant_id, source_id=str(uuid.uuid4()), user_id="owner-1"
            )

    def test_rotate_internal_source_conflict(self, tenant_id):
        wxm_sources.create_event_source(
            tenant_id=tenant_id, user_id="owner-1",
            source_ref="int-1", source_type="internal",
        )
        sources, _ = wxm_sources.list_event_sources(tenant_id)
        internal_id = next(s["id"] for s in sources if s["source_type"] == "internal")
        with pytest.raises(ValueError):
            wxm_sources.rotate_key(
                tenant_id=tenant_id, source_id=internal_id, user_id="owner-1"
            )


class TestWebhookSignature:
    def test_valid_signature_accepted(self, tenant_id):
        source = _create_webhook_source(tenant_id, allowed=["order.completed"])
        result = _post(tenant_id, source, {
            "event_id": "ev-1", "event_type": "order.completed", "amount": 3,
        })
        assert result["accepted"] is True and result["duplicate"] is False
        assert len(result["eligible"]) >= 0

    def test_missing_signature_rejected_401(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        r = _post(tenant_id, source, {"event_id": "ev-2"}, signature="")
        assert _rejected(r) and r["status"] == 401 and r["reason"] == "signature_invalid"

    def test_wrong_signature_rejected_401(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        r = _post(tenant_id, source, {"event_id": "ev-3"}, signature="deadbeef" * 8)
        assert _rejected(r) and r["status"] == 401

    def test_unknown_key_id_rejected_401(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        r = _post(tenant_id, source, {"event_id": "ev-4"}, key_id="wk-nonexistent")
        assert _rejected(r) and r["status"] == 401

    def test_expired_timestamp_rejected_403(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        now = utcnow()
        r = _post(tenant_id, source, {"event_id": "ev-5"}, timestamp=str(
            int((now - timedelta(seconds=301)).timestamp()
                )), now=now)
        assert _rejected(r) and r["status"] == 403
        assert r["reason"] == "timestamp_out_of_window"
        # 恰好在 ±5min 边界内（300s）通过
        r_edge = _post(tenant_id, source, {"event_id": "ev-5b"}, timestamp=str(
            int((now - timedelta(seconds=299)).timestamp())), now=now)
        assert not _rejected(r_edge)

    def test_future_timestamp_rejected_403(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        now = utcnow()
        r = _post(tenant_id, source, {"event_id": "ev-6"}, timestamp=str(
            int((now + timedelta(seconds=600)).timestamp())), now=now)
        assert _rejected(r) and r["status"] == 403

    def test_signature_binds_body(self, tenant_id):
        """签名对 body 绑定：改 body 后旧签名失效（防移植签名）"""
        source = _create_webhook_source(tenant_id)
        now = utcnow()
        ts = str(int(now.timestamp()))
        nc = "n-bind-1"
        raw1 = json.dumps({"event_id": "ev-a", "amount": 1}).encode()
        raw2 = json.dumps({"event_id": "ev-a", "amount": 2}).encode()
        sig1 = wxm_sources.compute_webhook_signature(source["secret"], ts, nc, raw1)
        with pytest.raises(wxm_sources.WebhookRejectError) as excinfo:
            wxm_sources.accept_webhook_event(
                tenant_id=tenant_id, source_id=source["source"]["id"],
                timestamp=ts, nonce=nc, signature=sig1, key_id=None,
                body=raw2, now=now, max_body_bytes=1024 * 1024,
            )
        assert excinfo.value.status_code == 401


class TestNonceReplay:
    def test_nonce_replay_rejected_403(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        now = utcnow()
        ts = str(int(now.timestamp()))
        nc = "n-replay-1"
        raw = json.dumps({"event_id": "ev-r1", "event_type": "x.y"}).encode()
        sig = wxm_sources.compute_webhook_signature(source["secret"], ts, nc, raw)
        kwargs = dict(
            tenant_id=tenant_id, source_id=source["source"]["id"],
            timestamp=ts, nonce=nc, signature=sig, key_id=None,
            body=raw, now=now, max_body_bytes=1024 * 1024,
        )
        r1 = wxm_sources.accept_webhook_event(**kwargs)
        assert r1["accepted"] is True
        try:
            wxm_sources.accept_webhook_event(**kwargs)
            rejected = False
        except wxm_sources.WebhookRejectError as e:
            rejected = e.status_code == 403 and e.reason == "nonce_replayed"
        assert rejected, "同 nonce 重放必须 403"

    def test_duplicate_event_different_nonce_returns_original(self, tenant_id):
        """重复有效事件（不同 nonce、同 external_event_id）：返回原接纳结果不重执行"""
        source = _create_webhook_source(tenant_id)
        r1 = _post(tenant_id, source, {"event_id": "ev-dup", "event_type": "x.y"},
                   nonce="n-1")
        r2 = _post(tenant_id, source, {"event_id": "ev-dup", "event_type": "x.y"},
                   nonce="n-2")
        assert r1["accepted"] and r2["accepted"]
        assert r2["duplicate"] is True and r2["event_id"] == r1["event_id"]

    def test_nonce_cleanup_ttl(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        _post(tenant_id, source, {"event_id": "ev-ttl", "event_type": "x.y"},
              nonce="n-ttl-1")
        deleted = wxm_sources.cleanup_expired_nonces(
            now=utcnow() + timedelta(seconds=901)
        )
        assert deleted >= 1


class TestPayloadLimits:
    def test_oversized_body_rejected_413(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        r = _post(tenant_id, source, {"event_id": "ev-big", "pad": "x" * 2048},
                  max_body_bytes=1024)
        assert _rejected(r) and r["status"] == 413

    def test_invalid_json_rejected_422(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        now = utcnow()
        ts = str(int(now.timestamp()))
        nc = "n-badjson"
        raw = b"{not json"
        sig = wxm_sources.compute_webhook_signature(source["secret"], ts, nc, raw)
        try:
            wxm_sources.accept_webhook_event(
                tenant_id=tenant_id, source_id=source["source"]["id"],
                timestamp=ts, nonce=nc, signature=sig, key_id=None, body=raw,
                now=now, max_body_bytes=65536,
            )
            rejected = False
        except wxm_sources.WebhookRejectError as e:
            rejected = e.status_code == 422
        assert rejected

    def test_invalid_event_id_rejected_422(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        r = _post(tenant_id, source, {"event_id": ""})
        assert _rejected(r) and r["status"] == 422

    def test_identity_fields_forbidden(self, tenant_id):
        """租户伪造：payload 自报 tenant_id/user_id 一律 422 拒绝（身份取自受信源）"""
        source = _create_webhook_source(tenant_id)
        r = _post(tenant_id, source, {
            "event_id": "ev-forged", "tenant_id": "other-tenant",
        })
        assert _rejected(r) and r["status"] == 422
        assert r["reason"] == "identity_field_forbidden"
        r2 = _post(tenant_id, source, {
            "event_id": "ev-forged-2", "user_id": "attacker",
        })
        assert _rejected(r2) and r2["reason"] == "identity_field_forbidden"

    def test_event_type_whitelist(self, tenant_id):
        source = _create_webhook_source(tenant_id, allowed=["order.completed"])
        ok = _post(tenant_id, source, {
            "event_id": "ev-ok", "event_type": "order.completed",
        })
        assert not _rejected(ok)
        bad = _post(tenant_id, source, {
            "event_id": "ev-bad", "event_type": "other.type",
        })
        assert _rejected(bad) and bad["status"] == 422
        assert bad["reason"] == "event_type_not_allowed"

    def test_unknown_source_404(self, tenant_id):
        with pytest.raises(wxm_sources.WebhookRejectError) as excinfo:
            wxm_sources.accept_webhook_event(
                tenant_id=tenant_id, source_id=str(uuid.uuid4()),
                timestamp="1", nonce="n", signature="s", key_id=None,
                body=b"{}", now=utcnow(), max_body_bytes=1024,
            )
        assert excinfo.value.status_code == 404


class TestRateLimit:
    def test_burst_limited_429(self, tenant_id):
        source = _create_webhook_source(tenant_id)
        limiter = wxm_sources.WebhookRateLimiter(max_per_minute=3)
        statuses = []
        for i in range(4):
            r = _post(tenant_id, source, {
                "event_id": f"ev-rl-{i}", "event_type": "x.y",
            }, nonce=f"n-rl-{i}", limiter=limiter)
            statuses.append(r.get("status", 202) if _rejected(r) else 202)
        assert statuses[:3] == [202, 202, 202]
        assert statuses[3] == 429

    def test_limiter_memory_fallback(self):
        limiter = wxm_sources.WebhookRateLimiter(max_per_minute=2)
        key = f"src-{uuid.uuid4().hex[:8]}"
        assert limiter.is_allowed(key)
        assert limiter.is_allowed(key)
        assert not limiter.is_allowed(key)
        # 不同源互不影响
        assert limiter.is_allowed(key + "-other")


class TestConditionDsl:
    def test_empty_condition_matches_all(self):
        hit, _ = wxm_sources.evaluate_condition(None, {"a": 1})
        assert hit
        hit, _ = wxm_sources.evaluate_condition("[]", {})
        assert hit

    def test_eq_exists(self):
        cond = json.dumps([{"field": "status", "op": "eq", "value": "completed"}])
        hit, _ = wxm_sources.evaluate_condition(cond, {"status": "completed"})
        assert hit
        hit, reason = wxm_sources.evaluate_condition(cond, {"status": "pending"})
        assert not hit and reason == "condition_not_matched"
        exists = json.dumps([{"field": "tag", "op": "exists"}])
        assert wxm_sources.evaluate_condition(exists, {"tag": "x"})[0]
        assert not wxm_sources.evaluate_condition(exists, {})[0]

    def test_numeric_compare(self):
        gt = json.dumps([{"field": "amount", "op": "gt", "value": 3}])
        assert wxm_sources.evaluate_condition(gt, {"amount": 4})[0]
        assert not wxm_sources.evaluate_condition(gt, {"amount": "not-a-number"})[0]

    def test_invalid_condition_json_conservative(self):
        hit, reason = wxm_sources.evaluate_condition("{broken", {"a": 1})
        assert not hit and reason == "condition_invalid"
        hit, reason = wxm_sources.evaluate_condition('{"field": "a"}', {"a": 1})
        assert not hit and reason == "condition_invalid"


class TestTriggerCompileCondition:
    def test_event_trigger_condition_frozen(self, wx_config):
        from src.weixin_marketing.models import parse_trigger
        from src.weixin_marketing.triggers import compile_trigger_specs

        trigger = parse_trigger({
            "type": "event", "source_ref": "s-1", "event_type": "order.completed",
            "delay_seconds": 30,
            "condition": [{"field": "status", "op": "eq", "value": "completed"}],
        })
        specs = compile_trigger_specs(trigger, config=wx_config)
        assert specs[0]["kind"] == "event"
        assert specs[0]["condition_ref"] is not None
        parsed = json.loads(specs[0]["condition_ref"])
        assert parsed[0]["field"] == "status"

    def test_event_trigger_without_condition(self, wx_config):
        from src.weixin_marketing.models import parse_trigger
        from src.weixin_marketing.triggers import compile_trigger_specs

        trigger = parse_trigger({"type": "event", "source_ref": "s-2"})
        specs = compile_trigger_specs(trigger, config=wx_config)
        assert specs[0]["condition_ref"] is None
        assert specs[0]["event_type"] == "*"

def _get_view_by_ref(tenant_id, ref):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM desktop_automation_event_sources "
            "WHERE tenant_id = %s AND source_ref = %s",
            (tenant_id, ref),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return wxm_sources.get_event_source_view(str(row["id"]), tenant_id)
