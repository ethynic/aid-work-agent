"""weixin_marketing P3-A1 工作台 API 集成测试（真实 DB + TestClient，不可用时 skip）

覆盖（R54① 六组端点）：
- test-send：显式 block+绑定只试发该条——独立 run/attempt 审计（:test task_ref，
  不进生产 runs 列表）、独立配额 scope（wxm:test:* 桶落账、生产 wxm: 桶零行）、
  全链路（claim→write-authorize→operation-result verified→run succeeded）；
  无绑定/不可用绑定/非法 position/图片块 422、未发布 409、跨租户 404、幂等三态；
- group-searches：POST 202 异步入队 + GET 轮询（fake 设备行为驱动 local_tool 队列：
  claim/start/result 模拟工具回包，读操作走 write_result 旧 /result 语义）、
  候选带 target_ref、跨租户 404、幂等三态抽查；
- group-bindings：从候选创建（pending）；verify 状态机——唯一精确命中→complete、
  同名多命中→rejected（候选冲突）、设备离线→409 VERIFY_FAILED 且绑定保持 pending
  可重试；P3 复审 P1-2 完整性门槛——无标志且条数≥limit / 显式截断标志 → 409
  VERIFY_FAILED 保持 pending（不判 complete/rejected）；证据引用 identity_evidence_ref；
- devices：属主列表 + 在线状态 + capabilities 的 weixin 可用性；preflight 经队列
  返回环境矩阵（幂等键）、设备离线 409 PREFLIGHT_FAILED；
- 错误矩阵：401/404（跨租户统一）/409/422/429 抽查；
- 测试后物理清理并核实零残留。
"""

import json
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PREFIX = "/api/weixin-marketing"

ALL_CLEANUP_TABLES = (
    # 底座表（依赖序，照 tests/unit/desktop_automation/conftest.py）
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_deliveries",
    "desktop_automation_runs",
    "desktop_automation_occurrences",
    "desktop_automation_outbox",
    "desktop_automation_events",
    "desktop_automation_event_sources",
    "desktop_automation_schedules",
    "desktop_automation_subjects",
    "desktop_automation_audit_events",
    "desktop_automation_quota_buckets",
    "local_tool_operation_permits",
    "local_tool_events",
    "local_tool_invocations",
    "local_tool_devices",
    # weixin 业务表
    "bs_weixin_marketing_audit_events",
    "bs_weixin_marketing_content_blocks",
    "bs_weixin_marketing_revisions",
    "bs_weixin_marketing_automations",
    "bs_weixin_marketing_group_bindings",
    "bs_weixin_marketing_account_bindings",
    "bs_weixin_marketing_assets",
    # API 层幂等键表
    "weixin_marketing_idempotency_keys",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def _count(table: str, tenant_id: str, where: str = "", params: tuple = ()) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s {where}",
            (tenant_id, *params),
        )
        return int(cur.fetchone()["c"])


def _create_binding(tenant_id: str, user_id: str, *, device_id: str, state: str = "complete") -> str:
    """直建 complete 群绑定（绑定创建链路本身是本文件被测对象，fixture 直落目标态）"""
    from src.db.database import get_db_connection

    group_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_group_bindings
                (id, tenant_id, user_id, device_id, account_binding_id, label,
                 identity_evidence_ref, identity_version, state, verified_at)
            VALUES (%s, %s, %s, %s, NULL, '集成测试群', 'ev-it', '1', %s, NOW())
            """,
            (group_id, tenant_id, user_id, device_id, state),
        )
        conn.commit()
    return group_id


def _create_device(tenant_id: str, user_id: str, *, weixin: bool = True, seen: bool = True) -> str:
    from src.db.database import get_db_connection
    from psycopg2.extras import Json

    device_id = str(uuid.uuid4())
    capabilities = (
        {
            "providers": ["weixin"],
            "protocol_version": 2,
            "provider_manifests": {
                "weixin": {"provider_id": "ai.aidwork.weixin", "protocol_version": 1}
            },
        }
        if weixin
        else {"providers": ["boss-recruiting"]}
    )
    last_seen = datetime.now() if seen else datetime.now() - timedelta(hours=1)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO local_tool_devices
                (id, tenant_id, user_id, name, platform, token_hash, capabilities_json,
                 selected, status, last_seen_at)
            VALUES (%s, %s, %s, %s, 'win32', %s, %s, FALSE, 'active', %s)
            """,
            (
                device_id, tenant_id, user_id, "测试设备", f"tok-{uuid.uuid4().hex}",
                Json(capabilities), last_seen,
            ),
        )
        conn.commit()
    return device_id


def _create_payload(group_binding_id: str, *, blocks=None, name="P3 集成测试"):
    if blocks is None:
        blocks = [
            {"type": "text", "text_content": "P3 第一条内容"},
            {"type": "link", "url": "https://example.com/p3"},
        ]
    return {
        "name": name,
        "trigger": {
            "type": "once",
            "run_at": (utcnow() + timedelta(hours=2)).isoformat(),
            "timezone": "UTC",
        },
        "blocks": blocks,
        "group_binding_id": group_binding_id,
    }


# ==================== fixtures ====================


@pytest.fixture(scope="module")
def wxm_adapter():
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.weixin_marketing.adapters import WeixinFixedContentAdapter
    from src.weixin_marketing.config import WeixinQuotaConfig, get_weixin_marketing_config

    config = replace(
        get_weixin_marketing_config(),
        enabled=True,
        time_triggers_enabled=True,
        max_blocks=20,
        min_interval_seconds=300,
        quotas=WeixinQuotaConfig(window_seconds=3600, tenant_limit=100, task_limit=30,
                                 target_limit=10, account_limit=60),
    )
    instance = WeixinFixedContentAdapter(config=config)
    TrustedAdapterRegistry.register(instance)
    yield instance
    TrustedAdapterRegistry.unregister(instance.scenario_key)


@pytest.fixture(scope="module")
def client():
    from src.main import app
    from src.weixin_marketing import api as wxm_api

    if not any(getattr(r, "path", "").startswith(PREFIX) for r in app.routes):
        app.include_router(wxm_api.router)
    return TestClient(app)


