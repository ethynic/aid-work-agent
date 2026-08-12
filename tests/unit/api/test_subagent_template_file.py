"""
subagent_template_file API 单元测试。

验证：
- GET /{name}：返回 DB 模板列表
- POST /{name}/upload：白名单校验、名称校验、无租户拒绝、落盘 + Redis 永久元数据 + DB 追加
- DELETE /{name}/{file_id}：DB 移除 + Redis/磁盘清理；不存在的 file_id 幂等
- Redis 元数据不调 expire（永久，区别于 /api/upload 的 24h）
"""

import io
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from starlette.datastructures import UploadFile

import src.api.subagent_template_file as api_module


def _make_upload(filename="tpl.docx", content=b"hello"):
    f = UploadFile(filename=filename, file=io.BytesIO(content))
    f.size = len(content)
    return f


class TestListTemplates:
    @pytest.mark.asyncio
    async def test_returns_db_data(self):
        data = [{"name": "A", "file_id": "file_1"}]
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"), \
             patch.object(api_module.SubagentTemplateFileDB, "get", return_value=data):
            res = await api_module.list_templates("agent_x", request_admin={"user_id": "u1"})
        assert res["success"] is True
        assert res["data"] == data

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data(self):
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"), \
             patch.object(api_module.SubagentTemplateFileDB, "get", return_value=[]):
            res = await api_module.list_templates("agent_x", request_admin={})
        assert res["success"] is True
        assert res["data"] == []


class TestUploadTemplate:
    @pytest.mark.asyncio
    async def test_upload_success(self, tmp_path):
        upload = _make_upload("quote.xlsx", b"abc")
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"), \
             patch.object(api_module, "_templates_dir", return_value=tmp_path), \
             patch.object(api_module, "redis_client") as mock_redis, \
             patch.object(api_module.SubagentTemplateFileDB, "get", return_value=[]) as mock_get, \
             patch.object(api_module.SubagentTemplateFileDB, "set", return_value=True) as mock_set:
            mock_redis.make_key.return_value = "uploaded_file:file_x"
            res = await api_module.upload_template(
                "agent_x", name="报价单", file=upload, request_admin={"user_id": "u1"}
            )
        assert res["success"] is True
        assert len(res["data"]) == 1
        item = res["data"][0]
        assert item["name"] == "报价单"
        assert item["file_id"].startswith("file_")
        assert item["mime_type"] == api_module._ALLOWED_EXTS[".xlsx"]
        assert item["original_name"] == "quote.xlsx"
        # 落盘文件存在
        assert len(list(tmp_path.glob("file_*"))) == 1
        # Redis 永久元数据：hset 被调用，expire 不被调用
        assert mock_redis.hset.called
        mock_redis.expire.assert_not_called()
        # DB set 收到含新模板的列表
        set_arg = mock_set.call_args.args[2]
        assert len(set_arg) == 1 and set_arg[0]["name"] == "报价单"

    @pytest.mark.asyncio
    async def test_upload_appends_to_existing(self, tmp_path):
        upload = _make_upload("note.md", b"x")
        existing = [{"name": "旧", "file_id": "file_old"}]
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"), \
             patch.object(api_module, "_templates_dir", return_value=tmp_path), \
             patch.object(api_module, "redis_client"), \
             patch.object(api_module.SubagentTemplateFileDB, "get", return_value=existing), \
             patch.object(api_module.SubagentTemplateFileDB, "set", return_value=True) as mock_set:
            res = await api_module.upload_template(
                "agent_x", name="新", file=upload, request_admin={}
            )
        assert len(res["data"]) == 2
        set_arg = mock_set.call_args.args[2]
        assert len(set_arg) == 2

    @pytest.mark.asyncio
    async def test_rejects_disallowed_extension(self):
        upload = _make_upload("evil.exe", b"x")
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"):
            with pytest.raises(HTTPException) as exc:
                await api_module.upload_template(
                    "agent_x", name="X", file=upload, request_admin={}
                )
        assert exc.value.status_code == 400
        assert "不支持" in exc.value.detail

    @pytest.mark.asyncio
    async def test_rejects_empty_name(self):
        upload = _make_upload()
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"):
            with pytest.raises(HTTPException) as exc:
                await api_module.upload_template(
                    "agent_x", name="   ", file=upload, request_admin={}
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_missing_file(self):
        """模板名称与文件必须配套：缺文件 → 400"""
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"):
            with pytest.raises(HTTPException) as exc:
                await api_module.upload_template(
                    "agent_x", name="X", file=None, request_admin={}
                )
        assert exc.value.status_code == 400
        assert "文件" in exc.value.detail

    @pytest.mark.asyncio
    async def test_rejects_no_tenant(self):
        upload = _make_upload()
        with patch.object(api_module, "get_current_tenant_id", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await api_module.upload_template(
                    "agent_x", name="X", file=upload, request_admin={}
                )
        assert exc.value.status_code == 400


class TestDeleteTemplate:
    @pytest.mark.asyncio
    async def test_delete_existing(self, tmp_path):
        existing = [
            {"name": "A", "file_id": "file_1"},
            {"name": "B", "file_id": "file_2"},
        ]
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"), \
             patch("src.main.UPLOAD_DIR", tmp_path), \
             patch.object(api_module, "redis_client") as mock_redis, \
             patch.object(api_module.SubagentTemplateFileDB, "get", return_value=existing), \
             patch.object(api_module.SubagentTemplateFileDB, "set", return_value=True) as mock_set:
            mock_redis.make_key.return_value = "k"
            res = await api_module.delete_template("agent_x", "file_1", request_admin={})
        assert res["success"] is True
        assert len(res["data"]) == 1
        assert res["data"][0]["file_id"] == "file_2"
        mock_redis.delete.assert_called_once_with("k")
        set_arg = mock_set.call_args.args[2]
        assert all(f["file_id"] != "file_1" for f in set_arg)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_idempotent(self):
        existing = [{"name": "A", "file_id": "file_1"}]
        with patch.object(api_module, "get_current_tenant_id", return_value="t1"), \
             patch.object(api_module, "redis_client"), \
             patch.object(api_module.SubagentTemplateFileDB, "get", return_value=existing), \
             patch.object(api_module.SubagentTemplateFileDB, "set") as mock_set:
            res = await api_module.delete_template("agent_x", "file_99", request_admin={})
        assert res["success"] is True
        assert res["data"] == existing
        mock_set.assert_not_called()  # 不存在 → 不写库

    @pytest.mark.asyncio
    async def test_delete_no_tenant_raises(self):
        with patch.object(api_module, "get_current_tenant_id", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await api_module.delete_template("agent_x", "file_1", request_admin={})
        assert exc.value.status_code == 400
