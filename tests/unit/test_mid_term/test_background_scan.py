"""Phase 8 §2.5 后台定时压缩扫描测试

覆盖：
- scan_over_threshold_sessions：返回行 / 透传参数 / DB 异常容错返回空
- run_background_compression_scan：disabled 直接零统计；
  enabled 时逐 session 压缩 + 隔离单 session 失败
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.memory.mid_term import (
    ContextCompressionService,
    run_background_compression_scan,
)


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary, fake_db_connection):
    """构造 ContextCompressionService（DB 已被 fake_db_connection mock）"""
    return ContextCompressionService(
        settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary
    )


# ========== scan_over_threshold_sessions ==========


@pytest.mark.asyncio
async def test_scan_over_threshold_sessions_returns_rows(service, fake_db_connection):
    """扫描返回 [(session_id, source_type), ...]，SQL 含 chat_sessions/channel_sessions/UNION ALL"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = [
        {"session_id": "s1", "source_type": "chat"},
        {"session_id": "s2", "source_type": "wecom_kf"},
    ]

    result = await service.scan_over_threshold_sessions(token_threshold=1000, batch_size=50)

    assert result == [("s1", "chat"), ("s2", "wecom_kf")]

    # 校验 SQL 包含两张表 + UNION ALL
    sql_arg = mock_cursor.execute.call_args[0][0]
    assert "chat_sessions" in sql_arg
    assert "channel_sessions" in sql_arg
    assert "UNION ALL" in sql_arg


@pytest.mark.asyncio
async def test_scan_threshold_passes_params(service, fake_db_connection):
    """execute 的 params 含 threshold(1000) 与 batch_size(50)"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = []

    await service.scan_over_threshold_sessions(token_threshold=1000, batch_size=50)

    params = mock_cursor.execute.call_args[0][1]
    assert 1000 in params           # threshold（出现两次）
    assert params.count(1000) == 2  # chat_sessions + channel_sessions 各一个
    assert 50 in params             # batch_size


@pytest.mark.asyncio
async def test_scan_db_error_returns_empty(service, fake_db_connection):
    """cursor.execute 抛异常 → 返回 [] 不外抛"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.execute.side_effect = Exception("db boom")

    result = await service.scan_over_threshold_sessions(token_threshold=1000, batch_size=50)
    assert result == []


# ========== run_background_compression_scan ==========


@pytest.mark.asyncio
async def test_run_background_compression_scan_disabled():
    """disabled 时直接返回零统计且不调用 scan"""
    with patch(
        "src.memory.mid_term.get_compression_service"
    ) as mock_get, patch(
        "src.memory.mid_term.settings"
    ) as mock_settings:
        mock_settings.memory.mid_term.enabled = True
        mock_settings.memory.mid_term.background_scan_enabled = False
        mock_get.return_value.scan_over_threshold_sessions = AsyncMock()

        stats = await run_background_compression_scan()

    assert stats == {"scanned": 0, "compressed": 0, "failed": 0, "skipped": 0}
    mock_get.return_value.scan_over_threshold_sessions.assert_not_called()


@pytest.mark.asyncio
async def test_run_background_compression_scan_compresses():
    """enabled 时：scan 返回 1 个 session，compress 返回 result → compressed=1"""
    fake_service = MagicMock()
    fake_service._get_model_limit = MagicMock(return_value=100000)
    fake_service.scan_over_threshold_sessions = AsyncMock(return_value=[("s1", "chat")])
    fake_service.compress_session = AsyncMock(return_value={"summary_id": "csum_x"})

    with patch(
        "src.memory.mid_term.get_compression_service", return_value=fake_service
    ), patch("src.memory.mid_term.settings") as mock_settings:
        mock_settings.memory.mid_term.enabled = True
        mock_settings.memory.mid_term.background_scan_enabled = True
        mock_settings.memory.mid_term.token_threshold_ratio = 0.7
        mock_settings.memory.mid_term.background_scan_batch_size = 50

        stats = await run_background_compression_scan()

    assert stats["scanned"] == 1
    assert stats["compressed"] == 1
    assert stats["failed"] == 0
    assert stats["skipped"] == 0
    # threshold = 100000 * 0.7
    fake_service.scan_over_threshold_sessions.assert_awaited_once_with(70000, 50)
    fake_service.compress_session.assert_awaited_once_with("s1", "chat", force=False)


@pytest.mark.asyncio
async def test_run_background_compression_scan_isolates_per_session_failure():
    """scan 返回 2 session，第一个 compress 抛异常、第二个成功 → failed=1, compressed=1"""
    fake_service = MagicMock()
    fake_service._get_model_limit = MagicMock(return_value=100000)
    fake_service.scan_over_threshold_sessions = AsyncMock(
        return_value=[("s1", "chat"), ("s2", "wecom_kf")]
    )
    fake_service.compress_session = AsyncMock(
        side_effect=[Exception("boom"), {"summary_id": "csum_y"}]
    )

    with patch(
        "src.memory.mid_term.get_compression_service", return_value=fake_service
    ), patch("src.memory.mid_term.settings") as mock_settings:
        mock_settings.memory.mid_term.enabled = True
        mock_settings.memory.mid_term.background_scan_enabled = True
        mock_settings.memory.mid_term.token_threshold_ratio = 0.7
        mock_settings.memory.mid_term.background_scan_batch_size = 50

        stats = await run_background_compression_scan()

    assert stats["scanned"] == 2
    assert stats["failed"] == 1
    assert stats["compressed"] == 1
    assert stats["skipped"] == 0
    assert fake_service.compress_session.await_count == 2
