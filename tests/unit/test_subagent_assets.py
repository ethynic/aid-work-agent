"""租户数字员工资产克隆单测（模板文件磁盘/元数据 + 知识栏目，全 mock DB）"""

from unittest.mock import patch

from src.services import subagent_assets as assets


class TestCopyTemplateFiles:
    def test_src_equals_dst_returns_zero(self):
        assert assets.copy_template_files("t1", "sales", "sales") == 0

    def test_empty_source_returns_zero(self):
        with patch("src.db.subagent_template_file_db.SubagentTemplateFileDB.get", return_value=[]):
            assert assets.copy_template_files("t1", "sales", "dst") == 0

    def test_clones_disk_and_metadata(self, tmp_path):
        (tmp_path / "file_aaa111.docx").write_bytes(b"hello")
        src_files = [{
            "name": "标准报价单", "file_id": "file_aaa111",
            "original_name": "quote.docx", "mime_type": "application/msword",
            "size_bytes": 5,
        }]
        set_calls = {}

        def fake_set(tid, dst, files):
            set_calls["dst"] = dst
            set_calls["files"] = files
            return True

        with patch("src.db.subagent_template_file_db.SubagentTemplateFileDB.get", return_value=src_files), \
             patch("src.db.subagent_template_file_db.SubagentTemplateFileDB.set", side_effect=fake_set), \
             patch.object(assets, "templates_dir", return_value=tmp_path), \
             patch.object(assets, "persist_template_meta") as meta:
            n = assets.copy_template_files("t1", "sales", "dst")
        assert n == 1
        assert set_calls["dst"] == "dst"
        new = set_calls["files"][0]
        assert new["file_id"] != "file_aaa111"
        assert new["file_id"].startswith("file_")
        assert new["name"] == "标准报价单"
        # 磁盘副本以新 file_id 落盘
        assert (tmp_path / f"{new['file_id']}.docx").exists()
        meta.assert_called_once()

    def test_missing_source_disk_file_skipped(self, tmp_path):
        src_files = [{"name": "x", "file_id": "file_missing", "original_name": "x.docx",
                      "mime_type": "application/msword"}]
        with patch("src.db.subagent_template_file_db.SubagentTemplateFileDB.get", return_value=src_files), \
             patch("src.db.subagent_template_file_db.SubagentTemplateFileDB.set") as set_m, \
             patch.object(assets, "templates_dir", return_value=tmp_path):
            n = assets.copy_template_files("t1", "sales", "dst")
        assert n == 0
        set_m.assert_not_called()


class TestCopyKnowledgeSources:
    def test_src_equals_dst_returns_zero(self):
        assert assets.copy_knowledge_sources("t1", "sales", "sales") == 0

    def test_empty_source_returns_zero(self):
        with patch("src.db.subagent_knowledge_source_db.SubagentKnowledgeSourceDB.get", return_value=[]):
            assert assets.copy_knowledge_sources("t1", "sales", "dst") == 0

    def test_copies_items_verbatim(self):
        sources = [{"source_type": "product", "display_name": "产品库"}]
        with patch("src.db.subagent_knowledge_source_db.SubagentKnowledgeSourceDB.get", return_value=sources), \
             patch("src.db.subagent_knowledge_source_db.SubagentKnowledgeSourceDB.set", return_value=True) as set_m:
            n = assets.copy_knowledge_sources("t1", "sales", "dst")
        assert n == 1
        set_m.assert_called_once_with("t1", "dst", sources)
