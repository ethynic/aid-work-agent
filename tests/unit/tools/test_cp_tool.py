"""
cp 工具单元测试

覆盖场景：
- 复制成功 + register_download=True（指定 file_path / 不传 file_path）
- 复制成功 + register_download=False + 提供 file_path
- 复制 + register_download=False + 不传 file_path（报错）
- 源文件不存在
- 源含 .. 路径穿越
- 源是绝对路径但在项目根外
- 目标已存在 + overwrite=False / overwrite=True
- 源后缀在 FORBIDDEN_EXTENSIONS
- 复制后内容一致（sha256）
- 注册下载调用验证
"""

import hashlib
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.tools]

# 项目根目录，用于构造合法源路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class TestCpToolBasic:
    """基本复制功能测试"""

    def test_tool_definition(self):
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        assert tool.name == "cp"
        assert tool.display_name == "复制文件"
        assert tool.category == "file"
        defn = tool.to_tool_definition()
        assert "input_schema" in defn
        assert defn["name"] == "cp"

    @pytest.mark.asyncio
    async def test_copy_success_with_file_path_and_register_download(self, tmp_path):
        """复制成功 + register_download=True + 指定 file_path"""
        from src.tools.file.cp_tool import CpTool

        # 在项目根目录内创建源文件（用 tmp_path 模拟项目内路径不可行，
        # 所以直接用项目内一个真实存在的文件做源）
        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        # 目标路径必须在 storage/ 下
        dst_rel = "output/test_cp_config.yaml"

        tool = CpTool()
        tool.set_user_id("test_user")

        # mock _register_download 避免 redis 依赖
        with patch.object(tool, "_register_download") as mock_reg:
            mock_reg.return_value = {
                "success": True,
                "file_id": "file_abc123",
                "file_name": "config.yaml",
                "file_size": 100,
                "download_url": "/api/files/file_abc123/download",
                "file_path": "/tmp/upload/config.yaml",
            }
            result = await tool.execute(
                source_file_path=str(src_file),
                file_path=dst_rel,
                register_download=True,
            )

        assert isinstance(result, dict)
        assert "file_path" in result
        assert "file_name" in result
        assert "file_size" in result
        assert "download_url" in result
        assert "file_id" in result
        assert "resolved_source" in result
        assert result["download_url"] == "/api/files/file_abc123/download"
        assert result["file_id"] == "file_abc123"

        # 清理：删除 storage/output 下创建的文件
        storage_dir = PROJECT_ROOT / "storage" / "output"
        if storage_dir.exists():
            cleanup = storage_dir / "test_cp_config.yaml"
            cleanup.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_copy_success_without_file_path(self, tmp_path):
        """复制成功 + 不传 file_path → 自动分配下载目录"""
        from src.tools.file.cp_tool import CpTool

        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        tool = CpTool()

        with patch.object(tool, "_register_download") as mock_reg:
            mock_reg.return_value = {
                "success": True,
                "file_id": "file_auto001",
                "file_name": "config.yaml",
                "file_size": 200,
                "download_url": "/api/files/file_auto001/download",
                "file_path": "/tmp/upload/config.yaml",
            }
            result = await tool.execute(
                source_file_path=str(src_file),
                register_download=True,
            )

        assert isinstance(result, dict)
        assert result["download_url"] == "/api/files/file_auto001/download"
        # _register_download 应被调用
        mock_reg.assert_called_once()

    @pytest.mark.asyncio
    async def test_copy_register_download_false_with_file_path(self, tmp_path):
        """复制成功 + register_download=False + 提供 file_path"""
        from src.tools.file.cp_tool import CpTool

        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        dst_rel = "output/test_cp_no_download.yaml"

        tool = CpTool()
        result = await tool.execute(
            source_file_path=str(src_file),
            file_path=dst_rel,
            register_download=False,
        )

        assert isinstance(result, dict)
        assert "file_path" in result
        assert "file_name" in result
        assert "file_size" in result
        assert "resolved_source" in result
        # 不注册下载时不应有 download_url 和 file_id
        assert "download_url" not in result
        assert "file_id" not in result

        # 清理
        cleanup = PROJECT_ROOT / "storage" / "output" / "test_cp_no_download.yaml"
        cleanup.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_copy_register_download_false_without_file_path(self):
        """不注册下载且不传 file_path → 返回错误字符串"""
        from src.tools.file.cp_tool import CpTool

        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        tool = CpTool()
        result = await tool.execute(
            source_file_path=str(src_file),
            register_download=False,
        )

        assert isinstance(result, str)
        assert "不注册下载时需要提供 file_path" in result


