"""契约 2.20 Phase 2 单元测试：parallel_only 领取过滤 + 心跳 parallel_claim 标记（mock DB，无真实 DB）

覆盖：
- repository.claim_next：parallel_only=True 时 WHERE 追加「provider_key 非 NULL 且
  tool_name 属于该设备上报 parallel_tools[provider_key]」条件；缺省参数 SQL 与现状一致；
  execution_lane/expected_invocation_id/provider_keys 既有过滤不受影响（session_tasks
  定向领取等调用方回归锚）
- api.runtime_claim：parallel_only 透传 claim_next，parallel_tools 取自设备
  capabilities_json 并做形态归一；缺省 False
- api.runtime_heartbeat：响应携带 parallel_claim=True（服务端实现即恒真）
"""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.local_tools import api as local_tools_api
from src.local_tools import repository

pytestmark = pytest.mark.unit

TENANT = "tenant_test"
DEVICE = "11111111-1111-1111-1111-111111111111"
HASH = "h" * 64

BASELINE_SELECT = "SELECT * FROM local_tool_invocations"


def _make_conn(fetchone_rows, executed):
    """mock 连接：cursor.execute 捕获 SQL，fetchone 按序返回"""
    cursor = SimpleNamespace(
        execute=lambda *a, **k: executed.append(a),
        fetchall=lambda: [],
        rowcount=1,
    )
    rows = iter(fetchone_rows)
    cursor.fetchone = lambda: next(rows, None)
    return SimpleNamespace(cursor=lambda *a, **k: cursor, commit=lambda: None, rollback=lambda: None)


def _patch_conn(fetchone_rows, executed):
    conn = _make_conn(fetchone_rows, executed)

    @contextmanager
    def fake_get_db_connection():
        yield conn

    return patch("src.local_tools.repository.get_db_connection", fake_get_db_connection)


def _select_sqls(executed):
    return [sql for sql, *_ in executed if isinstance(sql, str) and sql.lstrip().startswith("SELECT * FROM local_tool_invocations")]


class TestClaimNextParallelFilter:
    def test_parallel_only_appends_filter_and_param(self):
        """parallel_only=True：WHERE 追加并行过滤条件，jsonb 参数为设备 parallel_tools 映射"""
        executed = []
        with _patch_conn([{"id": DEVICE}, None], executed):  # lock_active_device 命中，SELECT 无行
            row = repository.claim_next(
                DEVICE, TENANT, HASH, 60, provider_keys=["wecom"],
                parallel_only=True, parallel_tools={"wecom": ["wecom_read_session"]},
            )
        assert row is None
        sqls = _select_sqls(executed)
        assert len(sqls) == 1
        sql = sqls[0]
        assert "provider_key IS NOT NULL" in sql
        assert "jsonb_array_elements_text" in sql
        assert "local_tool_invocations.tool_name = ANY" in sql
        # jsonb 参数紧跟在 lane/provider 过滤参数之后
        select_call = next(call for call in executed if isinstance(call[0], str) and call[0].lstrip().startswith(BASELINE_SELECT))
        params = select_call[1]
        assert json.loads(params[-1]) == {"wecom": ["wecom_read_session"]}

    def test_default_args_keep_sql_unchanged(self):
        """缺省参数：SQL 不含并行过滤条件（与现状逐字节一致）"""
        executed_default = []
        with _patch_conn([{"id": DEVICE}, None], executed_default):
            repository.claim_next(DEVICE, TENANT, HASH, 60, provider_keys=["wecom"])

        sql_default = _select_sqls(executed_default)[0]
        assert "provider_key IS NOT NULL" not in sql_default
        assert "jsonb_array_elements_text" not in sql_default

    def test_parallel_only_with_empty_tools_filters_everything(self):
        """parallel_only=True 但设备无并行工具映射：过滤条件仍生效（空映射匹配不到任何行）"""
        executed = []
        with _patch_conn([{"id": DEVICE}, None], executed):
            repository.claim_next(DEVICE, TENANT, HASH, 60, parallel_only=True, parallel_tools={})
        sql = _select_sqls(executed)[0]
        assert "provider_key IS NOT NULL" in sql
        select_call = next(call for call in executed if isinstance(call[0], str) and call[0].lstrip().startswith(BASELINE_SELECT))
        assert json.loads(select_call[1][-1]) == {}

    def test_session_lane_directed_claim_unaffected(self):
        """既有调用方回归锚：session_tasks 定向领取（execution_lane + expected_invocation_id）
        在新增参数缺省时 SQL 条件与现状一致，不受并行过滤影响"""
        executed = []
        with _patch_conn([{"id": DEVICE}, None], executed):
            repository.claim_next(
                DEVICE, TENANT, HASH, 60, provider_keys=["wecom"],
                execution_lane="session_task", expected_invocation_id="2",
            )
        sql = _select_sqls(executed)[0]
        assert "COALESCE(execution_lane, 'standard') = %s" in sql
        assert "id = %s" in sql
        assert "provider_key IS NOT NULL" not in sql
        assert "jsonb_array_elements_text" not in sql


