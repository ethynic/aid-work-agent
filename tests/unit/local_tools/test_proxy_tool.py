"""LocalToolProxy 单元测试（mock repository，无真实 DB）

覆盖（对应 m05-implementation-spec.md §7）：
- 设备闸门各分支（无设备/未选定/离线/未配对 provider）→ 明确引导，不建 invocation
- 授权上限：greet limit=4 / accept limit=2 → Pydantic 校验拒绝
- 终态映射：succeeded/failed/unknown/cancelled → 工具结果；effect=unknown message 含「禁止重试」
- 事件 → 进度队列：events 按 seq 转成 progress 文本
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from src.local_tools import catalog
from src.local_tools.proxy_tool import (
    LOCAL_PROXY_TOOL_CLASSES,
    LOCAL_PROXY_TOOL_NAMES,
    BossAcceptResumeTool,
    BossFilterTool,
    BossGotoTool,
    BossGreetTool,
    BossJobsListTool,
    BossSelectJobTool,
)
from src.tools.base import ExecutionTarget

pytestmark = pytest.mark.unit

TENANT = "tenant_t1"
USER = "user_t1"
REPO = "src.local_tools.proxy_tool.repository"


def _online_device(selected=True, status="active", provider=True, last_seen=None):
    return {
        "id": "dev-1",
        "selected": selected,
        "status": status,
        "last_seen_at": last_seen or datetime.now(),
        "capabilities_json": {"provider_id": "ai.aidwork.boss-recruiting"} if provider else {},
    }


def _kwargs(**extra):
    return {"_trusted_tenant_id": TENANT, "_trusted_user_id": USER, **extra}


def _patch_repo(devices, invocation=None, events=None):
    create_invocation = MagicMock(return_value="inv-1")
    request_cancel = MagicMock(return_value=True)
    patches = patch.multiple(
        REPO,
        list_devices=MagicMock(return_value=devices),
        create_invocation=create_invocation,
        list_events=MagicMock(return_value=events or []),
        get_invocation=MagicMock(return_value=invocation),
        request_cancel=request_cancel,
    )
    return patches, create_invocation, request_cancel


class TestToolDefinitions:
    def test_all_tools_local_required(self):
        """16 个 proxy 工具全部 LOCAL_REQUIRED + local_boss 分类，名称与受信 manifest 一致

        含 Phase 3 新增的 boss_list_jobs / boss_select_job / boss_jobs_list 与
        面试通知 Phase 1 的 boss_interview_notify；其中 boss_jobs_list /
        boss_interview_notify 是混合模式（云端执行逻辑 + 代理注册），execution_target 仍 LOCAL_REQUIRED
        """
        assert len(LOCAL_PROXY_TOOL_CLASSES) == 16
        assert LOCAL_PROXY_TOOL_NAMES == set(catalog.allowed_tools("boss-recruiting"))
        for cls in LOCAL_PROXY_TOOL_CLASSES:
            tool = cls()
            assert tool.execution_target == ExecutionTarget.LOCAL_REQUIRED
            assert tool.category == "local_boss"
            assert tool.display_name
            assert tool.description

    def test_select_job_requires_job_name(self):
        """boss_select_job 的 job_name 必填（min_length=1），空名拒绝"""
        from pydantic import ValidationError as VE

        with pytest.raises(VE):
            BossSelectJobTool.InputModel(job_name="")
        tool = BossSelectJobTool()
        assert tool.validate_parameters(job_name="PHP开发工程师（Laravel）") is True
        assert tool.validate_parameters() is False  # 缺 job_name
        assert tool.validate_parameters(job_name="") is False


class TestBossJobsListCloudMode:
    """boss_jobs_list 混合模式：execute 纯云端查询，不查设备、不建 invocation"""

    @staticmethod
    def _jobs(*rows):
        return [dict(r) for r in rows]

    async def test_missing_identity(self):
        result = await BossJobsListTool().execute()
        assert result["success"] is False
        assert result["code"] == "NO_IDENTITY"

    async def test_cloud_query_never_touches_device_or_invocation(self):
        """云端分支：只查 job service（list_jobs 已附统计），全程不碰 repository（设备闸门/invocation）"""
        jobs = self._jobs(
            {"id": "job-1", "job_name": "PHP开发工程师（Laravel）", "status": "active",
             "match_threshold": 70, "job_requirements": {"experience": "3-5年"},
             "resume_count": 3, "matched_count": 1},
            {"id": "job-2", "job_name": "Java后端", "status": "paused",
             "match_threshold": 75, "job_requirements": None,
             "resume_count": 0, "matched_count": 0},
        )
        with patch("src.local_tools.proxy_tool.recruiting_job_service.list_jobs",
                   return_value=jobs) as mock_list, \
             patch(f"{REPO}.list_devices") as mock_devices, \
             patch(f"{REPO}.create_invocation") as mock_create:
            result = await BossJobsListTool().execute(**_kwargs())
        assert result["success"] is True
        mock_list.assert_called_once_with(TENANT)
        mock_devices.assert_not_called()
        mock_create.assert_not_called()
        # paused 过滤：只返回 active
        assert result["data"]["jobs"] == [
            {"job_id": "job-1", "job_name": "PHP开发工程师（Laravel）", "status": "active",
             "match_threshold": 70, "job_requirements": {"experience": "3-5年"},
             "resume_count": 3, "matched_count": 1},
        ]
        assert "PHP开发工程师（Laravel）" in result["message"]

    async def test_no_active_jobs_returns_empty_with_hint(self):
        """无 active 职位 → 空列表 + 引导去职位管理创建"""
        with patch("src.local_tools.proxy_tool.recruiting_job_service.list_jobs",
                   return_value=self._jobs(
                       {"id": "job-2", "job_name": "Java后端", "status": "paused",
                        "match_threshold": 75, "job_requirements": None,
                        "resume_count": 0, "matched_count": 0},
                   )):
            result = await BossJobsListTool().execute(**_kwargs())
        assert result["success"] is True
        assert result["data"]["jobs"] == []
        assert "职位管理" in result["message"]

    async def test_infra_error_returns_failed(self):
        """职位库查询异常 → FAILED 用户可读文案，不抛 psycopg2 原文"""
        with patch("src.local_tools.proxy_tool.recruiting_job_service.list_jobs",
                   side_effect=RuntimeError("relation does not exist")):
            result = await BossJobsListTool().execute(**_kwargs())
        assert result["success"] is False
        assert result["code"] == "FAILED"
        assert "职位库查询失败" in result["message"]


class TestDeviceGate:
    async def test_missing_identity(self):
        """缺受信身份 → NO_IDENTITY，不查设备"""
        tool = BossGotoTool()
        result = await tool.execute(target="recommend")
        assert result["success"] is False
        assert result["code"] == "NO_IDENTITY"

    async def test_no_device(self):
        """无任何设备 → DEVICE_UNAVAILABLE + 配对引导，不建 invocation"""
        patches, create_invocation, _ = _patch_repo(devices=[])
        with patches:
            result = await BossGotoTool().execute(**_kwargs(target="recommend"))
        assert result["success"] is False
        assert result["code"] == "DEVICE_UNAVAILABLE"
        assert "本地工具" in result["message"]
        create_invocation.assert_not_called()

    async def test_no_selected_device(self):
        """有设备但未选定 → 引导选定，不建 invocation"""
        patches, create_invocation, _ = _patch_repo(devices=[_online_device(selected=False)])
        with patches:
            result = await BossGotoTool().execute(**_kwargs(target="recommend"))
        assert result["code"] == "DEVICE_UNAVAILABLE"
        assert "选定" in result["message"]
        create_invocation.assert_not_called()

    async def test_device_offline(self):
        """选定设备但 last_seen 超过 30s → 引导启动 Runtime，不建 invocation"""
        device = _online_device(last_seen=datetime.now() - timedelta(seconds=120))
        patches, create_invocation, _ = _patch_repo(devices=[device])
        with patches:
            result = await BossGotoTool().execute(**_kwargs(target="recommend"))
        assert result["code"] == "DEVICE_UNAVAILABLE"
        assert "离线" in result["message"]
        create_invocation.assert_not_called()

    async def test_device_without_provider(self):
        """选定在线但 capabilities 不含 boss-recruiting provider → 不支持提示，不建 invocation"""
        patches, create_invocation, _ = _patch_repo(devices=[_online_device(provider=False)])
        with patches:
            result = await BossGotoTool().execute(**_kwargs(target="recommend"))
        assert result["code"] == "DEVICE_UNAVAILABLE"
        assert "不支持" in result["message"]
        create_invocation.assert_not_called()


class TestAuthCaps:
    def test_greet_limit_hard_cap(self):
        """greet 单次上限 3：limit=4 Pydantic 拒绝，limit=3 通过"""
        with pytest.raises(ValidationError):
            BossGreetTool.InputModel(limit=4)
        tool = BossGreetTool()
        assert tool.validate_parameters(limit=4) is False
        assert tool.validate_parameters(limit=3) is True
        assert tool.validate_parameters() is True  # 默认 1

    def test_greet_names_validation(self):
        """greet 定向名单：>3 拒绝、空串项拒绝、空列表拒绝、None 合法、合法名单通过"""
        # 超过 3 人 → 拒绝（授权上限不可突破）
        with pytest.raises(ValidationError):
            BossGreetTool.InputModel(names=["刘草威", "张三丰", "王五", "赵六"])
        # 含空串/纯空白项 → 拒绝
        with pytest.raises(ValidationError):
            BossGreetTool.InputModel(names=["刘草威", "  "])
        with pytest.raises(ValidationError):
            BossGreetTool.InputModel(names=[""])
        # 空列表 → 拒绝（定向必须至少 1 人）
        with pytest.raises(ValidationError):
            BossGreetTool.InputModel(names=[])
        tool = BossGreetTool()
        assert tool.validate_parameters(names=["刘草威", "张三丰", "王五", "赵六"]) is False
        assert tool.validate_parameters(names=["刘草威", " "]) is False
        # None（非定向）与合法名单（1-3 个非空姓名）均通过
        assert tool.validate_parameters() is True
        assert tool.validate_parameters(names=["刘草威", "张三丰"]) is True

    def test_greet_names_normalized_before_forward(self):
        """greet 定向名单透传前规整：strip + 去重（保持顺序），CLI 收到的是规整后名单"""
        model = BossGreetTool.InputModel(names=[" 刘草威 ", "张三丰", "刘草威"])
        assert model.names == ["刘草威", "张三丰"]

    def test_accept_resume_limit_hard_cap(self):
        """accept 单次上限 1：limit=2 Pydantic 拒绝"""
        with pytest.raises(ValidationError):
            BossAcceptResumeTool.InputModel(limit=2)
        tool = BossAcceptResumeTool()
        assert tool.validate_parameters(limit=2) is False
        assert tool.validate_parameters(limit=1) is True

    async def test_filter_requires_at_least_one_condition(self):
        """filter 至少一个条件：全空 → INVALID_ARGS，不建 invocation"""
        patches, create_invocation, _ = _patch_repo(devices=[_online_device()])
        with patches:
            result = await BossFilterTool().execute(**_kwargs())
        assert result["success"] is False
        assert result["code"] == "INVALID_ARGS"
        assert "筛选条件" in result["message"]
        create_invocation.assert_not_called()


class TestTerminalMapping:
    async def test_succeeded(self):
        """succeeded → success=True，透传 message/data/effect"""
        invocation = {
            "id": "inv-1", "state": "succeeded", "effect": "applied",
            "result_json": {"success": True, "message": "已切换", "data": {"page": "recommend"}},
        }
        patches, _, _ = _patch_repo([_online_device()], invocation)
        with patches:
            result = await BossGotoTool().execute(**_kwargs(target="recommend"))
        assert result["success"] is True
        assert result["message"] == "已切换"
        assert result["data"] == {"page": "recommend"}
        assert result["effect"] == "applied"
        assert result["invocation_id"] == "inv-1"

    async def test_failed_message_passthrough(self):
        """failed + PAYWALL → 中文文案直传（LLM 据此停止并说明）"""
        invocation = {
            "id": "inv-1", "state": "failed", "effect": "none",
            "error_code": "PAYWALL", "error_message": "该职位无免费打招呼权益，请人工处理",
            "result_json": {},
        }
        patches, _, _ = _patch_repo([_online_device()], invocation)
        with patches:
            result = await BossGreetTool().execute(**_kwargs(limit=1))
        assert result["success"] is False
        assert result["code"] == "PAYWALL"
        assert result["message"] == "该职位无免费打招呼权益，请人工处理"

    async def test_unknown_forbids_retry(self):
        """state=unknown → message 含「禁止重试」"""
        invocation = {
            "id": "inv-1", "state": "unknown", "effect": "unknown",
            "result_json": {"message": "本机执行结果无法确认"},
        }
        patches, _, _ = _patch_repo([_online_device()], invocation)
        with patches:
            result = await BossGreetTool().execute(**_kwargs(limit=1))
        assert result["success"] is False
        assert "禁止重试" in result["message"]

    async def test_failed_with_unknown_effect_appends_notice(self):
        """failed 但 effect=unknown → message 附加「禁止重试」"""
        invocation = {
            "id": "inv-1", "state": "failed", "effect": "unknown",
            "error_code": "UI_CHANGED", "error_message": "页面结构变化",
            "result_json": {},
        }
        patches, _, _ = _patch_repo([_online_device()], invocation)
        with patches:
            result = await BossGreetTool().execute(**_kwargs(limit=1))
        assert result["code"] == "UI_CHANGED"
        assert "禁止重试" in result["message"]

    async def test_cancelled(self):
        """cancelled → success=False + code=CANCELLED"""
        invocation = {"id": "inv-1", "state": "cancelled", "effect": "none", "result_json": {}}
        patches, _, _ = _patch_repo([_online_device()], invocation)
        with patches:
            result = await BossGotoTool().execute(**_kwargs(target="chat"))
        assert result["success"] is False
        assert result["code"] == "CANCELLED"


class TestProgressEvents:
    async def test_events_pushed_to_progress_queue(self):
        """events 按 seq 转成 progress 文本推入队列（首条为 started 含 invocation_id）"""
        import asyncio

        events = [
            {"seq": 1, "stage": "greet", "current": 1, "total": 3, "message": "招呼进度"},
            {"seq": 2, "stage": "greet", "current": 2, "total": 3, "message": "招呼进度"},
        ]
        invocation = {
            "id": "inv-1", "state": "succeeded", "effect": "applied",
            "result_json": {"success": True, "message": "完成"},
        }
        patches, _, _ = _patch_repo([_online_device()], invocation, events=events)
        queue = asyncio.Queue()
        with patches:
            result = await BossGreetTool().execute(**_kwargs(limit=2, _progress_queue=queue))
        assert result["success"] is True

        drained = []
        while not queue.empty():
            drained.append(queue.get_nowait())
        assert drained[0]["type"] == "started"
        assert drained[0]["invocation_id"] == "inv-1"
        texts = [e["text"] for e in drained if e.get("text")]
        assert any("1/3" in t for t in texts)
        assert any("2/3" in t for t in texts)

    async def test_timeout_requests_cancel(self):
        """超时 → request_cancel + 返回 TIMEOUT（proxy 本地码）"""
        patches, _, request_cancel = _patch_repo(
            [_online_device()],
            invocation={"id": "inv-1", "state": "running", "result_json": {}},
        )
        tool = BossGotoTool()
        tool.timeout_seconds = 1  # 缩短超时
        with patches:
            result = await tool.execute(**_kwargs(target="chat"))
        assert result["success"] is False
        assert result["code"] == "TIMEOUT"
        request_cancel.assert_called_once_with("inv-1", TENANT)