class TestCpToolSourceErrors:
    """源文件错误场景测试"""

    @pytest.mark.asyncio
    async def test_source_not_found(self):
        """源文件不存在 → 返回错误字符串"""
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        result = await tool.execute(
            source_file_path=str(PROJECT_ROOT / "nonexistent_file_xyz.txt"),
        )

        assert isinstance(result, str)
        assert "复制文件失败" in result
        assert "源文件不存在" in result

    @pytest.mark.asyncio
    async def test_source_path_traversal_relative(self):
        """源含 .. 路径穿越（相对路径解析后超出项目根）→ 返回错误字符串"""
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        # 相对路径 ../../../etc/passwd 从项目根解析后超出范围
        result = await tool.execute(
            source_file_path="../../../etc/passwd",
        )

        assert isinstance(result, str)
        assert "复制文件失败" in result
        assert "超出允许范围" in result

    @pytest.mark.asyncio
    async def test_source_absolute_path_outside_project_root(self):
        """源是绝对路径但在项目根外 → 返回错误字符串"""
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        # Linux/macOS 绝对路径
        result = await tool.execute(
            source_file_path="/etc/passwd",
        )

        assert isinstance(result, str)
        assert "复制文件失败" in result

    @pytest.mark.asyncio
    async def test_source_absolute_path_outside_project_root_windows(self):
        """源是 Windows 绝对路径但在项目根外 → 返回错误字符串"""
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        # Windows 绝对路径（如果测试环境不是 Windows 则文件不存在，但路径安全检查先于文件存在检查）
        result = await tool.execute(
            source_file_path="C:/Windows/system32/drivers/etc/hosts",
        )

        assert isinstance(result, str)
        assert "复制文件失败" in result
        # 应该命中"超出允许范围"或"源文件不存在"，关键是不能成功复制
        assert "超出允许范围" in result or "源文件不存在" in result


class TestCpToolOverwrite:
    """目标文件已存在场景"""

    @pytest.mark.asyncio
    async def test_target_exists_no_overwrite(self, tmp_path):
        """目标已存在 + overwrite=False → 返回错误字符串"""
        from src.tools.file.cp_tool import CpTool

        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        # 在 storage/output 下创建已存在的目标文件
        dst_dir = PROJECT_ROOT / "storage" / "output"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_file = dst_dir / "test_cp_overwrite.yaml"
        dst_file.write_text("existing content", encoding="utf-8")

        try:
            tool = CpTool()
            result = await tool.execute(
                source_file_path=str(src_file),
                file_path="output/test_cp_overwrite.yaml",
                overwrite=False,
                register_download=False,
            )

            assert isinstance(result, str)
            assert "目标文件已存在" in result
        finally:
            dst_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_target_exists_with_overwrite(self, tmp_path):
        """目标已存在 + overwrite=True → 覆盖成功"""
        from src.tools.file.cp_tool import CpTool

        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        dst_dir = PROJECT_ROOT / "storage" / "output"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_file = dst_dir / "test_cp_overwrite_ok.yaml"
        dst_file.write_text("old content", encoding="utf-8")

        try:
            tool = CpTool()
            result = await tool.execute(
                source_file_path=str(src_file),
                file_path="output/test_cp_overwrite_ok.yaml",
                overwrite=True,
                register_download=False,
            )

            assert isinstance(result, dict)
            assert "file_path" in result
            # 目标文件内容应已更新
            new_content = dst_file.read_text(encoding="utf-8")
            assert new_content != "old content"
        finally:
            dst_file.unlink(missing_ok=True)


class TestCpToolForbiddenExtensions:
    """源后缀黑名单测试"""

    @pytest.mark.asyncio
    async def test_forbidden_extension_exe(self, tmp_path):
        """源后缀在 FORBIDDEN_EXTENSIONS（如 .exe）→ 返回错误字符串"""
        from src.tools.file.cp_tool import CpTool

        # 创建 .exe 源文件
        exe_file = PROJECT_ROOT / "test_forbidden_copy.exe"
        exe_file.write_bytes(b"MZ\x90\x00" + b"\x00" * 100)

        try:
            tool = CpTool()
            result = await tool.execute(
                source_file_path=str(exe_file),
                register_download=False,
                file_path="output/test.exe",
            )

            assert isinstance(result, str)
            assert "不允许复制" in result
            assert ".exe" in result
        finally:
            exe_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_forbidden_extension_py(self, tmp_path):
        """源后缀 .py 在 FORBIDDEN_EXTENSIONS → 返回错误字符串"""
        from src.tools.file.cp_tool import CpTool

        py_file = PROJECT_ROOT / "test_forbidden_script.py"
        py_file.write_text("print('hello')", encoding="utf-8")

        try:
            tool = CpTool()
            result = await tool.execute(
                source_file_path=str(py_file),
                register_download=False,
                file_path="output/test.py",
            )

            assert isinstance(result, str)
            assert "不允许复制" in result
            assert ".py" in result
        finally:
            py_file.unlink(missing_ok=True)


