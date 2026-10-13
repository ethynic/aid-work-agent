"""权益门禁与部分筛选回执；数据库和终端派发全部隔离。"""
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.local_tools.proxy_tool import BossFilterTool, LocalToolProxyTool, _job_filter_defaults, _job_option_description

pytestmark = pytest.mark.unit
REF = "ac14fdfd-9498-4bc5-a00f-b849c2df2b88"
ACCOUNT = "a" * 64
ARGS = {"filters": [{"label": "经验要求", "options": ["在校/应届", "1年以内"]}, {"label": "性别", "options": ["男"]}], "options_ref": REF}


def invocation(data, args=None):
    return {"id": REF, "state": "succeeded", "effect": "applied", "arguments_json": args or ARGS, "result_json": {"data": data}}


def receipt():
    return {"filter_schema_version": 3, "membership": {"status": "non_vip", "source": "paywall"},
            "applied_rows": [ARGS["filters"][0]], "skipped_filters": [{"label": "性别", "options": ["男"], "reason": "paywall"}], "first_degree_only": False}


def test_partial_nonvip_receipt_only_accepts_actual_ordinary_and_explicit_vip_skips():
    data = receipt()
    assert BossFilterTool()._map_terminal(invocation(data))["success"]
    variants = [
        {**data, "skipped_filters": None}, {**data, "applied_rows": []},
        {**data, "membership": {"status": "unknown"}}, {**data, "membership": {"status": "vip"}},
        {**data, "skipped_filters": [{"label": "经验要求", "reason": "paywall"}]},
        {**data, "skipped_filters": [{"label": "性别", "reason": "unsupported"}]},
        {**data, "skipped_filters": [*data["skipped_filters"], *data["skipped_filters"]]},
        {**data, "applied_rows": [*data["applied_rows"], ARGS["filters"][1]]},
        {**data, "filter_schema_version": 2},
        {**data, "filter_schema_version": 4},
        {**data, "skipped_filters": [{"label": None, "reason": "paywall"}]},
        {**data, "skipped_filters": [{"label": 1, "reason": "paywall"}]},
        {**data, "skipped_filters": [{"label": None, "reason": "paywall"}, *data["skipped_filters"]]},
        {**data, "skipped_filters": [None, *data["skipped_filters"]]},
        {**data, "skipped_filters": [{"label": "性别", "options": ["女"], "reason": "paywall"}]},
    ]
    for bad in variants:
        result = BossFilterTool()._map_terminal(invocation(bad))
        assert result["code"] == "EXECUTION_UNKNOWN"
        assert result["effect"] == "unknown"


def test_partial_skip_never_masks_omitted_legacy_ordinary_fields():
    args = {**ARGS, "experience": "3-5年", "educations": ["本科"], "salary": "15-25K"}
    data = receipt()
    assert not BossFilterTool()._map_terminal(invocation(data, args))["success"]
    data["applied_rows"] = [{"label": "经验要求", "options": ["3-5年"]}, {"label": "学历要求", "options": ["本科"]}, {"label": "薪资待遇[单选]", "options": ["20-50K"]}]
    args["filters"] = [ARGS["filters"][1]]
    assert not BossFilterTool()._map_terminal(invocation(data, args))["success"]
    data["substitutions"] = [{"row": "薪资待遇", "requested": "15-25K", "matched": "20-50K"}]
    assert BossFilterTool()._map_terminal(invocation(data, args))["success"]
    for index in range(3):
        bad = {**data, "applied_rows": [r for i, r in enumerate(data["applied_rows"]) if i != index]}
        assert not BossFilterTool()._map_terminal(invocation(bad, args))["success"]


def test_legacy_salary_unit_variants_match_cli_normalization_without_substitution():
    args = {"salary": "15k-20k", "filters": [ARGS["filters"][1]], "options_ref": REF}
    data = receipt()
    data["applied_rows"] = [{"label": "薪资待遇[单选]", "options": ["15-20K"]}]
    assert BossFilterTool()._map_terminal(invocation(data, args))["success"]


