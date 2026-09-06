"""
共享知识库 PUT 校验测试。

验证 _validate_shared_source：租户级授权存在 + source_type 属于来源租户，否则拒绝。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.subagent_knowledge_source import KnowledgeSourceItem, _validate_shared_source
from src.saas.api.knowledge_share import SetKnowledgeSharesRequest, set_knowledge_shares


def _fake_request():
    """构造 audit_action 装饰器可识别的 request 替身（端点签名新增 request: Request）"""
    return SimpleNamespace(
        headers={}, state=SimpleNamespace(tenant_id="B", user_id="u1", user_role=None),
        client=None, method="PUT", url=SimpleNamespace(path="/api/saas/tenant/knowledge-shares"),
    )


def _mock_db_row(row):
    """构造 fetchone 返回给定 row 的 DB 连接 mock"""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)
    return mock_cm


def test_validate_passes_when_authorized_and_has_category():
    """租户级授权存在 + 来源分类存在 -> 通过"""
    with patch("src.api.subagent_knowledge_source.get_db_connection",
               return_value=_mock_db_row({"authorized": True, "has_cat": True})):
        err = _validate_shared_source(
            "B", KnowledgeSourceItem(source_type="industry", display_name="行业库", owner_tenant_id="A")
        )
    assert err is None


def test_validate_rejects_unauthorized_owner():
    """来源租户未授权（tenant_knowledge_shares 无记录）-> 拒绝"""
    with patch("src.api.subagent_knowledge_source.get_db_connection",
               return_value=_mock_db_row({"authorized": False, "has_cat": True})):
        err = _validate_shared_source(
            "B", KnowledgeSourceItem(source_type="industry", display_name="行业库", owner_tenant_id="C")
        )
    assert err is not None
    assert "未授权" in err


def test_validate_rejects_missing_source_category():
    """来源租户不存在该 source_type 分类 -> 拒绝"""
    with patch("src.api.subagent_knowledge_source.get_db_connection",
               return_value=_mock_db_row({"authorized": True, "has_cat": False})):
        err = _validate_shared_source(
            "B", KnowledgeSourceItem(source_type="nope", display_name="x", owner_tenant_id="A")
        )
    assert err is not None
    assert "不存在" in err


def test_validate_rejects_self_owner_without_db_call():
    """来源租户不能是当前租户自身（不查 DB 直接拒绝）"""
    with patch("src.api.subagent_knowledge_source.get_db_connection") as db_mock:
        err = _validate_shared_source(
            "B", KnowledgeSourceItem(source_type="industry", display_name="行业库", owner_tenant_id="B")
        )
    assert err is not None
    assert "不能是当前租户" in err
    assert not db_mock.called


def test_validate_skips_item_without_owner():
    """本租户项（无 owner_tenant_id）跳过共享校验（不查 DB）"""
    with patch("src.api.subagent_knowledge_source.get_db_connection") as db_mock:
        err = _validate_shared_source(
            "B", KnowledgeSourceItem(source_type="product", display_name="商品库")
        )
    assert err is None
    assert not db_mock.called


@pytest.mark.asyncio
async def test_set_knowledge_shares_rejects_tenant_admin():
    """知识库接入是第一级授权闸门：租户管理员不能自行接入其他租户（防跨租户越权读）"""
    from src.saas.api import knowledge_share as ks

    req = SetKnowledgeSharesRequest(from_tenants=[])
    with patch.object(ks, "TenantKnowledgeShareDB") as db_mock, \
         patch.object(ks, "get_current_tenant_id", return_value="B"), \
         patch("src.services.behavior_log._insert_sync") as insert_mock:
        res = await set_knowledge_shares(
            _fake_request(), req, request_admin={"role": "tenant_admin", "user_id": "u1"}
        )
    assert res["success"] is False
    assert "仅平台管理员" in res["error"]
    db_mock.set.assert_not_called()
    # 业务级失败返回 {"success": False} 应被装饰器记为审计失败日志
    assert insert_mock.call_count == 1


@pytest.mark.asyncio
async def test_set_knowledge_shares_allows_platform_admin():
    """平台管理员可配置知识库接入（空列表 -> 全量清空）"""
    from src.saas.api import knowledge_share as ks

    req = SetKnowledgeSharesRequest(from_tenants=[])
    with patch.object(ks, "TenantKnowledgeShareDB") as db_mock, \
         patch.object(ks, "get_current_tenant_id", return_value="B"), \
         patch("src.services.behavior_log._insert_sync") as insert_mock:
        db_mock.set.return_value = True
        res = await set_knowledge_shares(
            _fake_request(), req, request_admin={"role": "platform_admin", "user_id": "u1"}
        )
    assert res["success"] is True
    db_mock.set.assert_called_once_with("B", [], created_by="u1")
    insert_mock.assert_called_once()
