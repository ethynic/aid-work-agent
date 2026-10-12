"""backfill_file_assets 补录脚本纯函数与自愈双写测试"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backfill_file_assets import (  # noqa: E402
    extract_names_from_channel_attachments,
    extract_names_from_message_metadata,
    is_stored_name_degraded,
    iter_storage_files,
)

pytestmark = [pytest.mark.tools]


class TestExtractFromMessageMetadata:
    def test_extracts_all_attachment_kinds(self):
        import json
        meta = json.dumps({
            "attachments": [{"file_id": "file_2ef39b21b91f", "name": "04_标准客房.xlsx"}],
            "images": [{"file_id": "file_aaa111222333", "display_name": "图表.png"}],
            "downloadableFiles": [{"file_id": "file_bbb111222333", "file_name": "清单.xlsx"}],
        })
        names = extract_names_from_message_metadata(meta)
        assert names == {
            "file_2ef39b21b91f": "04_标准客房.xlsx",
            "file_aaa111222333": "图表.png",
            "file_bbb111222333": "清单.xlsx",
        }

    def test_bad_json_returns_empty(self):
        assert extract_names_from_message_metadata("not json") == {}
        assert extract_names_from_message_metadata(None) == {}
        assert extract_names_from_message_metadata('{"attachments": "not-a-list"}') == {}
        assert extract_names_from_message_metadata('["flat-array"]') == {}

    def test_skips_non_file_id_entries(self):
        meta = '{"attachments": [{"file_id": "not-a-file-id", "name": "x.xlsx"}, {"name": "缺file_id"}]}'
        assert extract_names_from_message_metadata(meta) == {}

    def test_first_name_wins(self):
        meta = '{"attachments": [{"file_id": "file_2ef39b21b91f", "name": "第一个.xlsx"}, {"file_id": "file_2ef39b21b91f", "name": "第二个.xlsx"}]}'
        assert extract_names_from_message_metadata(meta) == {"file_2ef39b21b91f": "第一个.xlsx"}


class TestExtractFromChannelAttachments:
    def test_extracts_names(self):
        raw = '[{"file_id": "file_111222333444", "name": "报价.xlsx", "type": "file"}]'
        assert extract_names_from_channel_attachments(raw) == {"file_111222333444": "报价.xlsx"}

    def test_bad_json_returns_empty(self):
        assert extract_names_from_channel_attachments("broken") == {}
        assert extract_names_from_channel_attachments(None) == {}


class TestIsStoredNameDegraded:
    def test_storage_names_are_degraded(self):
        assert is_stored_name_degraded("file_2ef39b21b91f.xlsx") is True
        assert is_stored_name_degraded("file_2ef39b21b91f") is True
        assert is_stored_name_degraded("") is True
        assert is_stored_name_degraded(None) is True

    def test_visible_names_are_not_degraded(self):
        assert is_stored_name_degraded("04_标准客房卫生间_洁具与五金.xlsx") is False
        assert is_stored_name_degraded("report.pdf") is False


class TestIterStorageFiles:
    def test_yields_file_id_files_with_tenant(self, tmp_path):
        scene = tmp_path / "tenants" / "tenant_a" / "conversation"
        scene.mkdir(parents=True)
        (scene / "file_abc123def456.xlsx").write_text("x")
        (scene / "04_标准客房卫生间.xlsx").write_text("x")  # 非存储名，跳过
        sub = scene / "2026-08"
        sub.mkdir()
        (sub / "file_bbb111222333.png").write_text("x")

        results = list(iter_storage_files(tmp_path))
        assert {fid for _, fid, _ in results} == {"file_abc123def456", "file_bbb111222333"}
        by_fid = {fid: (tid, path) for tid, fid, path in results}
        assert by_fid["file_abc123def456"][0] == "tenant_a"
        assert by_fid["file_abc123def456"][1].name == "file_abc123def456.xlsx"
        assert by_fid["file_bbb111222333"][0] == "tenant_a"
        assert by_fid["file_bbb111222333"][1].parent.name == "2026-08"

    def test_missing_tenants_dir(self, tmp_path):
        assert list(iter_storage_files(tmp_path)) == []


class TestDiskHealPersistsToDB:
    """find_uploaded_file_on_disk 自愈命中后同步 upsert file_assets"""

    def test_hit_upserts_db(self, tmp_path, monkeypatch):
        from src.core.storage import find_uploaded_file_on_disk

        scene = tmp_path / "tenants" / "tenant_a" / "conversation"
        scene.mkdir(parents=True)
        (scene / "file_abc123def456.xlsx").write_text("x")
        monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path))

        with patch("src.core.redis_client.redis_client.hgetall", return_value={}), \
             patch("src.core.redis_client.redis_client.hset", return_value=1), \
             patch("src.core.redis_client.redis_client.expire", return_value=True), \
             patch("src.core.redis_client.redis_client.make_key",
                   side_effect=lambda prefix, fid: f"{prefix}:{fid}"), \
             patch("src.services.files.asset_store.upsert_file_asset") as mock_upsert:
            info = find_uploaded_file_on_disk("file_abc123def456")

        assert info is not None
        mock_upsert.assert_called_once()
        meta = mock_upsert.call_args[0][0]
        assert meta["file_id"] == "file_abc123def456"
        assert meta["name"] == "file_abc123def456.xlsx"  # 磁盘名（name 保护由 upsert SQL 保证）
        assert mock_upsert.call_args[1]["source"] == "disk_heal"

    def test_hit_without_register_skips_db(self, tmp_path, monkeypatch):
        from src.core.storage import find_uploaded_file_on_disk

        scene = tmp_path / "tenants" / "tenant_a" / "conversation"
        scene.mkdir(parents=True)
        (scene / "file_abc123def456.xlsx").write_text("x")
        monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path))

        with patch("src.core.redis_client.redis_client.hgetall", return_value={}), \
             patch("src.services.files.asset_store.upsert_file_asset") as mock_upsert:
            info = find_uploaded_file_on_disk("file_abc123def456", register_to_redis=False)

        assert info is not None
        mock_upsert.assert_not_called()