def test_false_first_degree_does_not_require_vip_for_ordinary_only_request():
    args = {"educations": ["本科"], "first_degree_only": False, "options_ref": REF}
    data = {"filter_schema_version": 3, "membership": {"status": "unknown"}, "applied_rows": [{"label": "学历要求", "options": ["本科"]}], "first_degree_only": False}
    assert BossFilterTool()._map_terminal(invocation(data, args))["success"]


def test_nonvip_skipped_option_whitespace_matches_cli_trimmed_receipt():
    args = {**ARGS, "filters": [ARGS["filters"][0], {"label": "性别", "options": [" 男 "]}]}
    assert BossFilterTool()._map_terminal(invocation(receipt(), args))["success"]


def test_unknown_schema_is_rejected_even_for_legacy_ordinary_only_receipt():
    result = BossFilterTool()._map_terminal(invocation({"filter_schema_version": 4, "applied_rows": [{"label": "学历要求", "options": ["本科"]}]}, {"educations": ["本科"]}))
    assert result["code"] == "EXECUTION_UNKNOWN"


def test_salary_execution_projection_preserves_configured_decimal_precision():
    value = _job_filter_defaults({"salary_range": {"min": 20.00001}})
    assert value["ordinary"]["salary"] == "20.00001K以上"


def test_age_and_first_degree_receipts_cannot_silently_omit_or_fake_success():
    args = {**ARGS, "age": {"min": 25, "max": None}, "first_degree_only": True}
    data = receipt()
    data["skipped_filters"] += [{"label": "年龄", "range": args["age"], "reason": "paywall"}, {"label": "只看第一学历", "options": ["true"], "reason": "paywall"}]
    assert BossFilterTool()._map_terminal(invocation(data, args))["success"]
    assert not BossFilterTool()._map_terminal(invocation({**data, "first_degree_only": True}, args))["success"]
    assert not BossFilterTool()._map_terminal(invocation({**data, "age": args["age"]}, args))["success"]
    for bad_options in (["false"], None, [], True):
        bad = {**data, "skipped_filters": [*data["skipped_filters"][:-1], {"label": "只看第一学历", "options": bad_options, "reason": "paywall"}]}
        assert not BossFilterTool()._map_terminal(invocation(bad, args))["success"]
    vip = {"filter_schema_version": 3, "membership": {"status": "vip", "source": "user_hint"}, "applied_rows": ARGS["filters"], "age": args["age"], "first_degree_only": True}
    assert BossFilterTool()._map_terminal(invocation(vip, args))["success"]
    for bad in [{**vip, "first_degree_only": False}, {**vip, "age": {"min": 26, "max": None}}, {**vip, "membership": {"status": "unknown"}}]:
        assert not BossFilterTool()._map_terminal(invocation(bad, args))["success"]


@pytest.mark.asyncio
@pytest.mark.parametrize("version,fields,allowed", [(2, ["filters", "age"], False), (3, ["filters", "age", "membership_hint", "membership_account_ref", "first_degree_only"], True), (3, ["filters", "age"], False)])
async def test_new_fields_never_dispatched_to_old_or_incomplete_provider(version, fields, allowed):
    probe = {"tenant_id": "t", "user_id": "u", "device_id": "d", "session_id": "s", "tool_name": "boss_filter_options", "state": "succeeded", "created_at": datetime.now(), "result_json": {"data": {"filter_schema_version": version, "probe_complete": True, "supported_fields": fields, "account_ref": ACCOUNT}}}
    args = {**ARGS, "membership_hint": "vip", "membership_account_ref": ACCOUNT, "first_degree_only": True}
    with patch("src.local_tools.proxy_tool.repository.get_invocation", return_value=probe), patch.object(LocalToolProxyTool, "_dispatch_and_wait", new=AsyncMock(return_value={"success": True})) as dispatch:
        result = await BossFilterTool()._dispatch_and_wait("t", "u", "s", {"id": "d"}, args, None)
    assert bool(result.get("success")) is allowed
    assert dispatch.await_count == int(allowed)
    if allowed:
        assert dispatch.await_args.args[4] == args