class TestRuntimeClaimEndpoint:
    def test_claim_passes_parallel_only_and_normalized_tools(self):
        """端点 parallel_only=True：取设备 capabilities_json.parallel_tools 归一后传 claim_next"""
        device = {
            "id": DEVICE,
            "tenant_id": TENANT,
            "capabilities_json": {
                "parallel_tools": {"wecom": ["wecom_read_session", "wecom_read_sessions"], "boss-recruiting": ["bad"]},
            },
        }
        captured = {}

        def fake_claim_next(device_id, tenant_id, token_hash, lease, provider_keys=None,
                            execution_lane="standard", expected_invocation_id=None,
                            parallel_only=False, parallel_tools=None):
            captured.update(device_id=device_id, tenant_id=tenant_id, parallel_only=parallel_only,
                            parallel_tools=parallel_tools, execution_lane=execution_lane)
            return None

        with patch.object(local_tools_api.repository, "claim_next", fake_claim_next), \
             patch.object(local_tools_api.repository, "expire_stale_claims", lambda: None):
            result = asyncio.run(local_tools_api.runtime_claim(wait=0, parallel_only=True, device=device))

        assert result == {"success": True, "invocation": None}
        assert captured["parallel_only"] is True
        # 通道参数不得被并行参数挤占（keyword 传参锚定）
        assert captured["execution_lane"] == "standard"
        # 合法形态（字符串列表）逐键保留，不做名单校验（设备上报什么就按什么过滤）
        assert captured["parallel_tools"] == {"wecom": ["wecom_read_session", "wecom_read_sessions"], "boss-recruiting": ["bad"]}

    def test_claim_default_no_parallel_filter(self):
        """缺省端点：parallel_only=False，不向 repository 传并行映射"""
        captured = {}

        def fake_claim_next(device_id, tenant_id, token_hash, lease, provider_keys=None,
                            execution_lane="standard", expected_invocation_id=None,
                            parallel_only=False, parallel_tools=None):
            captured.update(parallel_only=parallel_only, parallel_tools=parallel_tools)
            return None

        device = {"id": DEVICE, "tenant_id": TENANT, "capabilities_json": {"parallel_tools": {"wecom": ["wecom_read_session"]}}}
        with patch.object(local_tools_api.repository, "claim_next", fake_claim_next), \
             patch.object(local_tools_api.repository, "expire_stale_claims", lambda: None):
            asyncio.run(local_tools_api.runtime_claim(wait=0, device=device))

        assert captured["parallel_only"] is False
        assert captured["parallel_tools"] is None

    def test_claim_malformed_capabilities_degrade_to_empty(self):
        """capabilities_json.parallel_tools 形态异常（非 dict/值非 list）：归一为空映射，不抛错"""
        captured = {}

        def fake_claim_next(device_id, tenant_id, token_hash, lease, provider_keys=None,
                            execution_lane="standard", expected_invocation_id=None,
                            parallel_only=False, parallel_tools=None):
            captured.update(parallel_tools=parallel_tools)
            return None

        for bad in ({"parallel_tools": "oops"}, {"parallel_tools": {"wecom": "not-a-list"}}, None):
            device = {"id": DEVICE, "tenant_id": TENANT, "capabilities_json": bad}
            with patch.object(local_tools_api.repository, "claim_next", fake_claim_next), \
                 patch.object(local_tools_api.repository, "expire_stale_claims", lambda: None):
                asyncio.run(local_tools_api.runtime_claim(wait=0, parallel_only=True, device=device))
            assert captured["parallel_tools"] == {}


class TestHeartbeatParallelClaim:
    def test_heartbeat_response_carries_parallel_claim(self):
        """心跳响应携带 parallel_claim=True（常量，服务端实现即恒真；旧客户端忽略）"""
        device_row = {"selected": True}
        with patch.object(local_tools_api.repository, "touch_device_seen", lambda *a, **k: device_row):
            result = asyncio.run(local_tools_api.runtime_heartbeat(
                local_tools_api.HeartbeatRequest(runtime_version="test", capabilities={"providers": ["wecom"]}, manifest_digest="d"),
                device={"id": DEVICE, "tenant_id": TENANT},
            ))
        assert result["parallel_claim"] is True
        assert result["success"] is True
