"""
Phase 5 渠道媒体文件租户隔离单测

覆盖 wecom / dingtalk / wecom_kf 三个渠道的 tenant_id 注入与落盘目录解析：
- media / renderer 的 `set_tenant_id` + `_resolve_save_dir`
- adapter 的 `set_tenant_id`（转发到 media / renderer）

约定：
- 有 tenant_id 时走 `storage/tenants/{tenant_id}/conversation/`（ensure_tenant_storage_dir）
- 无 tenant_id 时回退到各自的旧 upload_dir（单租户模式兜底）
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.channels]


@pytest.fixture
def isolated_tenants_root(tmp_path: Path, monkeypatch):
    """把 storage._TENANTS_ROOT 重定向到 tmp_path，避免污染项目目录"""
    from src.core import storage as storage_mod
    fake_root = str(tmp_path / "tenants")
    monkeypatch.setattr(storage_mod, "_TENANTS_ROOT", fake_root)
    return tmp_path


def _token_getter():
    async def _get_token():
        return "test_access_token"

    return _get_token


# ============================================================
# WeComMedia
# ============================================================


class TestWeComMediaTenantStorage:
    def _make_media(self, upload_dir):
        from src.channels.wecom.media import WeComMedia
        return WeComMedia(access_token_getter=_token_getter(), upload_dir=upload_dir)

    def test_resolve_save_dir_with_tenant(self, isolated_tenants_root, tmp_path):
        media = self._make_media(str(tmp_path / "uploads_wecom"))
        media.set_tenant_id("tenant_wc")
        result = media._resolve_save_dir()
        p = Path(result)
        assert p.name == "conversation"
        assert p.parent.name == "wc"  # tenant_wc -> wc（Phase 8 前缀治理）
        assert p.parent.parent.name == "tenants"
        assert p.is_dir()

    def test_resolve_save_dir_without_tenant(self, isolated_tenants_root, tmp_path):
        upload_dir = str(tmp_path / "uploads_wecom")
        media = self._make_media(upload_dir)
        media.set_tenant_id("")
        assert media._resolve_save_dir() == upload_dir

    def test_set_tenant_id_empty_string(self, isolated_tenants_root, tmp_path):
        media = self._make_media(str(tmp_path / "uploads_wecom"))
        media.set_tenant_id("")
        assert media.tenant_id == ""


# ============================================================
# DingTalkMedia
# ============================================================


class TestDingTalkMediaTenantStorage:
    def _make_media(self, upload_dir):
        from src.channels.dingtalk.media import DingTalkMedia
        return DingTalkMedia(access_token_getter=_token_getter(), upload_dir=upload_dir)

    def test_resolve_save_dir_with_tenant(self, isolated_tenants_root, tmp_path):
        media = self._make_media(str(tmp_path / "uploads_dingtalk"))
        media.set_tenant_id("tenant_dt")
        result = media._resolve_save_dir()
        p = Path(result)
        assert p.name == "conversation"
        assert p.parent.name == "dt"  # tenant_dt -> dt（Phase 8 前缀治理）
        assert p.parent.parent.name == "tenants"
        assert p.is_dir()

    def test_resolve_save_dir_without_tenant(self, isolated_tenants_root, tmp_path):
        upload_dir = str(tmp_path / "uploads_dingtalk")
        media = self._make_media(upload_dir)
        media.set_tenant_id("")
        assert media._resolve_save_dir() == upload_dir


# ============================================================
# WeComAdapter
# ============================================================


class TestWeComAdapterTenantStorage:
    @pytest.mark.asyncio
    async def test_set_tenant_id_forwards_to_media(self, isolated_tenants_root, tmp_path):
        from src.channels.wecom.adapter import WeComAdapter
        adapter = WeComAdapter(
            corp_id="ww_test",
            agent_id="1000001",
            secret="test_secret",
            media_upload_dir=str(tmp_path / "uploads_wecom"),
        )
        await adapter.set_tenant_id("tenant_wc_adapter")
        assert adapter._tenant_id == "tenant_wc_adapter"
        assert adapter.media.tenant_id == "tenant_wc_adapter"

    @pytest.mark.asyncio
    async def test_set_tenant_id_empty(self, isolated_tenants_root, tmp_path):
        from src.channels.wecom.adapter import WeComAdapter
        adapter = WeComAdapter(
            corp_id="ww_test",
            agent_id="1000001",
            secret="test_secret",
            media_upload_dir=str(tmp_path / "uploads_wecom"),
        )
        await adapter.set_tenant_id("")
        assert adapter._tenant_id == ""
        assert adapter.media.tenant_id == ""


# ============================================================
# DingTalkAdapter
# ============================================================


class TestDingTalkAdapterTenantStorage:
    @pytest.mark.asyncio
    async def test_set_tenant_id_forwards_to_media(self, isolated_tenants_root, tmp_path):
        from src.channels.dingtalk.adapter import DingTalkAdapter
        adapter = DingTalkAdapter(
            app_key="ding_test",
            app_secret="test_secret",
            media_upload_dir=str(tmp_path / "uploads_dingtalk"),
        )
        await adapter.set_tenant_id("tenant_dt_adapter")
        assert adapter._tenant_id == "tenant_dt_adapter"
        assert adapter.media.tenant_id == "tenant_dt_adapter"


# ============================================================
# WeComKfAdapter
# ============================================================


class TestWeComKfAdapterTenantStorage:
    def _make_adapter(self, media_upload_dir):
        from src.channels.wecom_kf.adapter import WeComKfAdapter
        return WeComKfAdapter(
            corp_id="test_corp",
            secret="test_secret_xxxxxxxxxxxxxxxx",
            token="test_token",
            encoding_aes_key="",
            kf_account=[{"open_kfid": "kfXXX", "name": "测试客服"}],
            media_upload_dir=media_upload_dir,
        )

    @pytest.mark.asyncio
    async def test_resolve_media_dir_with_tenant(self, isolated_tenants_root, tmp_path):
        adapter = self._make_adapter(str(tmp_path / "uploads_wecom_kf"))
        await adapter.set_tenant_id("tenant_kf")
        assert adapter._tenant_id == "tenant_kf"
        result = adapter._resolve_media_dir()
        p = Path(result)
        assert p.name == "conversation"
        assert p.parent.name == "kf"  # tenant_kf -> kf（Phase 8 前缀治理）
        assert p.parent.parent.name == "tenants"
        assert p.is_dir()

    @pytest.mark.asyncio
    async def test_resolve_media_dir_without_tenant(self, isolated_tenants_root, tmp_path):
        media_upload_dir = str(tmp_path / "uploads_wecom_kf")
        adapter = self._make_adapter(media_upload_dir)
        await adapter.set_tenant_id("")
        assert adapter._resolve_media_dir() == media_upload_dir


# ============================================================
# WeComKfRenderer
# ============================================================


class TestWeComKfRendererTenantStorage:
    def _make_renderer(self, upload_dir):
        from src.channels.wecom_kf.renderer import WeComKfRenderer
        return WeComKfRenderer(upload_dir=upload_dir)

    def test_resolve_save_dir_with_tenant(self, isolated_tenants_root, tmp_path):
        renderer = self._make_renderer(str(tmp_path / "uploads_wecom_kf"))
        renderer.set_tenant_id("tenant_kf_render")
        result = renderer._resolve_save_dir()
        p = Path(result)
        assert p.name == "conversation"
        assert p.parent.name == "kf_render"  # tenant_kf_render -> kf_render（Phase 8 前缀治理）
        assert p.parent.parent.name == "tenants"
        assert p.is_dir()

    def test_resolve_save_dir_without_tenant(self, isolated_tenants_root, tmp_path):
        upload_dir = str(tmp_path / "uploads_wecom_kf")
        renderer = self._make_renderer(upload_dir)
        renderer.set_tenant_id("")
        assert renderer._resolve_save_dir() == upload_dir

    def test_init_with_tenant_id(self, isolated_tenants_root, tmp_path):
        """构造时直接传 tenant_id 也应生效（adapter 懒加载 renderer 时传入）"""
        from src.channels.wecom_kf.renderer import WeComKfRenderer
        renderer = WeComKfRenderer(
            upload_dir=str(tmp_path / "uploads_wecom_kf"),
            tenant_id="tenant_kf_init",
        )
        result = renderer._resolve_save_dir()
        p = Path(result)
        assert p.parent.name == "kf_init"  # tenant_kf_init -> kf_init（Phase 8 前缀治理）
        assert p.name == "conversation"
