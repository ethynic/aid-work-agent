"""
知识库文档下载权限校验测试。

验证 _can_download_document：本租户放行、无主文档放行、共享范围校验（§6.5 下载漏洞修复）。
验证 _resolve_schema_doc_file：schema 文档（[数据表] 前缀）源文件路径兜底解析。
"""
from unittest.mock import patch

from src.knowledge.api import (
    _can_download_document,
    _parse_doc_metadata,
    _resolve_schema_doc_file,
)


def test_can_download_own_tenant():
    """文档归属当前租户 -> 放行"""
    assert _can_download_document({"tenant_id": "B", "source_type": "product"}, "B") is True


def test_can_download_ownerless_without_tenant_context():
    """无租户上下文（命令行/后台任务）：仅无主（tenant_id 为 NULL）文档放行"""
    assert _can_download_document({"tenant_id": None, "source_type": "product"}, None) is True


def test_cannot_download_tenant_doc_without_tenant_context():
    """无租户上下文，但文档归属某租户 -> 拒绝（demo 公共租户已移除）"""
    assert _can_download_document({"tenant_id": "tenant_test1", "source_type": "product"}, None) is False


def test_cannot_download_foreign_tenant_doc_with_tenant_context():
    """有租户上下文 B，但文档归属他租户且无共享授权 -> 拒绝"""
    with patch("src.knowledge.api._has_shared_access", return_value=False):
        assert _can_download_document({"tenant_id": "A", "source_type": "product"}, "B") is False


def test_cannot_download_orphan_document_with_tenant_context():
    """有租户上下文，但文档无归属租户 -> 拒绝（不泄露无主文档）"""
    with patch("src.knowledge.api._has_shared_access", return_value=False):
        assert _can_download_document({"tenant_id": None, "source_type": "product"}, "B") is False


def test_cannot_download_cross_tenant_without_shared():
    """跨租户文档、未启用共享授权 -> 拒绝"""
    with patch("src.knowledge.api._has_shared_access", return_value=False):
        assert _can_download_document({"tenant_id": "A", "source_type": "product"}, "B") is False


def test_can_download_cross_tenant_with_shared():
    """跨租户文档、已启用共享授权 -> 放行，且以正确参数调用共享校验"""
    with patch("src.knowledge.api._has_shared_access", return_value=True) as mock_check:
        assert _can_download_document({"tenant_id": "A", "source_type": "industry"}, "B") is True
        mock_check.assert_called_once_with("B", "A", "industry")


def test_shared_check_not_called_for_own_tenant():
    """本租户文档不触发共享校验"""
    with patch("src.knowledge.api._has_shared_access") as mock_check:
        assert _can_download_document({"tenant_id": "B", "source_type": "product"}, "B") is True
        assert not mock_check.called


# ============== schema 源文件兜底解析 ==============


def test_parse_doc_metadata_json_string():
    """metadata 为 JSON 字符串（DB TEXT 列）-> 解析为 dict"""
    assert _parse_doc_metadata('{"a": 1}') == {"a": 1}


def test_parse_doc_metadata_invalid_returns_empty():
    """metadata 非法 JSON / None -> 空 dict（不抛异常）"""
    assert _parse_doc_metadata("not-json") == {}
    assert _parse_doc_metadata(None) == {}
    assert _parse_doc_metadata({"already": "dict"}) == {"already": "dict"}


def test_resolve_schema_file_from_source_dict(tmp_path, monkeypatch):
    """页面上传：metadata.source.file_path 还原路径，文件名取 source_info"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "storage/tenants/t1/data_sources").mkdir(parents=True)
    meta = {
        "source": {"type": "excel", "file_path": "storage/tenants/t1/data_sources/abc.xlsx", "sheet_name": "Sheet1"},
        "source_info": "客户名单.xlsx",
    }
    resolved = _resolve_schema_doc_file(meta, "tenant_t1")
    assert resolved == {
        "file_path": "storage/tenants/t1/data_sources/abc.xlsx",
        "filename": "客户名单.xlsx",
    }


def test_resolve_schema_file_from_file_prefix_source_info(tmp_path, monkeypatch):
    """对话上传：source_info 为 "file:" 前缀路径 -> 还原路径，文件名取路径 basename"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "storage/uploads/tenant_t1/conversation").mkdir(parents=True)
    meta = {"source": "chat-attachment", "source_info": "file:storage/uploads/tenant_t1/conversation/订单.csv"}
    resolved = _resolve_schema_doc_file(meta, "tenant_t1")
    assert resolved == {"file_path": "storage/uploads/tenant_t1/conversation/订单.csv", "filename": "订单.csv"}


def test_resolve_schema_file_rejects_path_outside_tenant(tmp_path, monkeypatch):
    """P1 安全：source.file_path 指向租户附件目录之外 -> 拒绝（防任意文件读取）"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "storage/tenants/t1/data_sources").mkdir(parents=True)
    meta = {"source": {"type": "excel", "file_path": "/app/.env"}, "source_info": "客户名单.xlsx"}
    assert _resolve_schema_doc_file(meta, "tenant_t1") is None
    meta2 = {"source_info": "file:storage/tenants/other_tenant/data_sources/a.xlsx"}
    assert _resolve_schema_doc_file(meta2, "tenant_t1") is None


def test_resolve_schema_file_rejects_without_tenant():
    """无租户上下文（tenant_id 为 NULL）-> 不做兜底下载"""
    meta = {"source": {"type": "excel", "file_path": "/app/.env"}, "source_info": "x.xlsx"}
    assert _resolve_schema_doc_file(meta, None) is None


def test_resolve_schema_file_none_for_connector():
    """数据库连接器导入的 schema 无本地文件 -> 返回 None（不可下载）"""
    meta = {"source": {"type": "database", "connector_id": 3}, "source_info": "mysql://host:3306/db/t"}
    assert _resolve_schema_doc_file(meta, "tenant_t1") is None


def test_resolve_schema_file_none_for_plain_source_info():
    """source_info 为普通文件名（无 file: 前缀、无 source.file_path）-> None，不误判为路径"""
    assert _resolve_schema_doc_file({"source_info": "客户名单.xlsx"}, "tenant_t1") is None
