"""
知识库文档下载权限校验测试。

验证 _can_download_document：本租户放行、无主文档放行、共享范围校验（§6.5 下载漏洞修复）。
"""
from unittest.mock import patch

from src.knowledge.api import _can_download_document


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