@pytest.fixture(scope="module")
def tenants():
    from src.saas.db.tenant_db import TenantDB

    created = []
    for _ in range(2):
        code = f"P{uuid.uuid4().hex[:6].upper()}"
        tenant = TenantDB.create(
            company_name=f"微信P3测试-{code}",
            tenant_code=code,
            contact_name="测试",
            contact_phone="13800000000",
        )
        if not tenant:
            pytest.skip("无法创建测试租户（DB 不可用）")
        created.append(tenant["tenant_id"])

    yield created

    from src.db.database import get_db_connection

    problems = []
    for tenant_id in created:
        TenantDB.delete(tenant_id)
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                for table in ALL_CLEANUP_TABLES:
                    cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
                cur.execute(
                    "DELETE FROM tokens WHERE user_id IN "
                    "(SELECT user_id FROM users WHERE tenant_id = %s)",
                    (tenant_id,),
                )
                cur.execute("DELETE FROM users WHERE tenant_id = %s", (tenant_id,))
                cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
                conn.commit()
                for table in (*ALL_CLEANUP_TABLES, "users", "tenants"):
                    cur.execute(
                        f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s",
                        (tenant_id,),
                    )
                    if int(cur.fetchone()["c"]):
                        problems.append(f"{table} tenant={tenant_id} 残留")
        except Exception as e:  # noqa: BLE001
            problems.append(f"tenant={tenant_id} 清理异常: {e}")
    assert not problems, f"测试数据残留: {problems}"


@pytest.fixture(scope="module")
def users(tenants):
    from src.api.auth import generate_token
    from src.db.models import UserDB

    result = []
    for i, tenant_id in enumerate(tenants):
        phone = f"198{uuid.uuid4().int % 10**8:08d}"
        user = UserDB.create(phone=phone, username=f"微信P3测试用户{i}", tenant_id=tenant_id)
        if not user:
            pytest.skip("无法创建测试用户（DB 不可用）")
        token = generate_token(user["user_id"])
        result.append({"user_id": user["user_id"], "tenant_id": tenant_id, "token": token})
    return result


# ==================== fake 设备行为（驱动 local_tool 队列）====================


class FakeDeviceWorker:
    """模拟 Runtime 设备行为：claim → started → 按工具回包。

    领取路径与生产 /runtime/claim 同构：claim_next 带 catalog 解析的 provider_keys
    过滤（设备 capabilities → 受信 provider 集合），并复刻 claim 端点的
    is_tool_allowed 门控（不在受信清单内的工具直接 TOOL_NOT_ALLOWED 落终态）。

    - weixin_chat_search：按 keyword 返回配置的候选（search_results[keyword]）；
      值可为 list（旧行为：自动带 truncated=limit<len 标志）或 dict（P1-2 完整性
      门槛用：精确控制 items 与可选显式 truncated/complete 标志，缺省键即无标志）；
    - weixin_probe：返回环境矩阵；
    - weixin_message_send_v2：走完整 v2 写链路（write-authorize 许可 →
      operation-result verified，证据 weixin-evidence:<request_id>:1）。
    """

    def __init__(self, tenant_id: str, device_id: str, *, search_results: dict = None,
                 fail_tools: set = None):
        from src.local_tools import catalog as lt_catalog
        from src.local_tools import repository
        from src.local_tools.security import generate_claim_token, sha256_hex

        self._catalog = lt_catalog
        self._repository = repository
        self._sha256 = sha256_hex
        self._generate_token = generate_claim_token
        self.tenant_id = tenant_id
        self.device_id = device_id
        self.search_results = search_results if search_results is not None else {}
        self.fail_tools = set(fail_tools or ())
        self.handled = []
        self._stop = threading.Event()
        self._thread = None
        self._provider_keys = lt_catalog.get_provider_keys_for_device(
            self._device_capabilities()
        )

    def _device_capabilities(self):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT capabilities_json FROM local_tool_devices "
                "WHERE id = %s AND tenant_id = %s",
                (self.device_id, self.tenant_id),
            )
            row = cur.fetchone()
        return dict(row)["capabilities_json"] if row else None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self):
        while not self._stop.is_set():
            invocation = self._claim_one()
            if invocation is None:
                time.sleep(0.05)
                continue
            try:
                self._handle(invocation)
            except Exception as e:  # noqa: BLE001 记录后继续（不静默吞掉测试线索）
                self.handled.append({"tool": invocation["tool_name"], "error": repr(e)})

    def _claim_one(self):
        """领取（生产同构：catalog provider 过滤——能力不含 weixin 的设备领不到）"""
        claim_token = self._generate_token()
        claimed = self._repository.claim_next(
            self.device_id, self.tenant_id, self._sha256(claim_token), 300,
            self._provider_keys,
        )
        if claimed is None:
            return None
        started = self._repository.mark_started(
            str(claimed["id"]), self.tenant_id, self._sha256(claim_token)
        )
        assert started is not None and started["state"] == "running"
        claimed["_claim_token"] = claim_token
        return claimed

    def _handle(self, invocation):
        from src.local_tools import permits
        from src.local_tools.operation_result import apply_operation_result

        invocation_id = str(invocation["id"])
        claim_hash = self._sha256(invocation["_claim_token"])
        tool = invocation["tool_name"]
        # claim 端点同款门控：受信清单外工具直接失败（不执行）
        invocation_provider = invocation.get("provider_key") or (
            self._provider_keys[0] if self._provider_keys else None
        )
        if not self._catalog.is_tool_allowed(invocation_provider, tool):
            self._repository.write_result(
                invocation_id, self.tenant_id, claim_hash, False,
                "TOOL_NOT_ALLOWED", "工具不在设备 Provider 批准清单内",
            )
            self.handled.append({"tool": tool, "rejected": "TOOL_NOT_ALLOWED"})
            return
        if tool in self.fail_tools:
            # 模拟设备侧工具执行失败（如 UI 变化），invocation 落 failed 终态
            self._repository.write_result(
                invocation_id, self.tenant_id, claim_hash, False,
                "WEIXIN_UI_CHANGED", "模拟工具执行失败",
            )
            self.handled.append({"tool": tool, "failed": "WEIXIN_UI_CHANGED"})
            return
        arguments = invocation.get("arguments_json") or {}
        if tool == "weixin_chat_search":
            keyword = str(arguments.get("keyword") or "")
            spec = self.search_results.get(keyword, [])
            if isinstance(spec, dict):
                # dict 形态：精确控制回包（items + 可选显式 truncated/complete
                # 标志；键缺省即设备未上报该标志）
                data = {"items": spec.get("items", [])}
                for flag in ("truncated", "complete"):
                    if flag in spec:
                        data[flag] = spec[flag]
            else:
                items = spec
                data = {"items": items, "truncated": bool(arguments.get("limit", 50) < len(items))}
            self._repository.write_result(
                invocation_id, self.tenant_id, claim_hash, True, data=data
            )
        elif tool == "weixin_probe":
            self._repository.write_result(
                invocation_id, self.tenant_id, claim_hash, True,
                data={"weixin_version": "4.0.0.42", "dpi": 96, "theme": "light",
                      "platform": "win32"},
            )
        elif tool == "weixin_message_send_v2":
            permit = permits.write_authorize(
                tenant_id=self.tenant_id,
                device_id=self.device_id,
                invocation_id=invocation_id,
                claim_token_hash=claim_hash,
                request_id=str(arguments.get("request_id")),
                target_version=arguments.get("target_version"),
                payload_hash=arguments.get("payload_hash"),
            )
            request_id = str(arguments.get("request_id"))
            apply_operation_result(
                tenant_id=self.tenant_id,
                device_id=self.device_id,
                invocation_id=invocation_id,
                claim_token_hash=claim_hash,
                request_id=request_id,
                effect="applied",
                phase="verified",
                evidence_ref=f"weixin-evidence:{request_id}:1",
                permit_id=permit["permit_id"],
                permit_token=permit["permit_token"],
            )
        else:
            self._repository.write_result(
                invocation_id, self.tenant_id, claim_hash, False,
                code="TOOL_NOT_SUPPORTED", message=f"fake device 不支持 {tool}",
            )
        self.handled.append({"tool": tool, "invocation_id": invocation_id})


