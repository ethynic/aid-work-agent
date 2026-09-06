"""
cp 工具内嵌工作成果登记（层1）单元测试

覆盖场景：
- 有完整上下文时：成功登记
- 缺 tenant_id：跳过登记
- 缺 user_id：跳过登记
- 缺 session_id：跳过登记
- WorkOutcomeDB.create 异常时不阻塞 cp 主流程（execute 层 try/except 隔离）
- _do_record_work_outcome 同步写入调用正确
"""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from src.tools.context import ToolExecutionContext, tool_execution_scope

pytestmark = [pytest.mark.tools]


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class TestRecordWorkOutcomeContextGate:
    """_record_work_outcome 上下文门控测试"""

    @pytest.mark.asyncio
    async def test_skips_when_no_tenant_id(self):
        """无 tenant_id 时跳过登记"""
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        cp_result = {"file_id": "f1", "file_name": "test.md", "file_path": "/tmp/test.md"}

        with (
            tool_execution_scope(ToolExecutionContext(user_id="u1", session_id="s1")),
            patch("src.reports.work_outcome_db.WorkOutcomeDB.create") as mock_create,
        ):
            await tool._record_work_outcome(cp_result)
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_user_id(self):
        """无 user_id 时跳过登记"""
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        cp_result = {"file_id": "f1", "file_name": "test.md", "file_path": "/tmp/test.md"}

        with (
            tool_execution_scope(ToolExecutionContext(tenant_id="t1", session_id="s1")),
            patch("src.reports.work_outcome_db.WorkOutcomeDB.create") as mock_create,
        ):
            await tool._record_work_outcome(cp_result)
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_session_id(self):
        """无 session_id 时跳过登记（避免 NOT NULL 约束失败）"""
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        cp_result = {"file_id": "f1", "file_name": "test.md", "file_path": "/tmp/test.md"}

        with (
            tool_execution_scope(ToolExecutionContext(tenant_id="t1", user_id="u1")),
            patch("src.reports.work_outcome_db.WorkOutcomeDB.create") as mock_create,
        ):
            await tool._record_work_outcome(cp_result)
            mock_create.assert_not_called()


class TestRecordWorkOutcomeSuccess:
    """有完整上下文时登记成功"""

    @pytest.mark.asyncio
    async def test_calls_create_with_correct_args(self):
        """有完整上下文时调用 WorkOutcomeDB.create，参数正确"""
        from src.tools.file.cp_tool import CpTool

        tool = CpTool()
        cp_result = {
            "file_id": "file_abc",
            "file_name": "报价单.xlsx",
            "file_path": "/tmp/报价单.xlsx",
        }

        with (
            tool_execution_scope(ToolExecutionContext(
                tenant_id="t1",
                user_id="u1",
                session_id="s_abc",
                channel="web",
                subagent_id="trade-specialist",
                chat_record_id=123,
            )),
            patch("src.reports.work_outcome_db.WorkOutcomeDB.create") as mock_create,
        ):
            mock_create.return_value = {"id": 1, "outcome_id": "wo_abc"}

            await tool._record_work_outcome(cp_result)

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["tenant_id"] == "t1"
        assert call_kwargs["user_id"] == "u1"
        assert call_kwargs["session_id"] == "s_abc"
        assert call_kwargs["channel"] == "web"
        assert call_kwargs["subagent_id"] == "trade-specialist"
        assert call_kwargs["chat_record_id"] == 123
        assert call_kwargs["outcome_type"] == "file"
        assert call_kwargs["source"] == "cp_realtime"
        assert call_kwargs["file_id"] == "file_abc"
        assert call_kwargs["file_name"] == "报价单.xlsx"
        assert call_kwargs["file_path"] == "/tmp/报价单.xlsx"
        assert "报价单.xlsx" in call_kwargs["summary"]
        assert call_kwargs["metadata"] == {"source_tool": "cp"}


class TestExecuteIsolatesRecordFailure:
    """execute 层隔离 _record_work_outcome 失败"""

    @pytest.mark.asyncio
    async def test_execute_returns_result_when_record_fails(self, tmp_path):
        """_record_work_outcome 抛异常时 execute 仍能返回正确结果"""
        from src.tools.file.cp_tool import CpTool

        src_file = PROJECT_ROOT / "configs" / "config.yaml"
        if not src_file.exists():
            pytest.skip("configs/config.yaml not found")

        tool = CpTool()
        # 目标文件新路径（output/ 前缀被剥离，落租户 conversation 目录），
        # 先清理历史残留（含旧版遗留），避免"目标文件已存在"干扰断言
        cleanup = PROJECT_ROOT / "storage" / "tenants" / "t1" / "conversation" / "test_isolate_record.yaml"
        cleanup.unlink(missing_ok=True)
        with (
            tool_execution_scope(ToolExecutionContext(user_id="u1", tenant_id="t1")),
            patch.object(tool, "_register_download") as mock_reg,
            patch.object(tool, "_record_work_outcome", new_callable=AsyncMock) as mock_record,
        ):
            mock_reg.return_value = {
                "success": True,
                "file_id": "file_isolate",
                "file_name": "config.yaml",
                "file_size": 100,
                "download_url": "/api/files/file_isolate/download",
                "file_path": "/tmp/config.yaml",
            }
            # 模拟工作成果登记失败
            mock_record.side_effect = Exception("DB down")

            result = await tool.execute(
                source_file_path=str(src_file),
                file_path="output/test_isolate_record.yaml",
                register_download=True,
            )

            # 主流程仍应返回正确结果（异常被 execute 的 try/except 吞掉）
            assert isinstance(result, dict)
            assert result["file_id"] == "file_isolate"
            assert result["download_url"] == "/api/files/file_isolate/download"
            # _record_work_outcome 确实被调用了
            mock_record.assert_called_once()

        # 清理（新路径；旧 storage/output 残留一并清理防跨版本污染）
        cleanup.unlink(missing_ok=True)
        legacy = PROJECT_ROOT / "storage" / "output" / "test_isolate_record.yaml"
        legacy.unlink(missing_ok=True)