class TestCpToolContentIntegrity:
    """复制后内容完整性测试"""

    @pytest.mark.asyncio
    async def test_copy_content_sha256_match(self, tmp_path):
        """复制后 sha256 一致"""
        from src.tools.file.cp_tool import CpTool

        # 在项目根目录内创建源文件
        src_file = PROJECT_ROOT / "test_cp_sha256_src.txt"
        original_content = "Hello, World! 这是一个测试文件。line2\nline3"
        src_file.write_text(original_content, encoding="utf-8")

        dst_rel = "output/test_cp_sha256_dst.txt"

        try:
            tool = CpTool()
            result = await tool.execute(
                source_file_path=str(src_file),
                file_path=dst_rel,
                register_download=False,
            )

            assert isinstance(result, dict)
            assert "file_path" in result

            dst_path = PROJECT_ROOT / "storage" / dst_rel
            src_hash = hashlib.sha256(src_file.read_bytes()).hexdigest()
            dst_hash = hashlib.sha256(dst_path.read_bytes()).hexdigest()
            assert src_hash == dst_hash, "复制前后 sha256 不一致"
        finally:
            src_file.unlink(missing_ok=True)
            dst_path = PROJECT_ROOT / "storage" / "output" / "test_cp_sha256_dst.txt"
            dst_path.unlink(missing_ok=True)


class TestCpToolRegisterDownload:
    """注册下载调用验证"""

    @pytest.mark.asyncio
    async def test_register_download_called_with_correct_args(self, tmp_path):
        """验证 _register_download 调用参数"""
        from src.tools.file.cp_tool import CpTool

        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        tool = CpTool()
        tool.set_user_id("user_123")
        tool.set_tenant_id("tenant_456")

        with patch.object(tool, "_register_download") as mock_reg:
            mock_reg.return_value = {
                "success": True,
                "file_id": "file_mocked",
                "file_name": "custom_name.yaml",
                "file_size": 300,
                "download_url": "/api/files/file_mocked/download",
                "file_path": "/tmp/upload/custom_name.yaml",
            }
            result = await tool.execute(
                source_file_path=str(src_file),
                file_path="output/test_reg_download.yaml",
                register_download=True,
                display_name="custom_name",
            )

        assert isinstance(result, dict)
        mock_reg.assert_called_once()
        # 验证 display_name 传递
        call_args = mock_reg.call_args
        assert call_args[0][1] == "custom_name"  # display_name 参数

        # 清理
        cleanup = PROJECT_ROOT / "storage" / "output" / "test_reg_download.yaml"
        cleanup.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_register_download_redis_hset_calls(self, tmp_path):
        """验证 redis hset 调用次数和字段"""
        from src.tools.file.cp_tool import CpTool

        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        tool = CpTool()
        tool.set_user_id("user_redis_test")
        tool.set_tenant_id("tenant_redis_test")

        mock_redis = MagicMock()
        mock_redis.make_key = MagicMock(return_value="uploaded_file:file_test123")
        mock_redis.hset = MagicMock()
        mock_redis.expire = MagicMock()

        # mock UPLOAD_DIR 到 tmp_path
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()

        # redis_client 在 _register_download 内部通过
        # `from src.core.redis_client import redis_client` 导入，
        # 所以要 patch 源模块的属性
        with (
            patch("src.core.redis_client.redis_client", mock_redis),
            patch("src.tools.file.cp_tool._resolve_upload_dir", return_value=upload_dir),
            patch("src.tools.file.cp_tool.uuid.uuid4") as mock_uuid,
        ):
            mock_uuid.return_value = MagicMock(hex="test123456789abc")

            result = await tool.execute(
                source_file_path=str(src_file),
                register_download=True,
            )

        assert isinstance(result, dict)
        assert "download_url" in result

        # redis hset 应被调用多次（每个 file_info 字段一次）
        assert mock_redis.hset.call_count >= 5  # file_id, name, path, size, mime_type, type
        mock_redis.expire.assert_called_once_with("uploaded_file:file_test123", 86400)


class TestCpToolGetDisplayName:
    """get_display_name 动态显示名测试"""

    def test_display_name_with_source(self):
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        name = tool.get_display_name(
            tool_args={"source_file_path": "/some/path/template.html"}
        )
        assert "template.html" in name
        assert "复制文件" in name

    def test_display_name_without_args(self):
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        name = tool.get_display_name()
        assert name == "复制文件"

    def test_display_name_empty_args(self):
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        name = tool.get_display_name(tool_args={})
        assert name == "复制文件"


class TestCpToolUserIdTenantId:
    """user_id / tenant_id 注入测试"""

    def test_set_user_id(self):
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        tool.set_user_id("u1")
        assert tool._user_id == "u1"

    def test_set_tenant_id(self):
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        tool.set_tenant_id("t1")
        assert tool._tenant_id == "t1"
