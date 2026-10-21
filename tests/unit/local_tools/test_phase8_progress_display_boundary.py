"""Phase 8 组合验收（组合 4 补充）：心跳/语义分离的服务端展示边界。

ProgressForwarder（共享包）发出的带标注心跳消息，经 Runner 侧事件转发到
服务端 _format_event_text 展示时：消息原文（含「（心跳续租，无新进展 Ns）」
标注与告警后缀）必须原样保留，只允许加前缀/计数后缀——服务端不得改写、
截断或吞掉标注，否则前端无法区分心跳与业务语义进展（Phase 7 第 1 条）。
"""

import pytest

from src.local_tools.proxy_tool import LocalToolProxyTool

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('message,current,total', [
    ('stageA 设置筛选面板（心跳续租，无新进展 12s）', None, None),
    ('stageB 筛选完成（心跳续租，无新进展 65s，已超过告警阈值 60s）', 2, 5),
    ('stageA 设置筛选面板', None, None),
])
def test_heartbeat_annotation_survives_server_event_formatting(message, current, total):
    tool = LocalToolProxyTool.__new__(LocalToolProxyTool)
    tool.display_name = 'BOSS 批量读取简历入库'
    text = tool._format_event_text({'message': message, 'current': current, 'total': total})
    assert message in text, '心跳/语义消息原文必须完整保留（展示只加前缀/计数）'
    if current is not None and total:
        assert f'{current}/{total}' in text
