"""扩展筛选参数与设备能力门禁；不连接数据库、浏览器或真实设备。"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.local_tools.proxy_tool import BossFilterTool, LocalToolProxyTool

pytestmark = pytest.mark.unit
REF = "ac14fdfd-9498-4bc5-a00f-b849c2df2b88"
ARGS = {"experience": "1年以内", "filters": [{"label": "性别", "options": ["男"]}], "options_ref": REF}


def probe(**changes):
    value = {"tenant_id": "t", "user_id": "u", "device_id": "d", "session_id": "s", "tool_name": "boss_filter_options",
             "state": "succeeded", "created_at": datetime.now(), "result_json": {"data": {
                 "filter_schema_version": 2, "probe_complete": True, "supported_fields": ["filters", "age"]}}}
    value.update(changes)
    return value


def test_filter_parameters_require_reference_and_do_not_allow_fake_range_options():
    tool = BossFilterTool()
    assert tool._validate_args({"salary": "10-20K"}) is None
    assert tool._validate_args({"filters": [{"label": "性别", "options": ["男"]}]})
    assert tool._validate_args(ARGS) is None
    assert tool._validate_args({"age": {"min": 25, "max": None}, "options_ref": REF}) is None
    for args in [{"age": {"min": 46}, "options_ref": REF}, {"age": {"min": None}, "options_ref": REF},
                 {"filters": [{"label": "性别", "options": [" "]}], "options_ref": REF},
                 {"salary": "10-20K", "unexpected": True}, {**ARGS, "options_ref": "bad"}]:
        assert tool._validate_args(args)


def test_filter_keeps_owned_device_and_original_invocation_registration():
    from src.local_tools.domain_flow import supports_owned_device_class, supports_owned_tool
    from src.local_tools.durable_flow import supports_original_invocation
    tool = BossFilterTool()
    assert type(tool).execute is LocalToolProxyTool.execute
    assert supports_original_invocation(tool)
    assert supports_owned_device_class(type(tool))
    assert supports_owned_tool(tool)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"user_id": "other"}, {"device_id": "other"}, {"session_id": "other"},
    {"tool_name": "boss_list_jobs"}, {"state": "failed"}, {"created_at": datetime.now() - timedelta(minutes=11)},
    {"result_json": {"data": {"rows": []}}},
    {"result_json": {"data": {"filter_schema_version": 2, "probe_complete": False, "supported_fields": ["filters"]}}},
])
async def test_old_incomplete_or_wrong_identity_probe_never_dispatches(changes):
    with patch("src.local_tools.proxy_tool.repository.get_invocation", return_value=probe(**changes)) as get, \
            patch.object(LocalToolProxyTool, "_dispatch_and_wait", new=AsyncMock()) as dispatch:
        result = await BossFilterTool()._dispatch_and_wait("t", "u", "s", {"id": "d"}, ARGS, None)
    assert result["code"] == "FILTER_CAPABILITY_REQUIRED"
    assert result["effect"] == "none"
    get.assert_called_once_with(REF, "t")
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_device_probe_preserves_reference_in_durable_intent_and_replay_arguments():
    with patch("src.local_tools.proxy_tool.repository.get_invocation", return_value=probe()), \
            patch.object(LocalToolProxyTool, "_dispatch_and_wait", new=AsyncMock(return_value={"success": True})) as dispatch:
        result = await BossFilterTool()._dispatch_and_wait("t", "u", "s", {"id": "d"}, ARGS, None)
    assert result["success"] is True
    assert dispatch.await_args.args[4] == ARGS


@pytest.mark.asyncio
async def test_age_requires_age_capability_and_query_failure_does_not_dispatch():
    args = {"age": {"min": 25, "max": None}, "options_ref": REF}
    for side_effect, value in [(None, probe(result_json={"data": {"filter_schema_version": 2, "probe_complete": True, "supported_fields": ["filters"]}})),
                               (RuntimeError("db unavailable"), None)]:
        with patch("src.local_tools.proxy_tool.repository.get_invocation", return_value=value, side_effect=side_effect), \
                patch.object(LocalToolProxyTool, "_dispatch_and_wait", new=AsyncMock()) as dispatch:
            result = await BossFilterTool()._dispatch_and_wait("t", "u", "s", {"id": "d"}, args, None)
        assert result["code"] == "FILTER_CAPABILITY_REQUIRED"
        dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_executor_nested_models_are_json_before_legacy_or_durable_intent():
    from src.tools.executor import ToolExecutor
    tool = BossFilterTool()
    registry = MagicMock()
    registry.get_tool.return_value = tool
    args = {**ARGS, "age": {"min": 25, "max": None}, "_trusted_tenant_id": "t", "_trusted_user_id": "u", "_session_id": "s"}
    # 此处父execute是两条通道建立意图的共同入口；检查它收到的是JSON而非BaseModel。
    with patch.object(LocalToolProxyTool, "execute", new=AsyncMock(return_value={"success": True})) as execute:
        result = await ToolExecutor(registry).execute("boss_filter", args)
    assert result["success"] is True
    received = execute.await_args.kwargs
    assert received["filters"] == ARGS["filters"]
    assert received["age"] == {"min": 25, "max": None}
    assert received["options_ref"] == REF
    import json
    json.dumps({key: val for key, val in received.items() if not key.startswith("_")})


def test_provider_extended_success_requires_version_and_actual_rows_not_only_badge_count():
    tool = BossFilterTool()
    invocation = {"id": REF, "state": "succeeded", "effect": "applied", "arguments_json": ARGS,
                  "result_json": {"data": {"filter_count": 4, "filter_schema_version": 2,
                      "applied_rows": [{"label": "性别", "options": ["男"]}]}}}
    assert tool._map_terminal(invocation)["success"] is True
    for data in [{"filter_count": 4}, {"filter_schema_version": 2, "applied_rows": [{"label": "性别", "options": ["女"]}]},
                 {"filter_schema_version": 2, "applied_rows": 1}, {"filter_schema_version": 2, "applied_rows": "bad"}]:
        failed = tool._map_terminal({**invocation, "result_json": {"data": data}})
        assert failed["code"] == "EXECUTION_UNKNOWN"
        assert failed["effect"] == "unknown"
    age = {"age": {"min": 25, "max": None}, "options_ref": REF}
    data = {"filter_schema_version": 2, "age": {"min": 25, "max": None}}
    assert tool._map_terminal({**invocation, "arguments_json": age, "result_json": {"data": data}})["success"] is True
