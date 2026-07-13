"""
ImageRegistry + ImageRef 单元测试（Phase 0）

覆盖：
- register（copy/move/Pillow 失败/knowledge_base 永久 TTL）
- resolve_local_path
- get_ref_by_file_id（隐式，通过 fetch cache 命中分支覆盖）
- fetch_to_local（URL 缓存命中 / 超大文件拒绝 / 超时）
- cleanup_temp（仅清理临时图片，知识库永久图不受影响）
- download_url 兼容 cp 的 /api/files/{file_id}/download 路由

所有外部依赖均 mock：
- redis_client → 用 _InMemoryFallback 替身（接口与 RedisClient 公开方法签名一致）
- Pillow → mock Image.open 测试损坏图不抛
- httpx → mock AsyncClient / stream 测试 fetch / 超时 / oversize
- 磁盘文件 → 用 tmp_path 写真实 1x1 PNG

参考：
- .claude/rules/testing.md
- docs/plans/plan-image-asset-pipeline.md P0.1
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.image_asset import (
    ImageRef,
    ImageRegistry,
    get_image_registry,
)
from src.core.redis_client import _InMemoryFallback


# ============================================================
# fixtures
# ============================================================

class _MemoryRedisForTest:
    """测试用的 Redis 替身：包装 _InMemoryFallback 并补齐 make_key（与 RedisClient 接口对齐）。

    _InMemoryFallback 是 RedisClient 的内部降级实现，但缺少 make_key（make_key 是
    RedisClient 的方法）。这里组合包装，让 ImageRegistry 测试无需启动真实 Redis。
    """

    def __init__(self):
        self._fallback = _InMemoryFallback()

    def make_key(self, prefix: str, identifier: str) -> str:
        # 与 RedisClient.make_key 行为一致（无 key_prefix 拼接）
        return f"{prefix}:{identifier}"

    def __getattr__(self, name):
        # 其他方法（hset/hget/hgetall/expire/get/set/delete/keys 等）委托给 fallback
        return getattr(self._fallback, name)


@pytest.fixture
def in_memory_redis():
    """每个测试独立的内存 Redis 替身（与 RedisClient 公开方法签名一致）。"""
    return _MemoryRedisForTest()


@pytest.fixture
def registry(in_memory_redis):
    """ImageRegistry 实例，注入内存 Redis 替身。"""
    reg = ImageRegistry()
    reg._redis = in_memory_redis
    return reg


def _make_1x1_png(path: Path) -> Path:
    """创建一个真实的 1x1 PNG 文件（Pillow 可正常读取）。"""
    from PIL import Image
    img = Image.new("RGB", (1, 1), color=(255, 0, 0))
    img.save(path, format="PNG")
    return path


def _make_corrupt_image(path: Path) -> Path:
    """创建一个内容损坏的「图片」文件（扩展名 .png 但内容是乱码）。"""
    path.write_bytes(b"not a real png content")
    return path


# ============================================================
# 1. test_register_copy_creates_image_ref
# ============================================================

@pytest.mark.asyncio
async def test_register_copy_creates_image_ref(registry, tmp_path):
    """复制注册：源文件保留，返回的 ImageRef 字段完整、Redis 元信息齐全。"""
    src = _make_1x1_png(tmp_path / "src.png")

    ref = await registry.register(
        source_path=src,
        tenant_id="tenant_a",
        user_id="user_1",
        display_name="测试图片",
        source="tool_generated",
        usage="inline",
    )

    # 源文件保留（copy 模式）
    assert src.exists(), "copy 模式下源文件应保留"

    # file_id 格式
    assert ref.file_id.startswith("file_")
    assert len(ref.file_id) == len("file_") + 12

    # download_url 兼容 cp 的路由
    assert ref.download_url == f"/api/files/{ref.file_id}/download"

    # display_name 自动补扩展名
    assert ref.display_name == "测试图片.png"

    # 宽高从 Pillow 读取
    assert ref.width == 1
    assert ref.height == 1

    # mime_type
    assert ref.mime_type == "image/png"

    # 源信息
    assert ref.source == "tool_generated"
    assert ref.usage == "inline"

    # 磁盘文件落到 storage/tenants/{tenant}/images/{yyyy-mm}/ 目录
    key = registry._redis.make_key("uploaded_file", ref.file_id)
    stored_path = registry._redis.hget(key, "path")
    assert stored_path is not None
    assert "tenants/tenant_a/images/" in str(stored_path).replace("\\", "/")
    assert Path(stored_path).exists()

    # Redis 元信息字段齐全
    assert registry._redis.hget(key, "type") == "image"
    assert registry._redis.hget(key, "source") == "tool_generated"
    assert registry._redis.hget(key, "usage") == "inline"
    assert registry._redis.hget(key, "user_id") == "user_1"
    assert registry._redis.hget(key, "registered_at") is not None


# ============================================================
# 2. test_register_move_replaces_source
# ============================================================

@pytest.mark.asyncio
async def test_register_move_replaces_source(registry, tmp_path):
    """移动注册：源文件被移走，磁盘文件出现在目标目录。"""
    src = _make_1x1_png(tmp_path / "movable.png")

    ref = await registry.register(
        source_path=src,
        tenant_id="tenant_b",
        display_name="移动图.png",
        move=True,
    )

    # 源文件已不存在
    assert not src.exists(), "move 模式下源文件应被移走"

    # 目标文件存在
    key = registry._redis.make_key("uploaded_file", ref.file_id)
    stored_path = Path(registry._redis.hget(key, "path"))
    assert stored_path.exists()


# ============================================================
# 3. test_register_pillow_failure_does_not_raise
# ============================================================

@pytest.mark.asyncio
async def test_register_pillow_failure_does_not_raise(registry, tmp_path):
    """损坏图片注册不抛异常，width/height 为 None，但文件元信息正常写入。"""
    src = _make_corrupt_image(tmp_path / "broken.png")

    ref = await registry.register(
        source_path=src,
        tenant_id="tenant_c",
        display_name="损坏图.png",
    )

    # 宽高为 None（Pillow 读取失败）
    assert ref.width is None
    assert ref.height is None

    # 但 size_bytes / file_id / download_url 正常
    assert ref.size_bytes > 0
    assert ref.file_id.startswith("file_")
    assert ref.download_url == f"/api/files/{ref.file_id}/download"


# ============================================================
# 4. test_register_knowledge_base_permanent_ttl
# ============================================================

@pytest.mark.asyncio
async def test_register_knowledge_base_permanent_ttl(registry, tmp_path):
    """source=knowledge_base 时不调用 expire（永久保留语义）。"""
    src = _make_1x1_png(tmp_path / "kb_cover.png")

    # 用 spy 监控 expire 调用
    expire_calls: List[Any] = []
    original_expire = registry._redis.expire

    def _spy_expire(key, seconds):
        expire_calls.append((key, seconds))
        return original_expire(key, seconds)

    registry._redis.expire = _spy_expire

    ref = await registry.register(
        source_path=src,
        tenant_id="tenant_kb",
        display_name="景点封面.png",
        source="knowledge_base",
        usage="thumbnail",
        linked_doc_id=42,
    )

    # knowledge_base 来源：不应调用 expire（永久）
    assert expire_calls == [], (
        f"knowledge_base 来源不应调用 expire，实际调用：{expire_calls}"
    )

    # linked_doc_id 应写入 Redis
    key = registry._redis.make_key("uploaded_file", ref.file_id)
    assert registry._redis.hget(key, "linked_doc_id") == "42"

    # 反过来 tool_generated 应当调用 expire
    src2 = _make_1x1_png(tmp_path / "tool.png")
    expire_calls.clear()
    await registry.register(
        source_path=src2,
        tenant_id="tenant_kb",
        display_name="工具图.png",
        source="tool_generated",
    )
    assert len(expire_calls) == 1, (
        f"tool_generated 来源应调用 expire 一次，实际：{expire_calls}"
    )
    assert expire_calls[0][1] == ImageRegistry.DEFAULT_TTL


# ============================================================
# 5. test_resolve_local_path_hits_redis
# ============================================================

@pytest.mark.asyncio
async def test_resolve_local_path_hits_redis(registry, tmp_path):
    """resolve_local_path 返回 register 时写入的路径，且文件存在。"""
    src = _make_1x1_png(tmp_path / "resolve_me.png")

    ref = await registry.register(
        source_path=src,
        tenant_id="tenant_d",
        display_name="待解析.png",
    )

    resolved = await registry.resolve_local_path(ref)
    assert isinstance(resolved, Path)
    assert resolved.exists()
    # 与 register 写入的路径一致
    key = registry._redis.make_key("uploaded_file", ref.file_id)
    assert str(resolved) == registry._redis.hget(key, "path")


@pytest.mark.asyncio
async def test_resolve_local_path_missing_redis_key_raises(registry):
    """Redis 中找不到 file_id 时 resolve 抛 KeyError。"""
    ref = ImageRef(
        file_id="file_notexist12345",
        download_url="/api/files/file_notexist12345/download",
        display_name="不存在的.png",
        source="tool_generated",
    )
    with pytest.raises(KeyError):
        await registry.resolve_local_path(ref)


@pytest.mark.asyncio
async def test_resolve_local_path_missing_disk_raises(registry, tmp_path):
    """Redis 有记录但磁盘文件被删时 resolve 抛 FileNotFoundError。"""
    src = _make_1x1_png(tmp_path / "will_be_deleted.png")
    ref = await registry.register(
        source_path=src,
        tenant_id="tenant_e",
        display_name="会被删的.png",
    )

    # 删除磁盘文件
    key = registry._redis.make_key("uploaded_file", ref.file_id)
    stored_path = Path(registry._redis.hget(key, "path"))
    stored_path.unlink()

    with pytest.raises(FileNotFoundError):
        await registry.resolve_local_path(ref)


# ============================================================
# 6. test_fetch_to_local_caches_by_url_hash
# ============================================================

@pytest.mark.asyncio
async def test_fetch_to_local_caches_by_url_hash(registry, tmp_path):
    """同 URL 第二次调用 fetch_to_local 走缓存，不重新下载。"""
    url = "https://example.com/photo.jpg"

    # mock _download_with_size_check：记录调用次数，写一个真实 png 到目标路径
    download_calls: List[str] = []

    async def _fake_download(url_arg, dest_path, timeout):
        download_calls.append(url_arg)
        _make_1x1_png(Path(dest_path))

    registry._download_with_size_check = _fake_download  # type: ignore[assignment]

    # 第一次：实际下载
    ref1 = await registry.fetch_to_local(url, tenant_id="tenant_f")
    assert len(download_calls) == 1
    assert ref1.source == "web_fetch"
    assert ref1.usage == "embedded"
    assert ref1.source_ref == url

    # 第二次同 URL：走缓存，不再下载
    ref2 = await registry.fetch_to_local(url, tenant_id="tenant_f")
    assert len(download_calls) == 1, "同 URL 第二次应命中缓存，不应再下载"
    assert ref2.file_id == ref1.file_id


# ============================================================
# 7. test_fetch_to_local_rejects_oversize
# ============================================================

@pytest.mark.asyncio
async def test_fetch_to_local_rejects_oversize(registry, tmp_path):
    """超过 MAX_FETCH_SIZE（10MB）的响应拒绝并抛 ValueError。"""
    url = "https://example.com/huge.jpg"

    async def _fake_oversize_download(url_arg, dest_path, timeout):
        # 模拟 stream 写入超过上限的逻辑
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(b"x" * 1024)  # 先写一点
        raise ValueError(
            f"图片累计大小 {ImageRegistry.MAX_FETCH_SIZE + 1} 超过上限 "
            f"{ImageRegistry.MAX_FETCH_SIZE} 字节"
        )

    registry._download_with_size_check = _fake_oversize_download  # type: ignore[assignment]

    with pytest.raises(ValueError, match="超过上限"):
        await registry.fetch_to_local(url, tenant_id="tenant_g")

    # 失败后不应写入 URL 缓存（下次同 URL 仍会重新尝试）
    import hashlib
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache_key = registry._redis.make_key("image_fetch_url", url_hash)
    assert registry._redis.get(cache_key) is None, "下载失败时不应写 URL 缓存"


@pytest.mark.asyncio
async def test_download_rejects_oversize_via_content_length(registry, tmp_path):
    """Content-Length 预检：响应头声明的大小超限时，应在读取 body 前就抛 ValueError。

    回归用例：原实现把 `int(cl)` 转换失败和「超过上限」都用 ValueError 抛/捕，
    导致「超过上限」被「非数字」分支吞掉，预检完全失效。本用例直接调
    `_download_with_size_check`，mock httpx stream 返回超大 Content-Length，
    验证 ValueError 立即抛出，且不写入任何字节。
    """
    url = "https://example.com/huge_by_header.jpg"
    dest = tmp_path / "out.bin"

    # 构造 mock response：Content-Length 声明超大，body 不应被读
    mock_response = MagicMock()
    mock_response.headers = {"content-length": str(ImageRegistry.MAX_FETCH_SIZE + 1)}
    mock_response.raise_for_status = MagicMock()
    # aiter_bytes 不应被调用（预检应在前）
    mock_response.aiter_bytes = AsyncMock(side_effect=AssertionError(
        "Content-Length 预检失效：不应读取 body"
    ))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.stream = MagicMock(return_value=_AsyncCtxManager(mock_response))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="超过上限"):
            await registry._download_with_size_check(str(url), str(dest), timeout=10)

    # 预检失败：不应创建任何文件
    assert not dest.exists(), "Content-Length 预检失败时不应写入磁盘"


class _AsyncCtxManager:
    """简单 async context manager 包装，用于 mock httpx.stream() 返回值。"""
    def __init__(self, value):
        self.value = value
    async def __aenter__(self):
        return self.value
    async def __aexit__(self, *args):
        return None


# ============================================================
# 8. test_fetch_to_local_timeout
# ============================================================

@pytest.mark.asyncio
async def test_fetch_to_local_timeout(registry, tmp_path):
    """httpx 超时（或网络异常）时 fetch_to_local 应向上抛出，不吞异常。"""
    import httpx
    url = "https://slow.example.com/timeout.jpg"

    async def _fake_timeout_download(url_arg, dest_path, timeout):
        raise httpx.TimeoutException("simulated timeout", request=None)

    registry._download_with_size_check = _fake_timeout_download  # type: ignore[assignment]

    with pytest.raises(httpx.TimeoutException):
        await registry.fetch_to_local(url, tenant_id="tenant_h", timeout=1)


@pytest.mark.asyncio
async def test_fetch_to_local_rejects_invalid_url(registry):
    """非法 URL（非 http(s)）直接抛 ValueError，不发请求。"""
    with pytest.raises(ValueError, match="非法 URL"):
        await registry.fetch_to_local("ftp://x/y.jpg", tenant_id="tenant_i")
    with pytest.raises(ValueError, match="非法 URL"):
        await registry.fetch_to_local("", tenant_id="tenant_i")


# ============================================================
# 9. test_compat_with_cp_file_download_route
# ============================================================

@pytest.mark.asyncio
async def test_compat_with_cp_file_download_route(registry, tmp_path):
    """注册后的 download_url 格式严格匹配 cp 的 /api/files/{file_id}/download。

    这是 Phase 0 的核心兼容性约束：ImageRegistry 与 cp 共用 Redis key
    命名空间 `uploaded_file:{file_id}`，所以现有 /api/files/{id}/download
    路由能直接下载 ImageRegistry 注册的文件（无需新增路由）。
    本用例只验证 URL 格式 + Redis key 命名空间一致，不真实启动 FastAPI。
    """
    src = _make_1x1_png(tmp_path / "compat.png")

    ref = await registry.register(
        source_path=src,
        tenant_id="tenant_compat",
        display_name="兼容性测试.png",
        source="tool_generated",
    )

    # download_url 格式精确匹配
    assert ref.download_url == f"/api/files/{ref.file_id}/download"

    # Redis key 与 cp_tool 同命名空间：uploaded_file:{file_id}
    key = registry._redis.make_key("uploaded_file", ref.file_id)
    assert key.endswith(f"uploaded_file:{ref.file_id}")

    # 必填字段齐全（cp 的 /api/files/{id}/download 处理器要读这些字段）
    data = registry._redis.hgetall(key)
    assert data.get("file_id") == ref.file_id
    assert data.get("name")  # cp 用 name 作为下载文件名
    assert data.get("path")  # cp 用 path 找磁盘文件
    assert data.get("type") == "image"  # ImageRegistry 标识为 image（cp 是 file）


# ============================================================
# 10. test_cleanup_temp_only_removes_inline_embedded_tool
# ============================================================

@pytest.mark.asyncio
async def test_cleanup_temp_only_removes_inline_embedded_tool(registry, tmp_path):
    """cleanup_temp 仅清理临时图片（tool/web_fetch + inline/embedded + 超时）。

    知识库永久图（source=knowledge_base）与未超时的临时图不受影响。
    """
    # 1. 知识库永久图：source=knowledge_base + usage=thumbnail
    kb_src = _make_1x1_png(tmp_path / "kb.png")
    kb_ref = await registry.register(
        source_path=kb_src,
        tenant_id="tenant_clean",
        display_name="景点封面.png",
        source="knowledge_base",
        usage="thumbnail",
    )

    # 2. 工具生成图（超时）：source=tool_generated + usage=inline
    old_tool_src = _make_1x1_png(tmp_path / "old_tool.png")
    old_tool_ref = await registry.register(
        source_path=old_tool_src,
        tenant_id="tenant_clean",
        display_name="旧工具图.png",
        source="tool_generated",
        usage="inline",
    )
    # 手动把 registered_at 改成 25 小时前，模拟超时
    old_key = registry._redis.make_key("uploaded_file", old_tool_ref.file_id)
    old_ts = (datetime.now() - timedelta(hours=25)).isoformat()
    registry._redis.hset(old_key, "registered_at", old_ts)

    # 3. 工具生成图（未超时）：source=tool_generated + usage=inline，刚注册
    fresh_tool_src = _make_1x1_png(tmp_path / "fresh_tool.png")
    fresh_tool_ref = await registry.register(
        source_path=fresh_tool_src,
        tenant_id="tenant_clean",
        display_name="新工具图.png",
        source="tool_generated",
        usage="inline",
    )

    # 4. web_fetch 嵌入图（超时）：source=web_fetch + usage=embedded
    old_fetch_src = _make_1x1_png(tmp_path / "old_fetch.png")
    old_fetch_ref = await registry.register(
        source_path=old_fetch_src,
        tenant_id="tenant_clean",
        display_name="旧抓取图.png",
        source="web_fetch",
        usage="embedded",
    )
    fetch_key = registry._redis.make_key("uploaded_file", old_fetch_ref.file_id)
    registry._redis.hset(fetch_key, "registered_at", old_ts)

    # 5. 附件类工具图（超时但 usage=attachment，不应被清理）
    attach_src = _make_1x1_png(tmp_path / "attach.png")
    attach_ref = await registry.register(
        source_path=attach_src,
        tenant_id="tenant_clean",
        display_name="附件图.png",
        source="tool_generated",
        usage="attachment",
    )
    attach_key = registry._redis.make_key("uploaded_file", attach_ref.file_id)
    registry._redis.hset(attach_key, "registered_at", old_ts)

    # 执行清理（默认 older_than_hours=24）
    cleaned = await registry.cleanup_temp(older_than_hours=24)

    # 应该清理 2 个：old_tool_ref + old_fetch_ref
    assert cleaned == 2, f"期望清理 2 个，实际清理 {cleaned}"

    # 知识库永久图仍在
    assert await registry.get_ref_by_file_id(kb_ref.file_id) is not None
    kb_path = Path(registry._redis.hget(
        registry._redis.make_key("uploaded_file", kb_ref.file_id), "path"
    ))
    assert kb_path.exists(), "知识库永久图磁盘文件应保留"

    # 未超时的工具图仍在
    assert await registry.get_ref_by_file_id(fresh_tool_ref.file_id) is not None

    # 超时的工具图和 web_fetch 图被清理（Redis key + 磁盘文件）
    assert await registry.get_ref_by_file_id(old_tool_ref.file_id) is None
    assert await registry.get_ref_by_file_id(old_fetch_ref.file_id) is None

    # usage=attachment 的图即使超时也不被清理
    assert await registry.get_ref_by_file_id(attach_ref.file_id) is not None, (
        "usage=attachment 的图不应被 cleanup_temp 清理"
    )


# ============================================================
# 额外：get_image_registry 惰性单例
# ============================================================

def test_get_image_registry_returns_singleton():
    """get_image_registry 返回同一个实例（惰性单例）。"""
    # 重置单例（避免其他测试干扰）
    import src.core.image_asset as mod
    mod._registry_instance = None

    reg1 = get_image_registry()
    reg2 = get_image_registry()
    assert reg1 is reg2
    assert isinstance(reg1, ImageRegistry)
    # 清理，避免污染其他测试
    mod._registry_instance = None
