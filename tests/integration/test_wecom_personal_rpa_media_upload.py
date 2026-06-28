"""企业微信个人账号 RPA 渠道媒体上传集成测试

覆盖 ``docs/system/wecom-personal-rpa-protocol.md §A.9`` 媒体上传接口：

1. ``test_media_upload_success``：客户端 HMAC 签名（body 用固定占位串 ``"media-upload"``）
   + multipart 上传一个小文件，期望返回 ``{file_id, url, expires_at, size}``，
   且 URL 能 GET 回原文件内容。
2. ``test_media_upload_invalid_signature_returns_401``：签名错误 → 401。
3. ``test_media_upload_oversize_rejected``：超过 100MB 上限的文件被拒（413）。
4. ``test_media_upload_unsupported_type_rejected``：危险扩展名（.exe/.bat/.ps1/.js）
   被白名单拒绝（400 + unsupported_file_type）。

签名约定（**重要**，写进 protocol.md）：
- HMAC body **不是** multipart 原始字节（boundary 脆弱），
- 而是**固定占位串** ``b"media-upload"``，客户端和服务端都按此计算签名。

复用 ``test_wecom_personal_rpa_flow.py`` 的环境隔离方案（占位 db pool，
mock ``db.get_client`` / ``secret_crypto.decrypt_secret``），避免远程 DB 重链。
"""

# ===========================================================================
# 0. 环境隔离：必须在任何 src.* 导入之前完成
# ===========================================================================

import os
import types
from unittest.mock import MagicMock

os.environ.pop("DATABASE_URL", None)


def _install_db_stubs() -> None:
    """注入 pool 占位，避免 conftest autouse fixture 真实连库。"""
    try:
        from src.db import database as dbm  # noqa: WPS433
    except Exception:  # pragma: no cover
        return
    if getattr(dbm, "_rpa_test_stubbed", False):
        return
    dbm._rpa_test_stubbed = True
    dbm.init_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]
    dbm.get_postgres_pool = lambda *a, **kw: object()  # type: ignore[assignment]
    dbm.close_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]


_install_db_stubs()


# ===========================================================================
# 1. 正式 imports
# ===========================================================================

import json
import os.path
import shutil
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ===========================================================================
# 常量 & Helpers
# ===========================================================================

_TEST_SECRET_PLAINTEXT = "test_rpa_secret_for_hmac"
_TEST_SECRET_BYTES = _TEST_SECRET_PLAINTEXT.encode("utf-8")
_TENANT_ID = "tenant_test_media_001"
_CLIENT_ID = "client_media_001"
_CONFIG_ID = "cfg_media_001"

# 服务端固定占位 body（与路由实现一致）
_MEDIA_BODY_PLACEHOLDER = b"media-upload"


