"""
租户代码验证 API 测试
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.main import app

client = TestClient(app)


def test_tenant_enter_missing_code():
    """测试缺少租户代码"""
    response = client.post("/api/tenant/enter", json={})
    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert "租户代码不能为空" in data["error"]


def test_tenant_enter_invalid_format():
    """测试无效格式的租户代码"""
    response = client.post("/api/tenant/enter", json={"tenant_code": "abc"})
    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert "格式无效" in data["error"]


def test_tenant_enter_nonexistent():
    """测试不存在的租户代码"""
    with patch("src.saas.db.tenant_db.TenantDB.get_by_code") as mock_get:
        mock_get.return_value = None
        response = client.post("/api/tenant/enter", json={"tenant_code": "NOSUCH"})
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
        assert "不存在" in data["error"]


def test_tenant_enter_suspended():
    """测试被暂停的租户"""
    with patch("src.saas.db.tenant_db.TenantDB.get_by_code") as mock_get:
        mock_get.return_value = {
            "tenant_id": "tenant_test123",
            "status": "suspended",
            "company_name": "Test Company",
            "expire_at": None
        }
        response = client.post("/api/tenant/enter", json={"tenant_code": "SUSPEND"})
        assert response.status_code == 403
        data = response.json()
        assert data["success"] is False
        assert "暂停" in data["error"]


def test_tenant_enter_success():
    """测试成功的租户代码验证"""
    with patch("src.saas.db.tenant_db.TenantDB.get_by_code") as mock_get:
        mock_get.return_value = {
            "tenant_id": "tenant_test123",
            "status": "active",
            "company_name": "Test Company",
            "expire_at": None
        }
        response = client.post("/api/tenant/enter", json={"tenant_code": "TEST123"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["redirect_url"] == "/t/tenant_test123"
        assert data["tenant_id"] == "tenant_test123"


def test_tenant_enter_case_insensitive():
    """测试大小写不敏感的租户代码"""
    with patch("src.saas.db.tenant_db.TenantDB.get_by_code") as mock_get:
        mock_get.return_value = {
            "tenant_id": "tenant_test123",
            "status": "active",
            "company_name": "Test Company",
            "expire_at": None
        }
        # 代码存储为大写，但用户输入小写
        response = client.post("/api/tenant/enter", json={"tenant_code": "test123"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


if __name__ == "__main__":
    pytest.main([__file__])