def _run_with_worker(worker: FakeDeviceWorker, call):
    """启动 fake 设备执行阻塞调用（verify/preflight 在服务内同步等待设备回包）"""
    worker.start()
    try:
        return call()
    finally:
        worker.stop()


def _api_create_automation(client, user, group_binding_id, **kw):
    resp = client.post(
        f"{PREFIX}/automations", headers=_auth(user),
        json=_create_payload(group_binding_id, **kw),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _api_publish(client, user, automation_id: str, version: int = 1):
    resp = client.post(
        f"{PREFIX}/automations/{automation_id}/publish",
        headers=_auth(user), json={"expected_version": version},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ==================== devices ====================


class TestDevices:
    def test_list_devices_owner_scoped_with_weixin_capability(self, client, users):
        user, other = users
        tenant_id = user["tenant_id"]
        wx_device = _create_device(tenant_id, user["user_id"], weixin=True)
        boss_device = _create_device(tenant_id, user["user_id"], weixin=False)
        offline_device = _create_device(tenant_id, user["user_id"], weixin=True, seen=False)
        _create_device(other["tenant_id"], other["user_id"])  # 他人设备不可见

        resp = client.get(f"{PREFIX}/devices", headers=_auth(user))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total"] == 3
        by_id = {item["device_id"]: item for item in data["items"]}
        assert by_id[wx_device]["online"] is True
        assert by_id[wx_device]["weixin"]["available"] is True
        assert by_id[boss_device]["weixin"]["available"] is False
        assert by_id[offline_device]["online"] is False
        # 不泄露原始 capability payload
        assert "provider_manifests" not in json.dumps(data)

    def test_preflight_success_and_idempotency(self, client, users, monkeypatch):
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 10)
        user = users[0]
        tenant_id = user["tenant_id"]
        device_id = _create_device(tenant_id, user["user_id"])
        key = {"Idempotency-Key": f"it-preflight-{uuid.uuid4().hex[:12]}"}

        resp = _run_with_worker(
            FakeDeviceWorker(tenant_id, device_id),
            lambda: client.post(
                f"{PREFIX}/devices/{device_id}/preflight",
                headers={**_auth(user), **key}, json={},
            ),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["device_id"] == device_id
        assert data["environment"]["weixin_version"] == "4.0.0.42"

        # 幂等重放：同 key 同 body → 同 invocation（不重复入队）；同 key 异 body → 409
        replay = client.post(
            f"{PREFIX}/devices/{device_id}/preflight",
            headers={**_auth(user), **key}, json={},
        )
        assert replay.status_code == 200
        assert replay.json()["data"]["invocation_id"] == data["invocation_id"]
        conflict = client.post(
            f"{PREFIX}/devices/{device_id}/preflight",
            headers={**_auth(user), **key}, json={"unexpected": True},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"

        # 跨租户/未知设备 → 404；非法 UUID → 404
        assert client.post(
            f"{PREFIX}/devices/{device_id}/preflight", headers=_auth(users[1]), json={}
        ).status_code == 404
        assert client.post(
            f"{PREFIX}/devices/{uuid.uuid4()}/preflight", headers=_auth(user), json={}
        ).status_code == 404
        assert client.post(
            f"{PREFIX}/devices/not-a-uuid/preflight", headers=_auth(user), json={}
        ).status_code == 404

    def test_preflight_device_offline_409(self, client, users, monkeypatch):
        from src.weixin_marketing import workbench as wxm_workbench

        # 短等待窗口（monotonic 截止 + 快轮询）：无设备响应时 409 是确定性结果，
        # 窗口放宽到 2s 消除慢机抖动（首查前 enqueue 慢不改变结论）
        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 2.0)
        monkeypatch.setattr(wxm_workbench, "READ_OP_POLL_INTERVAL_SECONDS", 0.25)
        user = users[0]
        device_id = _create_device(user["tenant_id"], user["user_id"])
        resp = client.post(
            f"{PREFIX}/devices/{device_id}/preflight", headers=_auth(user), json={}
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "PREFLIGHT_FAILED"
        assert "可重试" in resp.json()["error"]

    def test_preflight_same_key_retry_after_recovery(self, client, users, monkeypatch):
        """V-P1 粘滞修复（preflight 同路径）：离线 409 → 设备恢复 → 同 key 重试
        200；重试重建了新 invocation（非 queued 残留换 :r2 键）"""
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 2.0)
        monkeypatch.setattr(wxm_workbench, "READ_OP_POLL_INTERVAL_SECONDS", 0.25)
        user = users[0]
        tenant_id = user["tenant_id"]
        device_id = _create_device(tenant_id, user["user_id"])
        key = {"Idempotency-Key": f"it-pf-sticky-{uuid.uuid4().hex[:12]}"}

        offline = client.post(
            f"{PREFIX}/devices/{device_id}/preflight",
            headers={**_auth(user), **key}, json={},
        )
        assert offline.status_code == 409
        assert offline.json()["code"] == "PREFLIGHT_FAILED"

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 20)
        worker = FakeDeviceWorker(tenant_id, device_id).start()
        try:
            retried = client.post(
                f"{PREFIX}/devices/{device_id}/preflight",
                headers={**_auth(user), **key}, json={},
            )
            assert retried.status_code == 200, retried.text
        finally:
            worker.stop()
        assert retried.json()["data"]["environment"]["weixin_version"] == "4.0.0.42"
        # 离线一条（超时取消终态）+ 成功一条 = 2（:r2 键重建的直接证据）
        assert _count(
            "local_tool_invocations", tenant_id,
            "AND business_kind = 'weixin_device_preflight' AND device_id = %s",
            (device_id,),
        ) == 2


# ==================== group-searches ====================


class TestGroupSearches:
    def test_async_search_lifecycle(self, client, users):
        user = users[0]
        tenant_id = user["tenant_id"]
        device_id = _create_device(tenant_id, user["user_id"])
        key = {"Idempotency-Key": f"it-search-{uuid.uuid4().hex[:12]}"}
        body = {"device_id": device_id, "keyword": "哈尼测试群"}

        # 无设备响应时先创建（POST 只入队不等待）——先起 fake 设备再发请求更贴近真实
        worker = FakeDeviceWorker(
            tenant_id, device_id,
            search_results={"哈尼测试群": [
                {"title": "哈尼测试群", "member_count": 3, "target_ref": "wxg:aaa"},
                {"title": "哈尼测试群（备份）", "member_count": 5, "target_ref": "wxg:bbb"},
            ]},
        ).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-searches", headers={**_auth(user), **key}, json=body
            )
            assert resp.status_code == 202, resp.text
            search_id = resp.json()["data"]["search_id"]

            # GET 轮询至 succeeded（fake 设备秒级回包）
            deadline = time.monotonic() + 10
            data = None
            while time.monotonic() < deadline:
                got = client.get(f"{PREFIX}/group-searches/{search_id}", headers=_auth(user))
                assert got.status_code == 200, got.text
                data = got.json()["data"]
                if data["status"] == "succeeded":
                    break
                time.sleep(0.1)
            assert data["status"] == "succeeded", data
            assert len(data["items"]) == 2
            assert {i["target_ref"] for i in data["items"]} == {"wxg:aaa", "wxg:bbb"}
            assert data["truncated"] is False
        finally:
            worker.stop()

        # 幂等重放：同 key 同 body → 同 search_id（不重复入队）
        replay = client.post(
            f"{PREFIX}/group-searches", headers={**_auth(user), **key}, json=body
        )
        assert replay.status_code == 202
        assert replay.json()["data"]["search_id"] == search_id
        # 同 key 异 body → 409
        conflict = client.post(
            f"{PREFIX}/group-searches",
            headers={**_auth(user), **key},
            json={"device_id": device_id, "keyword": "别的关键词"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"

        # 审计留痕（keyword 只存 hash）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT details_redacted FROM bs_weixin_marketing_audit_events "
                "WHERE tenant_id = %s AND action = 'group_search_requested'",
                (tenant_id,),
            )
            rows = cur.fetchall()
        assert rows and "哈尼测试群" not in json.dumps([dict(r["details_redacted"]) for r in rows])

    def test_claim_provider_filter_routes_weixin_read_ops(self, client, users):
        """claim 过滤路由（消除「绕过 claim 过滤」盲区）：weixin 只读 invocation
        仅 capabilities 含 weixin 的设备经 catalog provider 过滤可领取；
        boss-only 设备领不到（provider_key 非 NULL 须能力命中）。"""
        from src.local_tools import catalog as lt_catalog
        from src.local_tools import repository as lt_repository
        from src.local_tools.security import generate_claim_token, sha256_hex
        from src.db.database import get_db_connection

        user = users[0]
        tenant_id = user["tenant_id"]
        wx_device = _create_device(tenant_id, user["user_id"], weixin=True)
        boss_device = _create_device(tenant_id, user["user_id"], weixin=False)

        # 入队一条 weixin 搜索（POST 只入队不等待，无需设备响应）
        resp = client.post(
            f"{PREFIX}/group-searches", headers=_auth(user),
            json={"device_id": wx_device, "keyword": "路由验证群"},
        )
        assert resp.status_code == 202, resp.text
        invocation_id = resp.json()["data"]["search_id"]

        def _provider_keys(device_id):
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT capabilities_json FROM local_tool_devices WHERE id = %s",
                    (device_id,),
                )
                caps = dict(cur.fetchone())["capabilities_json"]
            return lt_catalog.get_provider_keys_for_device(caps)

        # boss-only 设备（provider 过滤后无 weixin）领不到
        boss_token = generate_claim_token()
        assert lt_repository.claim_next(
            boss_device, tenant_id, sha256_hex(boss_token), 60,
            _provider_keys(boss_device),
        ) is None
        # weixin 能力设备领到，且工具在受信清单内（claim 端点同款门控）
        wx_token = generate_claim_token()
        claimed = lt_repository.claim_next(
            wx_device, tenant_id, sha256_hex(wx_token), 60, _provider_keys(wx_device)
        )
        assert claimed is not None
        assert str(claimed["id"]) == invocation_id
        assert claimed["tool_name"] == "weixin_chat_search"
        assert claimed["provider_key"] == "weixin"
        assert lt_catalog.is_tool_allowed(claimed["provider_key"], claimed["tool_name"])
        # 收敛：设备侧回包终态（读链路走旧 /result 语义的 write_result）
        row = lt_repository.write_result(
            invocation_id, tenant_id, sha256_hex(wx_token), True,
            data={"items": [], "truncated": False},
        )
        assert row["state"] == "succeeded"

    def test_search_errors(self, client, users):
        user, other = users
        # 未知设备/跨租户设备 → 404
        resp = client.post(
            f"{PREFIX}/group-searches", headers=_auth(user),
            json={"device_id": str(uuid.uuid4()), "keyword": "x"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"
        # 语义错（keyword 空）→ 422
        device_id = _create_device(user["tenant_id"], user["user_id"])
        resp = client.post(
            f"{PREFIX}/group-searches", headers=_auth(user),
            json={"device_id": device_id, "keyword": ""},
        )
        assert resp.status_code == 422
        # GET 未知/非法 UUID/跨租户 → 404
        assert client.get(
            f"{PREFIX}/group-searches/{uuid.uuid4()}", headers=_auth(user)
        ).status_code == 404
        assert client.get(
            f"{PREFIX}/group-searches/not-a-uuid", headers=_auth(user)
        ).status_code == 404
        # 未登录 → 401
        assert client.get(f"{PREFIX}/group-searches/{uuid.uuid4()}").status_code == 401


# ==================== group-bindings ====================


class TestGroupBindings:
    def _search_succeeded(self, client, user, keyword: str, items: list) -> dict:
        tenant_id = user["tenant_id"]
        device_id = _create_device(tenant_id, user["user_id"])
        worker = FakeDeviceWorker(tenant_id, device_id, search_results={keyword: items}).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-searches", headers=_auth(user),
                json={"device_id": device_id, "keyword": keyword},
            )
            assert resp.status_code == 202, resp.text
            search_id = resp.json()["data"]["search_id"]
            deadline = time.monotonic() + 10
            data = None
            while time.monotonic() < deadline:
                got = client.get(f"{PREFIX}/group-searches/{search_id}", headers=_auth(user))
                data = got.json()["data"]
                if data["status"] == "succeeded":
                    return {"device_id": device_id, "search_id": search_id, "data": data}
                time.sleep(0.1)
            raise AssertionError(f"搜索未成功: {data}")
        finally:
            worker.stop()

    def test_create_binding_from_candidate_then_list(self, client, users):
        user = users[0]
        found = self._search_succeeded(client, user, "唯一群", [
            {"title": "唯一群", "member_count": 2, "target_ref": "wxg:unique"},
        ])
        body = {
            "device_id": found["device_id"],
            "label": "唯一群",
            "target_ref": "wxg:unique",
            "search_id": found["search_id"],
        }
        resp = client.post(f"{PREFIX}/group-bindings", headers=_auth(user), json=body)
        assert resp.status_code == 200, resp.text
        binding = resp.json()["data"]
        assert binding["state"] == "pending"  # pending_verification

        # 列表 + 过滤
        resp = client.get(f"{PREFIX}/group-bindings", headers=_auth(user))
        assert resp.status_code == 200
        listed = resp.json()["data"]
        assert listed["total"] == 1
        assert listed["items"][0]["id"] == binding["binding_id"]
        resp = client.get(
            f"{PREFIX}/group-bindings", headers=_auth(user), params={"state": "complete"}
        )
        assert resp.json()["data"]["total"] == 0
        resp = client.get(
            f"{PREFIX}/group-bindings", headers=_auth(user), params={"state": "bogus"}
        )
        assert resp.status_code == 422

        # 候选外 target_ref / 未成功搜索 → 422；未知搜索 → 404；跨租户 → 404
        bad = dict(body, target_ref="wxg:not-in-candidates")
        assert client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user), json=bad
        ).status_code == 422
        assert client.post(
            f"{PREFIX}/group-bindings", headers=_auth(users[1]), json=body
        ).status_code == 404
        assert client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json=dict(body, search_id=str(uuid.uuid4())),
        ).status_code == 404

    def test_verify_unique_match_to_complete(self, client, users, monkeypatch):
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 10)
        user = users[0]
        found = self._search_succeeded(client, user, "核验目标群", [
            {"title": "核验目标群", "member_count": 8, "target_ref": "wxg:verify-ok"},
            {"title": "核验目标群分部", "member_count": 8, "target_ref": "wxg:verify-2"},
        ])
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "核验目标群",
                "target_ref": "wxg:verify-ok", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]

        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"],
            search_results={"核验目标群": [
                {"title": "核验目标群", "member_count": 8, "target_ref": "wxg:verify-ok"},
                {"title": "核验目标群分部", "member_count": 8, "target_ref": "wxg:verify-2"},
            ]},
        ).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 200, resp.text
        finally:
            worker.stop()
        data = resp.json()["data"]
        assert data["result"] == "verified"
        assert data["state"] == "complete"
        assert data["identity_evidence_ref"].startswith("weixin-bind-evidence:")
        assert data["exact_matches"] == 1

        # 已 complete 再核验 → 409
        resp = client.post(
            f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
        )
        assert resp.status_code == 409
        # DB 侧 verified_at/identity_evidence_ref 落位
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state, identity_evidence_ref, verified_at "
                "FROM bs_weixin_marketing_group_bindings WHERE tenant_id = %s AND id = %s",
                (user["tenant_id"], binding_id),
            )
            row = dict(cur.fetchone())
        assert row["state"] == "complete"
        assert row["identity_evidence_ref"] == data["identity_evidence_ref"]
        assert row["verified_at"] is not None

    def test_verify_candidate_conflict_rejected(self, client, users, monkeypatch):
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 10)
        user = users[0]
        found = self._search_succeeded(client, user, "同名群", [
            {"title": "同名群", "member_count": 1, "target_ref": "wxg:c1"},
        ])
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "同名群",
                "target_ref": "wxg:c1", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]

        # 核验时同名多命中 → rejected（候选冲突终态）
        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"],
            search_results={"同名群": [
                {"title": "同名群", "member_count": 1, "target_ref": "wxg:c1"},
                {"title": "同名群", "member_count": 2, "target_ref": "wxg:c2"},
            ]},
        ).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 200, resp.text
        finally:
            worker.stop()
        data = resp.json()["data"]
        assert data["result"] == "rejected"
        assert data["state"] == "rejected"
        assert data["reason"] == "candidate_conflict"
        # rejected 为终态：再核验 → 409（不可重试回 pending）
        resp = client.post(
            f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
        )
        assert resp.status_code == 409

    def test_verify_device_offline_failed_then_retry(self, client, users, monkeypatch):
        from src.weixin_marketing import workbench as wxm_workbench

        # 阶段一（离线）：短等待窗口（2s + 快轮询），409 为确定性结果
        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 2.0)
        monkeypatch.setattr(wxm_workbench, "READ_OP_POLL_INTERVAL_SECONDS", 0.25)
        user = users[0]
        found = self._search_succeeded(client, user, "离线群", [
            {"title": "离线群", "member_count": 4, "target_ref": "wxg:off"},
        ])
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "离线群",
                "target_ref": "wxg:off", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]

        # 无设备响应 → 409 VERIFY_FAILED，绑定保持 pending（可重试）
        resp = client.post(
            f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "VERIFY_FAILED"
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM bs_weixin_marketing_group_bindings "
                "WHERE tenant_id = %s AND id = %s",
                (user["tenant_id"], binding_id),
            )
            assert cur.fetchone()["state"] == "pending"

        # 阶段二（重试）：放宽等待窗口（慢机宽容下限），设备恢复后 → verified
        # （服务端不伪造通过、失败不锁死）
        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 20)
        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"],
            search_results={"离线群": [
                {"title": "离线群", "member_count": 4, "target_ref": "wxg:off"},
            ]},
        ).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 200, resp.text
        finally:
            worker.stop()
        assert resp.json()["data"]["result"] == "verified"

    def test_verify_same_key_retry_after_recovery(self, client, users, monkeypatch):
        """V-P1 粘滞失败修复探针场景：离线 409（abandon 释放占位）→ 设备恢复 →
        **同 key** 重试成功——dedupe 键遇非 queued 残留换 :r2 键重建新 invocation，
        不复用旧失败；成功后同 key 再试走路由层重放。"""
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 2.0)
        monkeypatch.setattr(wxm_workbench, "READ_OP_POLL_INTERVAL_SECONDS", 0.25)
        user = users[0]
        found = self._search_succeeded(client, user, "粘滞验证群", [
            {"title": "粘滞验证群", "member_count": 6, "target_ref": "wxg:sticky"},
        ])
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "粘滞验证群",
                "target_ref": "wxg:sticky", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]
        key = {"Idempotency-Key": f"it-sticky-{uuid.uuid4().hex[:12]}"}

        # 阶段一：设备离线 → 409 VERIFY_FAILED（占位已 abandon 释放）
        offline = client.post(
            f"{PREFIX}/group-bindings/{binding_id}/verify",
            headers={**_auth(user), **key}, json={},
        )
        assert offline.status_code == 409, offline.text
        assert offline.json()["code"] == "VERIFY_FAILED"

        # 阶段二：设备恢复 → 同 key 重试 → 200 verified（新建 invocation）
        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 20)
        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"],
            search_results={"粘滞验证群": [
                {"title": "粘滞验证群", "member_count": 6, "target_ref": "wxg:sticky"},
            ]},
        ).start()
        try:
            retried = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify",
                headers={**_auth(user), **key}, json={},
            )
            assert retried.status_code == 200, retried.text
        finally:
            worker.stop()
        assert retried.json()["data"]["result"] == "verified"

        # 重试确实重建了 invocation（离线一条 cancelled/终态 + 成功一条 = 2）
        assert _count(
            "local_tool_invocations", user["tenant_id"],
            "AND business_kind = 'weixin_binding_verify' AND device_id = %s",
            (found["device_id"],),
        ) == 2

        # 成功后同 key 再试 → 路由层幂等重放（不再执行、响应一致）
        replay = client.post(
            f"{PREFIX}/group-bindings/{binding_id}/verify",
            headers={**_auth(user), **key}, json={},
        )
        assert replay.status_code == 200
        assert replay.json() == retried.json()

    def test_verify_zero_exact_match_keeps_pending(self, client, users, monkeypatch):
        """V-P2-2：零精确命中 → 409 VERIFY_FAILED（0 命中），绑定保持待核验可重试"""
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 10)
        user = users[0]
        found = self._search_succeeded(client, user, "零命中群", [
            {"title": "零命中群备份", "member_count": 9, "target_ref": "wxg:z1"},
        ])
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "零命中群",
                "target_ref": "wxg:z1", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]

        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"],
            search_results={"零命中群": [
                {"title": "零命中群备份", "member_count": 9, "target_ref": "wxg:z1"},
                {"title": "零命中群（分部）", "member_count": 3, "target_ref": "wxg:z2"},
            ]},
        ).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 409, resp.text
        finally:
            worker.stop()
        assert resp.json()["code"] == "VERIFY_FAILED"
        assert "0 命中" in resp.json()["error"]
        assert _count(
            "bs_weixin_marketing_group_bindings", user["tenant_id"],
            "AND id = %s AND state = 'pending'", (binding_id,),
        ) == 1

    def test_verify_truncated_by_count_keeps_pending(self, client, users, monkeypatch):
        """P3 复审 P1-2 ①：无截断标志且结果条数 == 请求 limit → 视为可能截断，
        即使同名唯一命中在返回集内也不判 complete，409 VERIFY_FAILED + 保持 pending"""
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 10)
        user = users[0]
        # 50 条（== SEARCH_RESULT_LIMIT）：唯一一条精确命中在集合内
        truncated_items = [{"title": "截断群", "member_count": 2, "target_ref": "wxg:hit"}] + [
            {"title": f"无关群{i}", "member_count": 2, "target_ref": f"wxg:n{i}"}
            for i in range(wxm_workbench.SEARCH_RESULT_LIMIT - 1)
        ]
        assert len(truncated_items) == wxm_workbench.SEARCH_RESULT_LIMIT
        found = self._search_succeeded(client, user, "截断群", truncated_items)
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "截断群",
                "target_ref": "wxg:hit", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]

        # 核验回包不带任何标志：条数 == limit → 可能截断，阻断唯一性判定
        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"],
            search_results={"截断群": {"items": truncated_items}},
        ).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 409, resp.text
        finally:
            worker.stop()
        assert resp.json()["code"] == "VERIFY_FAILED"
        assert "候选集合可能不完整" in resp.json()["error"]
        assert f"达到上限 {wxm_workbench.SEARCH_RESULT_LIMIT}" in resp.json()["error"]
        assert _count(
            "bs_weixin_marketing_group_bindings", user["tenant_id"],
            "AND id = %s AND state = 'pending'", (binding_id,),
        ) == 1

    def test_verify_truncated_flag_blocks_rejected_too(self, client, users, monkeypatch):
        """P3 复审 P1-2 ②：显式 truncated 标志 → 保持 pending（不判 complete，也
        不落 rejected 终态——同名多命中同样先被完整性门槛拦截）"""
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 10)
        user = users[0]
        found = self._search_succeeded(client, user, "截断标志群", [
            {"title": "截断标志群", "member_count": 3, "target_ref": "wxg:t1"},
        ])
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "截断标志群",
                "target_ref": "wxg:t1", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]

        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"],
            search_results={"截断标志群": {
                "items": [{"title": "截断标志群", "member_count": 3, "target_ref": "wxg:t1"}],
                "truncated": True,
            }},
        ).start()
        try:
            # 唯一命中 + 显式截断标志 → 409 保持 pending（不判 complete）
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 409, resp.text
            assert resp.json()["code"] == "VERIFY_FAILED"
            assert "截断" in resp.json()["error"]
            assert _count(
                "bs_weixin_marketing_group_bindings", user["tenant_id"],
                "AND id = %s AND state = 'pending'", (binding_id,),
            ) == 1

            # 同名多命中 + 显式截断标志 → 同样 409 保持 pending（不落 rejected 终态）
            worker.search_results["截断标志群"] = {
                "items": [
                    {"title": "截断标志群", "member_count": 3, "target_ref": "wxg:t1"},
                    {"title": "截断标志群", "member_count": 9, "target_ref": "wxg:t2"},
                ],
                "truncated": True,
            }
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 409, resp.text
        finally:
            worker.stop()
        assert resp.json()["code"] == "VERIFY_FAILED"
        assert _count(
            "bs_weixin_marketing_group_bindings", user["tenant_id"],
            "AND id = %s AND state = 'pending'", (binding_id,),
        ) == 1

    def test_verify_complete_below_limit_unique_match_passes(self, client, users, monkeypatch):
        """P3 复审 P1-2 ③：完整形态（条数 < limit）→ 唯一精确命中 complete（回归）。
        显式 truncated=False 的多条候选同样放行（非精确命中不干扰唯一性判定）。"""
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 10)
        user = users[0]
        items = [
            {"title": "完整核验群", "member_count": 5, "target_ref": "wxg:ok1"},
            {"title": "完整核验群分部", "member_count": 5, "target_ref": "wxg:ok2"},
        ]
        found = self._search_succeeded(client, user, "完整核验群", items)
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "完整核验群",
                "target_ref": "wxg:ok1", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]

        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"],
            search_results={"完整核验群": {"items": items, "truncated": False}},
        ).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 200, resp.text
        finally:
            worker.stop()
        assert resp.json()["data"]["result"] == "verified"
        assert resp.json()["data"]["state"] == "complete"

    def test_verify_tool_failure_keeps_pending(self, client, users, monkeypatch):
        """V-P2-2：设备侧工具执行失败（invocation failed 终态）→ 409 VERIFY_FAILED，
        绑定保持待核验可重试"""
        from src.weixin_marketing import workbench as wxm_workbench

        monkeypatch.setattr(wxm_workbench, "READ_OP_WAIT_SECONDS", 10)
        user = users[0]
        found = self._search_succeeded(client, user, "工具失败群", [
            {"title": "工具失败群", "member_count": 2, "target_ref": "wxg:f1"},
        ])
        resp = client.post(
            f"{PREFIX}/group-bindings", headers=_auth(user),
            json={
                "device_id": found["device_id"], "label": "工具失败群",
                "target_ref": "wxg:f1", "search_id": found["search_id"],
            },
        )
        binding_id = resp.json()["data"]["binding_id"]

        worker = FakeDeviceWorker(
            user["tenant_id"], found["device_id"], fail_tools={"weixin_chat_search"},
        ).start()
        try:
            resp = client.post(
                f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(user), json={}
            )
            assert resp.status_code == 409, resp.text
        finally:
            worker.stop()
        assert resp.json()["code"] == "VERIFY_FAILED"
        assert "核验工具执行失败" in resp.json()["error"]
        assert _count(
            "bs_weixin_marketing_group_bindings", user["tenant_id"],
            "AND id = %s AND state = 'pending'", (binding_id,),
        ) == 1

    def test_verify_cross_tenant_404(self, client, users):
        user = users[0]
        device_id = _create_device(user["tenant_id"], user["user_id"])
        binding_id = _create_binding(user["tenant_id"], user["user_id"], device_id=device_id)
        assert client.post(
            f"{PREFIX}/group-bindings/{binding_id}/verify", headers=_auth(users[1]), json={}
        ).status_code == 404
        assert client.post(
            f"{PREFIX}/group-bindings/not-a-uuid/verify", headers=_auth(user), json={}
        ).status_code == 404


