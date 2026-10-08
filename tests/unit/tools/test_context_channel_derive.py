"""_derive_channel_from_record 渠道派生测试

背景：cp_tool 等工具实时登记工作成果时 channel 取自 ToolExecutionContext，
但主智能体循环构造上下文时从不传 channel，渠道会话交付文件的成果渠道恒为空。
修复：从 SessionRecordService.source_type 派生渠道（chat -> web，渠道名原样）。
"""

import pytest

from src.services.session_record import SessionRecordManager
from src.tools.context import ExecutionContextFactory

_derive_channel_from_record = ExecutionContextFactory.channel_from_record


class _FakeRecord:
    def __init__(self, source_type):
        self.source_type = source_type


@pytest.fixture(autouse=True)
def _clean_record_context():
    token = SessionRecordManager.set_current_record(None)
    yield
    SessionRecordManager.reset_current_record(token)


@pytest.mark.parametrize("source_type,expected", [
    ("chat", "web"),
    ("wecom", "wecom"),
    ("wecom_kf", "wecom_kf"),
    ("wecom_personal_rpa", "wecom_personal_rpa"),
    ("dingtalk", "dingtalk"),
    ("feishu", "feishu"),
])
def test_derive_channel_from_known_source_types(source_type, expected):
    SessionRecordManager.set_current_record(_FakeRecord(source_type))
    assert _derive_channel_from_record() == expected


def test_derive_channel_none_without_record():
    assert _derive_channel_from_record() is None


def test_derive_channel_none_for_background_source_type():
    SessionRecordManager.set_current_record(_FakeRecord("background_llm"))
    assert _derive_channel_from_record() is None
