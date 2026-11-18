"""
使用教程文档中心 API 集成测试

覆盖两个端点的鉴权与内容读取：
- GET /api/guides/catalog
- GET /api/guides/doc?path=...

测试策略（同 test_admin_redis.py）：
- 鉴权：mock get_current_user 返回 None / 非平台管理员，断言 401 / 403
- 正常路径：mock 平台管理员，读取仓库真实 docs/guides/ 文档
- 安全：目录穿越、反斜杠、非 .md、不存在路径一律 404
"""

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient
from src.main import app


client = TestClient(app)


def _patch_platform_admin(user_id: str = "admin_test"):
    """mock 平台管理员权限校验"""
    return (
        patch(
            "src.api.guides.get_current_user",
            return_value={"user_id": user_id, "role": "platform_admin", "tenant_id": None},
        ),
        patch("src.api.guides.is_platform_admin", return_value=True),
    )


# ============== 鉴权 ==============

def test_catalog_requires_login():
    with patch("src.api.guides.get_current_user", return_value=None):
        resp = client.get("/api/guides/catalog")
    assert resp.status_code == 401


def test_catalog_rejects_non_platform_admin():
    with (
        patch(
            "src.api.guides.get_current_user",
            return_value={"user_id": "tenant_user", "role": "tenant_admin", "tenant_id": "t1"},
        ),
        patch("src.api.guides.is_platform_admin", return_value=False),
    ):
        resp = client.get("/api/guides/catalog")
    assert resp.status_code == 403


def test_doc_requires_login():
    with patch("src.api.guides.get_current_user", return_value=None):
        resp = client.get("/api/guides/doc", params={"path": "development/mcp_server.md"})
    assert resp.status_code == 401


def test_doc_rejects_non_platform_admin():
    with (
        patch(
            "src.api.guides.get_current_user",
            return_value={"user_id": "tenant_user", "role": "tenant_admin", "tenant_id": "t1"},
        ),
        patch("src.api.guides.is_platform_admin", return_value=False),
    ):
        resp = client.get("/api/guides/doc", params={"path": "development/mcp_server.md"})
    assert resp.status_code == 403


# ============== catalog ==============

def test_catalog_lists_categories_and_docs():
    p1, p2 = _patch_platform_admin()
    with p1, p2:
        resp = client.get("/api/guides/catalog")
    assert resp.status_code == 200
    data = resp.json()
    cats = {c["dir"]: c for c in data["categories"]}

    # manifest.yaml 定义的四个分类齐全且显示名正确
    assert set(cats.keys()) == {"deployment", "channels", "clients", "development"}
    assert cats["deployment"]["name"] == "部署与运维"
    assert cats["channels"]["name"] == "渠道接入"
    assert cats["clients"]["name"] == "客户端安装与使用"
    assert cats["development"]["name"] == "开发环境与工具"

    # 真实迁移文档在列，标题取自首个一级标题
    all_paths = {d["path"]: d for c in data["categories"] for d in c["docs"]}
    assert "deployment/runtime-boss-release-guide.md" in all_paths
    assert "channels/onboarding_guide.md" in all_paths
    assert "clients/安装手册.md" in all_paths
    assert "development/mcp_server.md" in all_paths

    doc = all_paths["development/mcp_server.md"]
    assert doc["title"].startswith("MCP Server")
    assert doc["size_bytes"] > 0
    assert len(doc["updated_at"]) >= 10


# ============== doc 正常读取 ==============

def test_doc_reads_content():
    p1, p2 = _patch_platform_admin()
    with p1, p2:
        resp = client.get("/api/guides/doc", params={"path": "development/mcp_server.md"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "development/mcp_server.md"
    assert data["title"].startswith("MCP Server")
    assert data["content"].lstrip().startswith("#")
    assert len(data["content"]) > 100


def test_doc_reads_nested_path():
    p1, p2 = _patch_platform_admin()
    with p1, p2:
        resp = client.get("/api/guides/doc", params={"path": "guides/guides/README.md"})
    assert resp.status_code == 404  # 相对根的路径不带 guides/ 前缀


# ============== 安全：路径校验 ==============

@pytest.mark.parametrize("bad_path", [
    "../../src/config/settings.py",
    "deployment/../../configs/config.yaml",
    "../AGENTS.md",
    "deployment/backup.md#§4",
    "manifest.yaml",
    "deployment/not-exist.md",
    "deployment\\backup.md",
    "",
    "/etc/passwd",
])
def test_doc_rejects_invalid_paths(bad_path):
    p1, p2 = _patch_platform_admin()
    with p1, p2:
        resp = client.get("/api/guides/doc", params={"path": bad_path})
    assert resp.status_code == 404


def test_doc_missing_path_param():
    p1, p2 = _patch_platform_admin()
    with p1, p2:
        resp = client.get("/api/guides/doc")
    assert resp.status_code == 422