# ==================== test-send ====================


class TestTestSend:
    def _published(self, client, user, *, blocks=None):
        device_id = _create_device(user["tenant_id"], user["user_id"])
        binding_id = _create_binding(user["tenant_id"], user["user_id"], device_id=device_id)
        detail = _api_create_automation(client, user, binding_id, blocks=blocks)
        automation_id = str(detail["automation"]["id"])
        published = _api_publish(client, user, automation_id)
        return automation_id, published["revision_id"], binding_id, device_id

    def test_send_single_block_full_chain_and_independent_quota(
        self, client, users, wxm_adapter
    ):
        user = users[0]
        tenant_id = user["tenant_id"]
        automation_id, revision_id, binding_id, device_id = self._published(client, user)
        key = {"Idempotency-Key": f"it-testsend-{uuid.uuid4().hex[:12]}"}

        worker = FakeDeviceWorker(tenant_id, device_id).start()
        try:
            resp = client.post(
                f"{PREFIX}/automations/{automation_id}/test-send",
                headers={**_auth(user), **key},
                json={"group_binding_id": binding_id, "block_position": 1},
            )
            assert resp.status_code == 202, resp.text
            data = resp.json()["data"]
            assert data["block_position"] == 1
            assert data["run_id"]

            # 等待设备侧完成 v2 写链路（write-authorize→verified）
            deadline = time.monotonic() + 20
            state = None
            while time.monotonic() < deadline:
                got = client.get(f"{PREFIX}/runs/{data['run_id']}", headers=_auth(user))
                assert got.status_code == 200, got.text
                state = got.json()["data"]["run"]["state"]
                if state in ("succeeded", "failed", "unknown", "partial", "cancelled", "expired"):
                    break
                time.sleep(0.2)
            assert state == "succeeded", state
            detail = client.get(
                f"{PREFIX}/runs/{data['run_id']}", headers=_auth(user)
            ).json()["data"]
            # 只试发该条：单条 delivery
            assert len(detail["deliveries"]) == 1
            assert detail["deliveries"][0]["latest_attempt"]["evidence_ref"]
        finally:
            worker.stop()

        # 独立配额断言：wxm:test:* 桶已落账（reserved→used），生产 wxm: 桶零行
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT scope_type, scope_id, reserved_count, used_count
                FROM desktop_automation_quota_buckets
                WHERE tenant_id = %s AND (scope_id LIKE 'wxm:test:%%' OR scope_id LIKE 'wxm:%%')
                """,
                (tenant_id,),
            )
            buckets = {(r["scope_id"]): dict(r) for r in cur.fetchall()}
        test_ids = [sid for sid in buckets if sid.startswith("wxm:test:")]
        prod_ids = [sid for sid in buckets if not sid.startswith("wxm:test:")]
        assert test_ids, "试发应有 wxm:test:* 配额桶"
        assert all(buckets[sid]["used_count"] == 1 for sid in test_ids), buckets
        assert prod_ids == [], f"试发不应占用生产配额桶: {prod_ids}"

        # 独立审计：:test task_ref 的 run + weixin 审计行；不进生产 runs 列表
        resp = client.get(
            f"{PREFIX}/runs", headers=_auth(user), params={"automation_id": automation_id}
        )
        assert resp.json()["data"]["total"] == 0  # 试发 run 不在生产列表
        assert _count(
            "bs_weixin_marketing_audit_events", tenant_id,
            "AND action = 'test_send_requested'",
        ) == 1
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT task_ref FROM desktop_automation_runs WHERE tenant_id = %s AND id = %s",
                (tenant_id, data["run_id"]),
            )
            assert cur.fetchone()["task_ref"] == f"{automation_id}:test"

        # 幂等重放：同 key 同 body → 同 run（不重复发送）
        replay = client.post(
            f"{PREFIX}/automations/{automation_id}/test-send",
            headers={**_auth(user), **key},
            json={"group_binding_id": binding_id, "block_position": 1},
        )
        assert replay.status_code == 202
        assert replay.json()["data"]["run_id"] == data["run_id"]
        # 同 key 异 body → 409
        conflict = client.post(
            f"{PREFIX}/automations/{automation_id}/test-send",
            headers={**_auth(user), **key},
            json={"group_binding_id": binding_id, "block_position": 2},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"

    def test_send_position_selects_exact_block_content(
        self, client, users, wxm_adapter
    ):
        """复审（2026-09-10）：多块 revision 试发按 block_position 精确绑定实发内容——
        position=2 时 delivery 的 payload_ref/hash 必须是第 2 块（1 基编号，防错发）。
        test_send 派发即返回（非阻塞），delivery 行在 202 响应时已同步落库。"""
        import hashlib as _h

        from src.db.database import get_db_connection

        user = users[0]
        tenant_id = user["tenant_id"]
        blocks = [
            {"type": "text", "text_content": "第一条内容"},
            {"type": "text", "text_content": "第二条内容"},
        ]
        automation_id, revision_id, binding_id, device_id = self._published(
            client, user, blocks=blocks
        )
        key = {"Idempotency-Key": f"it-pos-{uuid.uuid4().hex[:12]}"}

        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/test-send",
            headers={**_auth(user), **key},
            json={"group_binding_id": binding_id, "block_position": 2},
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        run_id = data["run_id"]
        assert data["block_position"] == 2
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT payload_ref, payload_hash FROM desktop_automation_deliveries
                WHERE tenant_id = %s AND run_id = %s
                """,
                (tenant_id, run_id),
            )
            delivery = cur.fetchone()
            assert delivery is not None
        # 实发内容绑定断言：ref 指向第 2 块、hash 与第 2 块冻结内容一致
        assert delivery["payload_ref"].endswith(f":{revision_id}:2")
        assert delivery["payload_hash"] == _h.sha256("第二条内容".encode("utf-8")).hexdigest()

    def test_send_validation_errors(self, client, users, wxm_adapter, monkeypatch):
        user = users[0]
        automation_id, _, binding_id, device_id = self._published(client, user)
        url = f"{PREFIX}/automations/{automation_id}/test-send"
        headers = _auth(user)

        # 非法 position → 422
        resp = client.post(url, headers=headers, json={"group_binding_id": binding_id, "block_position": 99})
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_FAILED"

        # 图片块 → 422（临时放开 images_enabled 以落进已发布 revision，再验证试发拒绝）
        from src.weixin_marketing import triggers as wxm_triggers
        from src.weixin_marketing.config import get_weixin_marketing_config

        images_on = replace(get_weixin_marketing_config(), images_enabled=True)
        monkeypatch.setattr(
            wxm_triggers, "get_weixin_marketing_config", lambda: images_on
        )
        monkeypatch.setattr(wxm_adapter, "_config", images_on)
        img_automation, _, img_binding, _ = self._published(
            client, user, blocks=[{"type": "image", "asset_id": str(uuid.uuid4())}]
        )
        monkeypatch.undo()
        resp = client.post(
            f"{PREFIX}/automations/{img_automation}/test-send",
            headers=headers, json={"group_binding_id": img_binding, "block_position": 1},
        )
        assert resp.status_code == 422
        assert "图片" in resp.json()["error"]

        # 不可用绑定（pending）→ 422；无绑定（空）→ 422
        pending_device = _create_device(user["tenant_id"], user["user_id"])
        pending_binding = _create_binding(
            user["tenant_id"], user["user_id"], device_id=pending_device, state="pending"
        )
        resp = client.post(url, headers=headers, json={"group_binding_id": pending_binding, "block_position": 1})
        assert resp.status_code == 422
        assert resp.json()["field_errors"]  # 服务层语义错统一映射（field 为空串，同既有端点）
        resp = client.post(url, headers=headers, json={"group_binding_id": str(uuid.uuid4()), "block_position": 1})
        assert resp.status_code == 422

        # 未发布（draft）→ 409
        draft_device = _create_device(user["tenant_id"], user["user_id"])
        draft_binding = _create_binding(user["tenant_id"], user["user_id"], device_id=draft_device)
        detail = _api_create_automation(client, user, draft_binding, name="试发未发布")
        resp = client.post(
            f"{PREFIX}/automations/{detail['automation']['id']}/test-send",
            headers=headers,
            json={"group_binding_id": draft_binding, "block_position": 1},
        )
        assert resp.status_code == 409

        # 跨租户 → 404；非法 UUID → 404；未登录 → 401
        assert client.post(
            url, headers=_auth(users[1]),
            json={"group_binding_id": binding_id, "block_position": 1},
        ).status_code == 404
        assert client.post(
            f"{PREFIX}/automations/not-a-uuid/test-send", headers=headers,
            json={"group_binding_id": binding_id, "block_position": 1},
        ).status_code == 404
        assert client.post(
            url, json={"group_binding_id": binding_id, "block_position": 1}
        ).status_code == 401

    def test_send_quota_precheck_429(self, client, users, wxm_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        automation_id, _, binding_id, device_id = self._published(client, user)

        # 手工把 test target 桶打满（reserved+used >= limit）→ 试发 429
        from src.db.database import get_db_connection
        from src.desktop_automation import quota as da_quota
        from src.weixin_marketing.config import get_weixin_marketing_config

        cfg = get_weixin_marketing_config()
        bucket_start = da_quota.bucket_start_for(cfg.quotas.window_seconds, utcnow())
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO desktop_automation_quota_buckets
                    (tenant_id, scope_type, scope_id, bucket_start, window_seconds,
                     limit_count, reserved_count, used_count)
                VALUES (%s, 'target', %s, %s, %s, 1, 1, 0)
                """,
                (
                    tenant_id, f"wxm:test:gb:{binding_id}", bucket_start,
                    cfg.quotas.window_seconds,
                ),
            )
            conn.commit()
        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/test-send",
            headers=_auth(user),
            json={"group_binding_id": binding_id, "block_position": 1},
        )
        assert resp.status_code == 429, resp.text
        assert resp.json()["code"] == "QUOTA_EXCEEDED"
        # 零副作用：本自动化（生产 + :test task_ref）无 run/invocation 产生
        assert _count(
            "desktop_automation_runs", tenant_id,
            "AND task_ref IN (%s, %s)", (automation_id, f"{automation_id}:test"),
        ) == 0
        assert _count(
            "local_tool_invocations", tenant_id, "AND device_id = %s", (device_id,),
        ) == 0