@pytest.fixture
def app() -> FastAPI:
    """最小 FastAPI app：仅挂 RPA 渠道路由。"""
    from src.saas.api import wecom_personal_rpa_routes as routes_mod

    test_app = FastAPI()
    test_app.include_router(routes_mod.router)
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _media_signed_headers(secret: bytes = _TEST_SECRET_BYTES) -> dict:
    """构造携带正确 HMAC 签名的请求头（body 用固定占位串 ``"media-upload"``）。"""
    from src.channels.wecom_personal_rpa import auth

    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = auth.compute_signature(
        _CLIENT_ID, timestamp, nonce, _MEDIA_BODY_PLACEHOLDER, secret
    )
    return {
        "X-Client-Id": _CLIENT_ID,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


def _patched_db(client_row: dict | None = None):
    """路由模块 db 替身：仅提供 media-upload 用到的 get_client。"""
    fake = MagicMock()
    default_client = client_row or {
        "id": _CLIENT_ID,
        "tenant_id": _TENANT_ID,
        "status": "active",
        "encrypted_secret": "enc_stub",
        "min_version": "1.0.0",
    }
    fake.get_client.return_value = default_client
    return fake


def _apply_common_patches(fake_db):
    """统一 patch：db + secret_crypto。返回 ctxs 列表，调用方负责 exit。"""
    from src.saas.api import wecom_personal_rpa_routes as routes_mod

    ctxs = [
        patch.object(routes_mod, "db", fake_db),
        patch.object(
            routes_mod.secret_crypto,
            "decrypt_secret",
            return_value=_TEST_SECRET_BYTES,
        ),
    ]
    for c in ctxs:
        c.__enter__()
    return ctxs


# ===========================================================================
# 测试
# ===========================================================================


@pytest.mark.integration
class TestWeComPersonalRpaMediaUpload:
    """媒体上传接口（POST /media-upload）端到端契约。"""

    def test_media_upload_success(self, client):
        """场景1：HMAC 正确 + multipart 上传小文件 → 返回 200 + 字段齐全，
        URL 能 GET 到原文件内容。

        WHY: 协议规定 body 用固定占位串 ``"media-upload"`` 而非 multipart 字节，
        避开 boundary 在不同 HTTP 客户端实现中的差异导致的签名校验脆弱性。
        """
        from src.core.storage import get_tenant_storage_dir

        storage_root = get_tenant_storage_dir(_TENANT_ID, "conversation")
        # 确保测试从干净状态开始
        tenant_root = os.path.dirname(storage_root)
        if os.path.isdir(tenant_root):
            shutil.rmtree(tenant_root)

        fake_db = _patched_db()
        ctxs = _apply_common_patches(fake_db)
        try:
            content = b"hello wecom rpa media upload - small fixture"
            files = {"file": ("fixture.txt", content, "text/plain")}
            resp = client.post(
                "/api/v1/channels/wecom-personal-rpa/media-upload",
                files=files,
                headers=_media_signed_headers(),
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # 字段齐全
            assert set(body.keys()) >= {"file_id", "url", "expires_at", "size"}
            assert body["size"] == len(content)
            assert body["file_id"]
            assert body["url"].startswith(
                "/api/v1/channels/wecom-personal-rpa/files/"
            )
            # expires_at 应是 24h 后的 ISO 时间字符串
            assert isinstance(body["expires_at"], str)

            # URL 可下载到原文件内容（FileResponse 走真实磁盘）
            download_url = body["url"]
            get_resp = client.get(download_url)
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.content == content
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)
            # 清理测试产物
            if os.path.isdir(tenant_root):
                shutil.rmtree(tenant_root, ignore_errors=True)

    def test_media_upload_invalid_signature_returns_401(self, client):
        """场景2：签名错误 → 401 + RpaErrorResponse（error=auth_failed）。"""
        fake_db = _patched_db()
        ctxs = _apply_common_patches(fake_db)
        try:
            files = {"file": ("f.txt", b"x", "text/plain")}
            bad_headers = _media_signed_headers()
            bad_headers["X-Signature"] = "0" * 64  # 篡改签名
            resp = client.post(
                "/api/v1/channels/wecom-personal-rpa/media-upload",
                files=files,
                headers=bad_headers,
            )
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert resp.status_code == 401, resp.text
        body = resp.json()
        assert body["error"] == "auth_failed"
        # 不得泄漏 secret
        assert _TEST_SECRET_PLAINTEXT not in json.dumps(body)

    def test_media_upload_oversize_rejected(self, client):
        """场景3：超过 100MB 上限的文件 → 413 Payload Too Large。

        WHY: 路由先用 ``content-length`` 头做预检（+1024 字节余量给 multipart
        开销），超限直接 413，避免读取大文件浪费 IO。

        策略：直接调用路由函数 ``upload_media``，用一个最小 fake request
        让 ``request.headers.get("content-length")`` 命中超限分支，无需
        构造真实 100MB 字节流（速度优先）。
        """
        from fastapi import UploadFile
        from starlette.datastructures import Headers

        from src.saas.api import wecom_personal_rpa_routes as routes_mod

        fake_db = _patched_db()
        ctxs = _apply_common_patches(fake_db)
        try:
            over_limit = routes_mod._MEDIA_MAX_SIZE_BYTES + 2048  # 超 +1024 阈值

            # 构造 fake request：headers.get("content-length") 返回超限值
            fake_request = MagicMock()
            # headers 是 dict-like，路由用 request.headers.get(...) 取值
            fake_request.headers = {"content-length": str(over_limit)}
            # HMAC 鉴权路径需要 dict(request.headers)，给一份带签名的头
            signed = _media_signed_headers()
            # dict(...) 会拿 MagicMock 的属性，需让 fake_request 支持 dict() 转换
            # auth.verify_request 接收 dict-like，且只读 X-* 头：直接传 signed
            fake_request.headers = signed
            # 但 content-length 必须超限 —— 在 headers 中手动塞入
            signed_with_cl = {**signed, "content-length": str(over_limit)}
            fake_request.headers = signed_with_cl

            # 路由签名：upload_media(request, file)
            # file 是 UploadFile；预检分支在调用 file.read 前就 return，
            # 所以 file 可以是 None / 任意对象
            fake_file = MagicMock(spec=UploadFile)
            fake_file.filename = "big.bin"

            # upload_media 是 async 函数（路由定义），需 await
            import asyncio

            resp = asyncio.run(routes_mod.upload_media(fake_request, fake_file))
            # resp 是 starlette JSONResponse：status_code + body
            assert resp.status_code == 413, (
                f"expect 413, got {resp.status_code}: {resp.body!r}"
            )
            payload = json.loads(resp.body)
            assert payload.get("error") == "bad_request"
            assert "超过上限" in payload.get("message", "")
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

    def test_media_upload_unsupported_type_rejected(self, client):
        """场景4：危险扩展名（.exe/.bat/.ps1/.js）被白名单拒绝（400 + unsupported_file_type）。

        WHY: 协议 §A.10 规定服务端只允许
        ``png/jpg/jpeg/gif/bmp/webp/pdf/docx/xlsx/pptx/zip/txt/csv`` 等扩展名，
        避免 .exe 等可执行文件通过签名 URL 下载，被客户端或用户误点击执行。

        策略：直接调用路由函数 ``upload_media``，构造一个能通过大小预检
        （不设 content-length 或设小值）、但文件名扩展名不在白名单的请求，
        校验返回 400 + unsupported_file_type + message 包含允许列表。
        """
        from fastapi import UploadFile

        from src.saas.api import wecom_personal_rpa_routes as routes_mod

        fake_db = _patched_db()
        ctxs = _apply_common_patches(fake_db)
        try:
            signed = _media_signed_headers()
            # 不设 content-length 让大小预检跳过（路由逻辑：declared 为 None → 跳过预检）
            fake_request = MagicMock()
            fake_request.headers = signed
            # file.filename 决定扩展名校验；file.read 不会被调用（扩展名校验在前）
            fake_file = MagicMock(spec=UploadFile)
            fake_file.filename = "malware.exe"

            import asyncio

            resp = asyncio.run(routes_mod.upload_media(fake_request, fake_file))
            assert resp.status_code == 400, (
                f"expect 400, got {resp.status_code}: {resp.body!r}"
            )
            payload = json.loads(resp.body)
            assert payload.get("error") == "unsupported_file_type"
            msg = payload.get("message", "")
            # message 要列出允许的扩展名，方便客户端调试
            assert "png" in msg and "pdf" in msg and "txt" in msg
            assert ".exe" in msg or "exe" in msg  # 错误信息回显当前扩展名
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

    @pytest.mark.parametrize(
        "filename",
        ["payload.bat", "evil.ps1", "script.js", "no_extension_file", "UPPER.PDF"],
    )
    def test_media_upload_dangerous_extensions_rejected(self, client, filename):
        """补充参数化测试：多种危险/异常扩展名都被拒，且大小写不敏感（UPPER.PDF 通过）。

        WHY: 白名单逻辑用 ``os.path.splitext()[1].lower()``，确保 .PDF / .Pdf 等
        大写写法仍能被识别为合法 pdf。no_extension_file 验证无扩展名时也被拒
        （ext == "" 不在白名单）。
        """
        from fastapi import UploadFile

        from src.saas.api import wecom_personal_rpa_routes as routes_mod

        fake_db = _patched_db()
        ctxs = _apply_common_patches(fake_db)
        try:
            signed = _media_signed_headers()
            fake_request = MagicMock()
            fake_request.headers = signed
            fake_file = MagicMock(spec=UploadFile)
            fake_file.filename = filename

            import asyncio

            resp = asyncio.run(routes_mod.upload_media(fake_request, fake_file))
            # UPPER.PDF 应该通过（pdf 在白名单，已 lower）；其他全部 400
            if filename == "UPPER.PDF":
                # 这个分支不能简单断言 200 —— 因为没有真实文件流，文件写入会失败。
                # 改断言：扩展名校验通过（未返回 unsupported_file_type），落到后续流程
                payload = json.loads(resp.body) if resp.status_code != 200 else {}
                assert payload.get("error") != "unsupported_file_type", (
                    f"UPPER.PDF 不应被扩展名白名单拒绝: {resp.body!r}"
                )
            else:
                assert resp.status_code == 400, (
                    f"{filename}: expect 400, got {resp.status_code}: {resp.body!r}"
                )
                payload = json.loads(resp.body)
                assert payload.get("error") == "unsupported_file_type"
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)