def test_new_args_use_strict_boolean_and_hint_and_need_bound_options_reference():
    tool = BossFilterTool()
    assert tool._validate_args({**ARGS, "membership_hint": "vip", "membership_account_ref": ACCOUNT, "first_degree_only": True}) is None
    for bad in [{"salary": "10-20K", "membership_hint": "vip"}, {**ARGS, "membership_hint": "unknown"}, {**ARGS, "first_degree_only": "true"}, {**ARGS, "first_degree_only": 1}]:
        assert tool._validate_args(bad)


@pytest.mark.asyncio
async def test_membership_hint_is_bound_to_probe_account_and_verified_in_receipt():
    args = {**ARGS, "membership_hint": "non_vip", "membership_account_ref": ACCOUNT}
    probe = {"tenant_id": "t", "user_id": "u", "device_id": "d", "session_id": "s", "tool_name": "boss_filter_options", "state": "succeeded", "created_at": datetime.now(), "result_json": {"data": {"filter_schema_version": 3, "probe_complete": True, "supported_fields": ["filters", "membership_hint", "membership_account_ref"], "account_ref": "b" * 64}}}
    for candidate in (args, {key: value for key, value in args.items() if key != "membership_hint"}):
        with patch("src.local_tools.proxy_tool.repository.get_invocation", return_value=probe), patch.object(LocalToolProxyTool, "_dispatch_and_wait", new=AsyncMock()) as dispatch:
            result = await BossFilterTool()._dispatch_and_wait("t", "u", "s", {"id": "d"}, candidate, None)
        assert result["code"] == "FILTER_CAPABILITY_REQUIRED"
        dispatch.assert_not_awaited()
    data = {**receipt(), "account_ref": ACCOUNT}
    assert BossFilterTool()._map_terminal(invocation(data, args))["success"]
    assert not BossFilterTool()._map_terminal(invocation({**data, "account_ref": "b" * 64}, args))["success"]


def test_job_defaults_preserve_all_dimensions_and_separate_scoring_keywords():
    requirements = {"experiences": ["在校/应届", "1年以内"], "educations": ["本科", "硕士"], "job_statuses": ["离职-随时到岗"], "salary_range": {"min": 15, "max": 25}, "keywords": ["LLM评分"], "notes": "补充", "vip_filters": {"age": {"min": 25, "max": None}, "gender": "男", "activity": "不限", "schools": ["985", "211"], "first_degree_only": True, "boss_keywords": ["HTML", "MYSQL"]}}
    defaults = _job_filter_defaults(requirements)
    assert defaults["ordinary"]["filters"] == [{"label": "经验要求", "options": ["在校/应届", "1年以内"]}, {"label": "求职状态", "options": ["离职-随时到岗"]}]
    assert defaults["ordinary"]["salary"] == "15-25K"
    assert defaults["ordinary"]["educations"] == ["本科", "硕士"]
    assert defaults["vip"] == {"filters": [{"label": "性别", "options": ["男"]}, {"label": "院校", "options": ["985", "211"]}, {"label": "牛人关键词", "options": ["HTML", "MYSQL"]}], "age": {"min": 25, "max": None}, "first_degree_only": True}
    assert "含VIP专享条件" in _job_option_description({"job_requirements": requirements}, 3, 1)
    assert "15-25K" in _job_option_description({"job_requirements": requirements}, 3, 1)
    assert _job_filter_defaults({"experience": "3-5年", "salary": "50K以上"})["ordinary"] == {"filters": [{"label": "经验要求", "options": ["3-5年"]}], "salary": "50K以上"}
