"""微信营销素材 API 集成测试（P4-A，R57/R58：上传矩阵/ACL/引用保护/Runtime 下载）

覆盖：
- HTTP 上传正常流（multipart → 元数据 → 列表/详情 → content 字节+hash → 删除幂等）；
- 上传约束矩阵（伪 mime 实测/损坏/截断/超大小/超像素/images_enabled=false）；
- ACL：跨租户 404 统一；
- 引用保护：draft 引用删除 409 ASSET_IN_USE + 文案；解引用（重存草稿）后可删；
- Runtime 资产下载端点 E2E：真实配对设备 token → 归属校验链 → 字节+X-Asset-Hash；
  未引用素材 404 / 跨租户 404 / 终态 409；
- 测试后物理清理（含素材文件目录与零残留核实）。
"""

import io
import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

pytestmark = pytest.mark.integration

PREFIX = "/api/weixin-marketing"
RUNTIME_PREFIX = "/api/local-tools/runtime"

ALL_CLEANUP_TABLES = (
    # 底座表（依赖序，照 tests/unit/desktop_automation/conftest.py）
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_deliveries",
    "desktop_automation_runs",
    "desktop_automation_occurrences",
    "desktop_automation_outbox",
    "desktop_automation_events",
    "desktop_automation_schedules",
    "desktop_automation_subjects",
    "desktop_automation_audit_events",
    "desktop_automation_quota_buckets",
    "local_tool_operation_permits",
    "local_tool_events",
    "local_tool_invocations",
    "local_tool_devices",
    "local_tool_pairing_tickets",
    # weixin 业务表
    "bs_weixin_marketing_audit_events",
    "bs_weixin_marketing_content_blocks",
    "bs_weixin_marketing_revisions",
    "bs_weixin_marketing_automations",
    "bs_weixin_marketing_group_bindings",
    "bs_weixin_marketing_account_bindings",
    "bs_weixin_marketing_assets",
    "weixin_marketing_idempotency_keys",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def make_png(width=120, height=80, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def make_big_png(approx_bytes=90_000) -> bytes:
    """生成 ~90KB 的真实 PNG（触发 Content-Length 提前拒绝路径）"""
    buf = io.BytesIO()
    size = 400
    while True:
        img = Image.new("RGB", (size, size))
        # 随机噪声保证压缩后体积（PNG 无损，噪声不可压）
        px = img.load()
        seed = 12345
        for y in range(size):
            for x in range(size):
                seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
                px[x, y] = (seed % 256, (seed >> 8) % 256, (seed >> 16) % 256)
        img.save(buf, format="PNG")
        if buf.tell() >= approx_bytes or size > 1200:
            break
        size += 200
    return buf.getvalue()


@contextmanager
def images_enabled_config(**overrides):
    """把 images_enabled=true（可叠加覆盖）注入服务/校验/素材模块的配置读取点。

    service/triggers/assets 三个模块各自持 get_weixin_marketing_config 引用，
    api.py 上传端点为函数内 import（patch config 模块本体即可命中）。
    """
    from src.weixin_marketing import assets as wxm_assets
    from src.weixin_marketing import config as wxm_config_module
    from src.weixin_marketing import service as wxm_service
    from src.weixin_marketing import triggers as wxm_triggers

    cfg = replace(
        wxm_config_module.get_weixin_marketing_config(),
        enabled=True,
        images_enabled=True,
        **overrides,
    )
    saved = []
    for mod in (wxm_service, wxm_triggers, wxm_assets, wxm_config_module):
        saved.append((mod, mod.get_weixin_marketing_config))
        mod.get_weixin_marketing_config = lambda: cfg
    try:
        yield cfg
    finally:
        for mod, fn in saved:
            mod.get_weixin_marketing_config = fn


# ==================== fixtures ====================


@pytest.fixture(scope="module")
def img_adapter():
    """注册 images_enabled=true 的受信适配器（发布图片 revision 用）"""
    from dataclasses import replace as _replace

    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.weixin_marketing import config as wxm_config_module
    from src.weixin_marketing.adapters import WeixinFixedContentAdapter

    instance = WeixinFixedContentAdapter(
        config=_replace(
            wxm_config_module.get_weixin_marketing_config(),
            enabled=True, time_triggers_enabled=True, images_enabled=True,
        )
    )
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
        code = f"A{uuid.uuid4().hex[:6].upper()}"
        tenant = TenantDB.create(
            company_name=f"微信素材测试-{code}",
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
        # 素材文件目录回收（行删除前先取 storage_ref）
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT storage_ref FROM bs_weixin_marketing_assets WHERE tenant_id = %s",
                    (tenant_id,),
                )
                refs = [r["storage_ref"] for r in cur.fetchall()]
            for ref in refs:
                if ref and os.path.exists(ref):
                    os.remove(ref)
        except Exception as e:  # noqa: BLE001
            problems.append(f"tenant={tenant_id} 素材文件回收异常: {e}")
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
                        f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s", (tenant_id,)
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
        user = UserDB.create(phone=phone, username=f"微信素材测试用户{i}", tenant_id=tenant_id)
        if not user:
            pytest.skip("无法创建测试用户（DB 不可用）")
        token = generate_token(user["user_id"])
        result.append({"user_id": user["user_id"], "tenant_id": tenant_id, "token": token})
    return result


def _create_binding(tenant_id: str, user_id: str, device_id=None) -> str:
    from src.db.database import get_db_connection

    account_id, group_id = str(uuid.uuid4()), str(uuid.uuid4())
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_account_bindings
                (id, tenant_id, user_id, device_id, account_anchor_ref, session_epoch, status)
            VALUES (%s, %s, %s, %s, 'anchor-as', 1, 'active')
            """,
            (account_id, tenant_id, user_id, device_id or str(uuid.uuid4())),
        )
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_group_bindings
                (id, tenant_id, user_id, device_id, account_binding_id, label,
                 identity_evidence_ref, identity_version, state, verified_at)
            VALUES (%s, %s, %s, %s, %s, '素材集成群', 'ev-as', '1', 'complete', NOW())
            """,
            (group_id, tenant_id, user_id, device_id or str(uuid.uuid4()), account_id),
        )
        conn.commit()
    return group_id


def _pair_weixin_device(client, user) -> tuple:
    """完整 Web→Runtime 配对（capabilities 含 weixin provider），返回 (device_id, token)"""
    resp = client.post("/api/local-tools/pairing-tickets", headers=_auth(user))
    assert resp.status_code == 200, resp.text
    resp = client.post(
        f"{RUNTIME_PREFIX}/pair",
        json={
            "code": resp.json()["code"],
            "name": "素材测试设备",
            "platform": "windows",
            "runtime_version": "1.0.0",
            "capabilities": {"providers": ["weixin"]},
            "machine_fingerprint": "fp-" + uuid.uuid4().hex[:8],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["device_id"], body["device_token"]


def _upload(client, user, content: bytes, filename="t.png", mime="image/png"):
    return client.post(
        f"{PREFIX}/assets", headers=_auth(user),
        files={"file": (filename, content, mime)},
    )


def _cleanup_scene_dirs(tenant_id: str) -> None:
    """回收素材存储目录（normalize 剥 tenant_ 前缀，与 assets 写入路径一致）"""
    from src.core.storage import normalize_tenant_id

    tenant_dir = os.path.join("storage", "tenants", normalize_tenant_id(tenant_id))
    if os.path.isdir(tenant_dir):
        shutil.rmtree(tenant_dir, ignore_errors=True)


# ==================== 上传 / 查询 / 删除 HTTP 流 ====================


class TestAssetsHttp:
    def test_upload_list_content_delete_flow(self, client, users):
        user = users[0]
        tenant_id = user["tenant_id"]
        try:
            png = make_png()
            with images_enabled_config():
                resp = _upload(client, user, png)
            assert resp.status_code == 200, resp.text
            meta = resp.json()["data"]
            assert meta["mime"] == "image/png"
            assert (meta["width"], meta["height"]) == (120, 80)
            asset_id = meta["id"]

            with images_enabled_config():
                listed = client.get(f"{PREFIX}/assets", headers=_auth(user)).json()["data"]
                detail = client.get(
                    f"{PREFIX}/assets/{asset_id}", headers=_auth(user)
                ).json()["data"]
            assert listed["total"] == 1
            assert listed["items"][0]["id"] == asset_id
            assert listed["images_enabled"] is True
            assert detail["reference_count"] == 0

            resp = client.get(f"{PREFIX}/assets/{asset_id}/content", headers=_auth(user))
            assert resp.status_code == 200, resp.text
            assert resp.content == png
            assert resp.headers["x-asset-hash"] == meta["sha256"]
            assert resp.headers["content-type"].startswith("image/png")
            assert resp.headers["x-content-type-options"] == "nosniff"

            resp = client.delete(f"{PREFIX}/assets/{asset_id}", headers=_auth(user))
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"] == {"asset_id": asset_id, "deleted": True}
            # 幂等：重复删除 404（不区分不存在/已删）
            resp = client.delete(f"{PREFIX}/assets/{asset_id}", headers=_auth(user))
            assert resp.status_code == 404
            assert resp.json()["code"] == "NOT_FOUND"
        finally:
            _cleanup_scene_dirs(tenant_id)

    def test_upload_constraint_matrix(self, client, users):
        user = users[0]
        tenant_id = user["tenant_id"]
        try:
            png = make_png()
            cases = [
                # (label, content, mime, 错误文案包含子串)
                ("伪 mime（文本字节）", b"plain text not image", "image/png", "拒绝"),
                ("截断 PNG", png[:24], "image/png", "拒绝"),
                ("空内容", b"", "image/png", "为空"),
            ]
            with images_enabled_config():
                for label, content, mime, match in cases:
                    resp = _upload(client, user, content, mime=mime)
                    assert resp.status_code == 422, (label, resp.status_code, resp.text)
                    body = resp.json()
                    assert body["code"] == "VALIDATION_FAILED", label
                    assert match in body["error"], (label, body["error"])
            # 超大小（服务层上限）
            with images_enabled_config(asset_max_bytes=100):
                resp = _upload(client, user, png)
                assert resp.status_code == 422
                assert "上限" in resp.json()["error"]
            # 超大小（Content-Length 提前拒绝：~90KB 实际 PNG + 10 字节上限）
            big = make_big_png()
            with images_enabled_config(asset_max_bytes=10):
                resp = _upload(client, user, big)
                assert resp.status_code == 422, resp.text
                assert "上限" in resp.json()["error"]
            # 超像素
            with images_enabled_config(asset_max_pixels=1000):
                resp = _upload(client, user, png)
                assert resp.status_code == 422
                assert "像素" in resp.json()["error"]
            # images_enabled=false（未打补丁的全局配置）
            resp = _upload(client, user, png)
            assert resp.status_code == 422
            assert "images_enabled" in resp.json()["error"]
            # 全部失败上传零行落库
            listed = client.get(f"{PREFIX}/assets", headers=_auth(user)).json()["data"]
            assert listed["total"] == 0
        finally:
            _cleanup_scene_dirs(tenant_id)

    def test_acl_cross_tenant_404(self, client, users):
        user_a, user_b = users[0], users[1]
        tenant_id = user_a["tenant_id"]
        try:
            with images_enabled_config():
                resp = _upload(client, user_a, make_png())
            asset_id = resp.json()["data"]["id"]
            # B 租户用户：详情/内容/删除/列表统一 404（不泄露存在性）
            assert client.get(
                f"{PREFIX}/assets/{asset_id}", headers=_auth(user_b)
            ).status_code == 404
            assert client.get(
                f"{PREFIX}/assets/{asset_id}/content", headers=_auth(user_b)
            ).status_code == 404
            assert client.delete(
                f"{PREFIX}/assets/{asset_id}", headers=_auth(user_b)
            ).status_code == 404
            listed_b = client.get(f"{PREFIX}/assets", headers=_auth(user_b)).json()["data"]
            assert listed_b["total"] == 0
            # 非 UUID → 404
            assert client.get(
                f"{PREFIX}/assets/not-a-uuid", headers=_auth(user_a)
            ).status_code == 404
        finally:
            _cleanup_scene_dirs(tenant_id)

    def test_reference_protection_and_release(self, client, users, img_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        try:
            png = make_png()
            with images_enabled_config():
                asset_id = _upload(client, user, png).json()["data"]["id"]
                group_id = _create_binding(tenant_id, user["user_id"])
                # 建含图片块的草稿（服务端校验素材引用）
                create_body = {
                    "name": "图片引用保护",
                    "trigger": {
                        "type": "once",
                        "run_at": (utcnow() + timedelta(hours=2)).isoformat(),
                        "timezone": "UTC",
                    },
                    "blocks": [
                        {"type": "text", "text_content": "配图内容"},
                        {"type": "image", "asset_id": asset_id},
                    ],
                    "group_binding_id": group_id,
                }
                resp = client.post(f"{PREFIX}/automations", headers=_auth(user), json=create_body)
                assert resp.status_code == 200, resp.text
                automation_id = str(resp.json()["data"]["automation"]["id"])
                version = resp.json()["data"]["automation"]["version"]

                # 引用计数 + 删除保护（409 ASSET_IN_USE 文案化）
                detail = client.get(
                    f"{PREFIX}/assets/{asset_id}", headers=_auth(user)
                ).json()["data"]
                assert detail["reference_count"] == 1
                resp = client.delete(f"{PREFIX}/assets/{asset_id}", headers=_auth(user))
                assert resp.status_code == 409, resp.text
                body = resp.json()
                assert body["code"] == "ASSET_IN_USE"
                assert "引用" in body["error"]

                # 解引用（草稿重写为纯文字）→ 可删
                resp = client.put(
                    f"{PREFIX}/automations/{automation_id}/draft",
                    headers=_auth(user),
                    json={
                        "expected_version": version,
                        "blocks": [{"type": "text", "text_content": "只剩文字"}],
                    },
                )
                assert resp.status_code == 200, resp.text
                resp = client.delete(f"{PREFIX}/assets/{asset_id}", headers=_auth(user))
                assert resp.status_code == 200, resp.text
        finally:
            _cleanup_scene_dirs(tenant_id)

    def test_image_draft_missing_asset_422(self, client, users, img_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        group_id = _create_binding(tenant_id, user["user_id"])
        with images_enabled_config():
            resp = client.post(
                f"{PREFIX}/automations", headers=_auth(user),
                json={
                    "name": "悬空引用",
                    "trigger": {
                        "type": "once",
                        "run_at": (utcnow() + timedelta(hours=2)).isoformat(),
                        "timezone": "UTC",
                    },
                    "blocks": [{"type": "image", "asset_id": str(uuid.uuid4())}],
                    "group_binding_id": group_id,
                },
            )
        assert resp.status_code == 422, resp.text
        assert "素材" in resp.json()["error"]


# ==================== Runtime 资产下载端点（E2E）====================


class TestRuntimeAssetDownload:
    def _prepare(self, client, user, img_adapter):
        """上传素材 + 建/发图片自动化 + 手动 run + 驱动到 invocation 并 claim。"""
        from src.desktop_automation import executor
        from src.local_tools import repository
        from src.local_tools.security import generate_claim_token, sha256_hex
        from src.weixin_marketing.service import WeixinMarketingService

        tenant_id = user["tenant_id"]
        device_id, device_token = _pair_weixin_device(client, user)
        png = make_png(color=(9, 99, 199))
        with images_enabled_config():
            asset_id = _upload(client, user, png).json()["data"]["id"]
            group_id = _create_binding(tenant_id, user["user_id"], device_id=device_id)
            resp = client.post(
                f"{PREFIX}/automations", headers=_auth(user),
                json={
                    "name": "图片执行链",
                    "trigger": {
                        "type": "once",
                        "run_at": (utcnow() + timedelta(hours=2)).isoformat(),
                        "timezone": "UTC",
                    },
                    "blocks": [{"type": "image", "asset_id": asset_id}],
                    "group_binding_id": group_id,
                },
            )
            assert resp.status_code == 200, resp.text
            automation_id = str(resp.json()["data"]["automation"]["id"])
            version = resp.json()["data"]["automation"]["version"]
            resp = client.post(
                f"{PREFIX}/automations/{automation_id}/publish",
                headers=_auth(user), json={"expected_version": version},
            )
            assert resp.status_code == 200, resp.text
            revision_id = resp.json()["data"]["revision_id"]

        service = WeixinMarketingService()
        occurrence_id, run_id = None, None
        with images_enabled_config():
            manual = service.manual_run(
                tenant_id, automation_id, user["user_id"],
                request_id=f"as-{uuid.uuid4().hex[:8]}", now=utcnow(),
            )
            occurrence_id, run_id = manual["occurrence_id"], manual["run_id"]
            revision_config = service.load_revision_config(tenant_id, revision_id)
            prepared = executor.claim_and_prepare_run(
                revision_config=revision_config, device_id=device_id,
                tenant_id=tenant_id, lease_seconds=300,
            )
            assert prepared and prepared["prepared"] is True
            assert str(prepared["run"]["occurrence_id"]) == str(occurrence_id)
        step = executor.execute_next_delivery(prepared["run"], now=utcnow())
        assert step is not None and step.get("invocation_id")
        claim_token = generate_claim_token()
        claimed = repository.claim_next(device_id, tenant_id, sha256_hex(claim_token), 300)
        assert claimed is not None and str(claimed["id"]) == str(step["invocation_id"])
        started = repository.mark_started(
            str(step["invocation_id"]), tenant_id, sha256_hex(claim_token)
        )
        assert started is not None and started["state"] == "running"
        return {
            "device_id": device_id,
            "device_token": device_token,
            "claim_token": claim_token,
            "asset_id": asset_id,
            "png": png,
            "automation_id": automation_id,
            "revision_id": revision_id,
            "invocation_id": str(step["invocation_id"]),
            "request_id": step["request_id"],
            "run_id": str(run_id),
        }

    def test_runtime_download_flow(self, client, users, img_adapter):
        import hashlib

        user = users[0]
        tenant_id = user["tenant_id"]
        try:
            ctx = self._prepare(client, user, img_adapter)
            runtime = {"Authorization": f"Bearer {ctx['device_token']}"}
            url = (
                f"{RUNTIME_PREFIX}/invocations/{ctx['invocation_id']}"
                f"/assets/{ctx['asset_id']}"
            )
            resp = client.get(url, headers=runtime)
            assert resp.status_code == 200, resp.text
            assert resp.content == ctx["png"]
            assert resp.headers["x-asset-hash"] == hashlib.sha256(ctx["png"]).hexdigest()
            assert resp.headers["content-type"].startswith("image/png")
            assert resp.headers["x-content-type-options"] == "nosniff"

            # 无 token → 401
            assert client.get(url).status_code == 401
            # 非 UUID asset → 404
            bad = client.get(
                f"{RUNTIME_PREFIX}/invocations/{ctx['invocation_id']}/assets/not-a-uuid",
                headers=runtime,
            )
            assert bad.status_code == 404

            # 本租户内未被任务引用的素材 → 404（任务绑定资产集校验）
            other = make_png(color=(55, 55, 5))
            with images_enabled_config():
                unbound_id = _upload(client, user, other).json()["data"]["id"]
            resp = client.get(
                f"{RUNTIME_PREFIX}/invocations/{ctx['invocation_id']}/assets/{unbound_id}",
                headers=runtime,
            )
            assert resp.status_code == 404, resp.text

            # 终态后拒绝（409）：先许可再回执（applied+verified 需有效 permit）
            from src.local_tools import permits, repository
            from src.local_tools.operation_result import apply_operation_result
            from src.local_tools.security import sha256_hex

            inv = repository.get_invocation(ctx["invocation_id"], tenant_id)
            payload_hash = (inv["arguments_json"] or {}).get("payload_hash")
            permit = permits.write_authorize(
                tenant_id=tenant_id, device_id=ctx["device_id"],
                invocation_id=ctx["invocation_id"],
                claim_token_hash=sha256_hex(ctx["claim_token"]),
                request_id=ctx["request_id"], target_version="iv-1-se-1",
                payload_hash=payload_hash,
            )
            apply_operation_result(
                tenant_id=tenant_id, device_id=ctx["device_id"],
                invocation_id=ctx["invocation_id"],
                claim_token_hash=sha256_hex(ctx["claim_token"]),
                request_id=ctx["request_id"], effect="applied", phase="verified",
                evidence_ref=f"weixin-evidence:{ctx['request_id']}:1",
                permit_id=permit["permit_id"], permit_token=permit["permit_token"],
            )
            assert repository.get_invocation(
                ctx["invocation_id"], tenant_id
            )["state"] in ("succeeded", "failed")
            resp = client.get(url, headers=runtime)
            assert resp.status_code == 409, resp.text
        finally:
            _cleanup_scene_dirs(tenant_id)

    def test_runtime_download_cross_tenant_404(self, client, users, img_adapter):
        user_a, user_b = users[0], users[1]
        tenant_id = user_a["tenant_id"]
        try:
            ctx = self._prepare(client, user_a, img_adapter)
            _, token_b = _pair_weixin_device(client, user_b)
            resp = client.get(
                f"{RUNTIME_PREFIX}/invocations/{ctx['invocation_id']}"
                f"/assets/{ctx['asset_id']}",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert resp.status_code == 404, resp.text
        finally:
            _cleanup_scene_dirs(tenant_id)
