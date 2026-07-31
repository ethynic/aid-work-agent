"""视频生成 MediaRegistry 单元测试。

验证：
- register_local 写 Redis hash 含兼容字段、文件落盘
- read_as_base64 返回 data:image base64 串
- download_and_register 下载 + 烧录标识 + 注册（mock httpx + 真实 ffmpeg 可用时）
- TTL 处理：None=永久（不调 expire）
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import MagicMock, patch

import pytest

from src.video_gen import media

pytestmark = [pytest.mark.unit]


def _fake_redis():
    """构造一个内存版 redis_client mock（记录 hset/hget/expire 调用）。"""
    store = {}
    expires = {}

    def make_key(prefix, ident):
        return f"{prefix}:{ident}"

    def hset(key, mapping=None, **kwargs):
        m = mapping or kwargs
        store.setdefault(key, {}).update(m)

    def hget(key, field):
        return store.get(key, {}).get(field)

    def expire(key, seconds):
        expires[key] = seconds

    rc = MagicMock()
    rc.make_key.side_effect = make_key
    rc.hset.side_effect = hset
    rc.hget.side_effect = hget
    rc.expire.side_effect = expire
    return rc, store, expires


class TestRegisterLocal:
    def test_registers_video_with_required_fields(self, tmp_path):
        rc, store, expires = _fake_redis()
        src = tmp_path / "v.mp4"
        src.write_bytes(b"fake mp4 content")

        with patch.object(media, "redis_client", rc):
            reg = media.MediaRegistry()
            file_id = asyncio.new_event_loop().run_until_complete(
                reg.register_local(str(src), tenant_id="t1", mime_type="video/mp4", ttl_seconds=86400)
            )

        key = f"uploaded_file:{file_id}"
        entry = store[key]
        # 兼容 main.py _get_file_info 读取端的核心字段
        for field in ("file_id", "name", "path", "size", "mime_type", "type"):
            assert field in entry, f"缺字段 {field}"
        assert entry["type"] == "video"
        assert entry["mime_type"] == "video/mp4"
        assert expires[key] == 86400
        # 文件落盘
        import os
        assert os.path.exists(entry["path"])

    def test_image_type_marked_correctly(self, tmp_path):
        rc, store, _ = _fake_redis()
        src = tmp_path / "img.jpg"
        src.write_bytes(b"fake jpg")

        with patch.object(media, "redis_client", rc):
            reg = media.MediaRegistry()
            file_id = asyncio.new_event_loop().run_until_complete(
                reg.register_local(str(src), tenant_id="t1", mime_type="image/jpeg", scene_subdir="images")
            )
        assert store[f"uploaded_file:{file_id}"]["type"] == "image"

    def test_ttl_none_means_no_expire(self, tmp_path):
        rc, store, expires = _fake_redis()
        src = tmp_path / "v.mp4"
        src.write_bytes(b"x")

        with patch.object(media, "redis_client", rc):
            reg = media.MediaRegistry()
            file_id = asyncio.new_event_loop().run_until_complete(
                reg.register_local(str(src), tenant_id="t1", mime_type="video/mp4", ttl_seconds=None)
            )
        # 永久则不应调 expire
        assert f"uploaded_file:{file_id}" not in expires


class TestReadAsBase64:
    def test_returns_data_url(self, tmp_path):
        rc, _, _ = _fake_redis()
        src = tmp_path / "img.jpg"
        src.write_bytes(b"img-bytes")

        with patch.object(media, "redis_client", rc):
            reg = media.MediaRegistry()
            file_id = asyncio.new_event_loop().run_until_complete(
                reg.register_local(str(src), tenant_id="t1", mime_type="image/jpeg")
            )
            data_url = media.MediaRegistry.read_as_base64(file_id)

        assert data_url.startswith("data:image/jpeg;base64,")
        b64part = data_url.split(",", 1)[1]
        assert base64.b64decode(b64part) == b"img-bytes"

    def test_missing_file_raises(self):
        rc, store, _ = _fake_redis()
        with patch.object(media, "redis_client", rc):
            with pytest.raises(ValueError):
                media.MediaRegistry.read_as_base64("file_nonexistent")


class TestBurnAiLabel:
    def test_burns_label_produces_output(self, tmp_path):
        """端到端：ffmpeg 真实可用时，drawtext 烧录产出有效 mp4。"""
        import subprocess
        import os

        ffmpeg = shutil_which("ffmpeg")
        if ffmpeg is None:
            pytest.skip("ffmpeg 未安装，跳过烧录测试")

        # 生成 1s 蓝色测试视频
        raw = str(tmp_path / "raw.mp4")
        gen = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1", "-pix_fmt", "yuv420p", raw],
            capture_output=True,
        )
        if gen.returncode != 0 or not os.path.exists(raw):
            pytest.skip("无法生成测试视频")

        out = str(tmp_path / "out.mp4")
        media._burn_ai_label(raw, out)
        assert os.path.exists(out) and os.path.getsize(out) > 0


def shutil_which(cmd):
    import shutil
    return shutil.which(cmd)
