"""skill_runner_proxy dispatch 单元测试（M2 云端执行路由，plan §4.2/§6）

覆盖：dispatch 组装 payload（§3.1 逐字段）/ invocation 行 tool_name 与
provider_key='skill-runner'（行级 claim 过滤键）；版本门第 1 层
（SKILL_NOT_INSTALLED / SKILL_VERSION_MISMATCH 不建 invocation + 文案与
version 不参与对账）；终态映射 → ExecutionResult 字段齐全（含 stdout 尾部
截断与 TIMEOUT→timed_out/request_cancel）；派发器注册面与基类语义继承
（heal 不触发 / unsupported_provider_message / NO_IDENTITY）。

全部 mock repository（无真实 DB，无真实设备），不触碰真实 storage/。
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import settings
from src.local_tools import catalog
from src.local_tools.proxy_tool import LOCAL_PROXY_TOOL_CLASSES, LOCAL_PROXY_TOOL_NAMES
from src.local_tools.skill_runner_proxy import (
    SKILL_RUNNER_PROVIDER_KEY,
    SKILL_RUNNER_TOOL_NAME,
    SkillScriptRunDispatchTool,
    dispatch_device_skill_script,
)
from src.tools.base import ExecutionTarget

pytestmark = pytest.mark.unit

TENANT = "tenant_t1"
USER = "user_t1"
REPO = "src.local_tools.repository"
PYTEST_POLL_INTERVAL = "src.local_tools.proxy_tool.POLL_INTERVAL_SECONDS"
APPROVED_HASH = "a" * 64


def _skill_ready_device(selected=True, status="active", last_seen=None, skills=None,
                        providers=("skill-runner",)):
    caps: dict = {"providers": list(providers), "protocol_version": 2}
    if skills is not None:
        caps["skills"] = skills
    return {
        "id": "dev-1",
        "selected": selected,
        "status": status,
        "last_seen_at": last_seen or datetime.now(),
        "capabilities_json": caps,
    }


def _succeeded_invocation(inv_id="inv-1", stdout="flow ok", stderr="", exit_code=0,
                          duration_ms=1500):
    return {
        "id": inv_id,
        "state": "succeeded",
        "effect": "applied",
        "result_json": {
            "code": None,
            "message": "执行成功",
            "data": {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
            },
        },
        "credit_cost": None,
    }


def _failed_invocation(inv_id="inv-1", code="DESKTOP_NOT_INTERACTIVE", message="桌面处于锁屏状态"):
    return {
        "id": inv_id,
        "state": "failed",
        "effect": None,
        "error_code": code,
        "error_message": message,
        "result_json": {"code": code, "message": message, "data": None},
        "credit_cost": None,
    }


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


def _dispatch(**overrides):
    kwargs = dict(
        skill="vec-skill",
        entry="scripts/flow.py",
        args=["seq", "verify"],
        exec_hash=APPROVED_HASH,
        version="2.0.0",
        tenant_id=TENANT,
        user_id=USER,
        session_id="sess-1",
    )
    kwargs.update(overrides)
    return dispatch_device_skill_script(**kwargs)


class TestDispatchPayloadConstruction:
    async def test_dispatch_assembles_payload_per_contract(self):
        """payload §3.1 逐字段：skill/version/entry/args/exec_hash/timeout_seconds；
        invocation 行 tool_name='skill_script_run'、provider_key='skill-runner'"""
        devices = [_skill_ready_device(skills=[{"name": "vec-skill", "hash": APPROVED_HASH}])]
        patches, create_invocation, _ = _patch_repo(devices, invocation=_succeeded_invocation())
        with patches:
            result = await _dispatch()
        assert result.success is True
        create_invocation.assert_called_once()
        call = create_invocation.call_args
        # create_invocation(tenant, user, device, tool_name, arguments, session, provider_key=...)
        assert call.args[0] == TENANT and call.args[1] == USER
        assert call.args[3] == SKILL_RUNNER_TOOL_NAME
        arguments = call.args[4]
        assert arguments == {
            "skill": "vec-skill",
            "version": "2.0.0",
            "entry": "scripts/flow.py",
            "args": ["seq", "verify"],
            "exec_hash": APPROVED_HASH,
            "timeout_seconds": int(settings.skills.plugins.device_execution.timeout_seconds),
        }
        assert call.kwargs["provider_key"] == SKILL_RUNNER_PROVIDER_KEY

    async def test_catalog_allows_tool_for_device_routing(self):
        """catalog 白名单放行（设备闸门 device_ready_error 消费路径）"""
        assert catalog.is_tool_allowed(SKILL_RUNNER_PROVIDER_KEY, SKILL_RUNNER_TOOL_NAME)


class TestVersionGateBeforeEnqueue:
    async def test_skill_not_installed_no_invocation(self):
        """设备清单无该技能名 → SKILL_NOT_INSTALLED（不建 invocation）+ 安装引导文案"""
        devices = [_skill_ready_device(skills=[{"name": "other-skill", "hash": "x"}])]
        patches, create_invocation, _ = _patch_repo(devices, invocation=None)
        with patches:
            result = await _dispatch()
        assert result.success is False
        assert result.error == "SKILL_NOT_INSTALLED"
        assert "尚未安装" in result.stderr
        assert "设备端" in result.stderr
        create_invocation.assert_not_called()

    async def test_device_without_skills_manifest_treated_as_not_installed(self):
        """capabilities 无 skills 清单（老 Runtime / 未配置 skills.python）→ 按未安装 fail-closed"""
        devices = [_skill_ready_device(skills=None)]
        patches, create_invocation, _ = _patch_repo(devices, invocation=None)
        with patches:
            result = await _dispatch()
        assert result.success is False
        assert result.error == "SKILL_NOT_INSTALLED"
        create_invocation.assert_not_called()

    async def test_hash_mismatch_rejected_no_invocation(self):
        """设备 hash != 审批 exec_hash → SKILL_VERSION_MISMATCH（不建 invocation）"""
        devices = [_skill_ready_device(skills=[{"name": "vec-skill", "hash": "b" * 64}])]
        patches, create_invocation, _ = _patch_repo(devices, invocation=None)
        with patches:
            result = await _dispatch()
        assert result.success is False
        assert result.error == "SKILL_VERSION_MISMATCH"
        assert "不一致" in result.stderr
        create_invocation.assert_not_called()

    async def test_version_field_not_compared(self):
        """version 仅日志/展示不参与对账：设备清单条目无 version 字段、hash 一致 → 放行"""
        devices = [_skill_ready_device(skills=[{"name": "vec-skill", "hash": APPROVED_HASH}])]
        patches, create_invocation, _ = _patch_repo(devices, invocation=_succeeded_invocation())
        with patches:
            result = await _dispatch(version="9.9.9-tampered")
        assert result.success is True
        create_invocation.assert_called_once()


class TestDispatchResultMapping:
    async def test_succeeded_maps_all_execution_result_fields(self):
        """succeeded → ExecutionResult 字段齐全（stdout/stderr/exit_code/duration=duration_ms→秒）"""
        devices = [_skill_ready_device(skills=[{"name": "vec-skill", "hash": APPROVED_HASH}])]
        invocation = _succeeded_invocation(stdout="step1 ok\nstep2 ok", stderr="warn", exit_code=0, duration_ms=1500)
        patches, _, _ = _patch_repo(devices, invocation=invocation)
        with patches:
            result = await _dispatch()
        assert result.success is True
        assert result.stdout == "step1 ok\nstep2 ok"
        assert result.stderr == "warn"
        assert result.exit_code == 0
        assert result.duration == pytest.approx(1.5)
        assert result.timed_out is False
        assert result.error is None

    async def test_failed_maps_code_and_message(self):
        """failed → error=设备端错误码、stderr=中文文案、exit_code=-1、timed_out=False"""
        devices = [_skill_ready_device(skills=[{"name": "vec-skill", "hash": APPROVED_HASH}])]
        patches, _, _ = _patch_repo(devices, invocation=_failed_invocation())
        with patches:
            result = await _dispatch()
        assert result.success is False
        assert result.error == "DESKTOP_NOT_INTERACTIVE"
        assert "锁屏" in result.stderr
        assert result.exit_code == -1
        assert result.timed_out is False

    async def test_timeout_requests_cancel_and_maps_timed_out(self, monkeypatch):
        """轮询超时 → request_cancel 被调（沿 proxy 语义）+ TIMEOUT 结果 → timed_out=True/error=TIMEOUT"""
        monkeypatch.setattr(settings.skills.plugins.device_execution, "timeout_seconds", 1)
        monkeypatch.setattr(PYTEST_POLL_INTERVAL, 0.05)
        devices = [_skill_ready_device(skills=[{"name": "vec-skill", "hash": APPROVED_HASH}])]
        # get_invocation 恒 queued（设备不回传终态）→ 轮询至超时
        patches, create_invocation, request_cancel = _patch_repo(
            devices, invocation={"id": "inv-1", "state": "queued"})
        with patches:
            result = await _dispatch()
        create_invocation.assert_called_once()
        request_cancel.assert_called()
        assert result.success is False
        assert result.error == "TIMEOUT"
        assert result.timed_out is True
        assert "超时" in result.stderr

    async def test_stdout_tail_truncation(self, monkeypatch):
        """stdout 超长 → 尾部保留截断（max_stdout_chars，与设备端截断策略一致）"""
        monkeypatch.setattr(settings.skills.plugins.device_execution, "max_stdout_chars", 10)
        devices = [_skill_ready_device(skills=[{"name": "vec-skill", "hash": APPROVED_HASH}])]
        invocation = _succeeded_invocation(stdout="0123456789ABCDEF")
        patches, _, _ = _patch_repo(devices, invocation=invocation)
        with patches:
            result = await _dispatch()
        assert result.stdout == "6789ABCDEF"  # 尾部 10 字符


class TestDispatchSemanticsInheritance:
    def test_internal_dispatcher_registration_surface(self):
        """内部派发器不注册 LLM 可见面：catalog=False、不在 LOCAL_PROXY_TOOL_CLASSES/NAMES
        （SUBAGENT 白名单按名称交集注册面不受影响——skill_script_run 不在任何白名单）；
        invocation 行路由键 provider_key='skill-runner'（行级 claim 过滤只派 skill-runner 设备）"""
        assert SkillScriptRunDispatchTool.catalog is False
        assert SkillScriptRunDispatchTool.execution_target == ExecutionTarget.LOCAL_REQUIRED
        assert SkillScriptRunDispatchTool not in LOCAL_PROXY_TOOL_CLASSES
        assert SKILL_RUNNER_TOOL_NAME not in LOCAL_PROXY_TOOL_NAMES
        assert SkillScriptRunDispatchTool.provider_key == SKILL_RUNNER_PROVIDER_KEY
        assert SkillScriptRunDispatchTool.invocation_provider_key == SKILL_RUNNER_PROVIDER_KEY

    def test_heal_not_eligible(self):
        """弹层自愈是 BOSS 页面编排，skill-runner 派发器绝不触发"""
        assert SkillScriptRunDispatchTool.heal_eligible is False

    async def test_unsupported_provider_guidance_message(self):
        """选定设备 capabilities 不含 skill-runner → skill-runner 引导文案（非 BOSS 原文案）"""
        devices = [_skill_ready_device(providers=("boss-recruiting",))]
        patches, create_invocation, _ = _patch_repo(devices, invocation=None)
        with patches:
            result = await _dispatch()
        assert result.success is False
        assert result.error == "DEVICE_UNAVAILABLE"
        assert "skill-runner" in result.stderr
        create_invocation.assert_not_called()

    async def test_no_identity_rejected_without_device_lookup(self):
        """受信身份缺失 → NO_IDENTITY，不查设备不建 invocation（沿基类闸门语义）"""
        patches, create_invocation, _ = _patch_repo([_skill_ready_device()], invocation=None)
        with patches:
            result = await _dispatch(tenant_id="", user_id="")
        assert result.success is False
        assert result.error == "NO_IDENTITY"

    async def test_version_gate_precedes_enqueue_in_dispatch_chain(self):
        """版本门插在设备闸门之后 enqueue 之前：设备离线（闸门先拦）与版本不符（版本门拦）
        的先后关系——离线设备不触发版本门文案（闸门先返回）"""
        offline = _skill_ready_device(
            last_seen=datetime.now() - timedelta(seconds=120),
            skills=[{"name": "vec-skill", "hash": "b" * 64}])  # hash 也不符
        patches, create_invocation, _ = _patch_repo([offline], invocation=None)
        with patches:
            result = await _dispatch()
        assert result.error == "DEVICE_UNAVAILABLE"  # 离线先拦，不是 SKILL_VERSION_MISMATCH
        create_invocation.assert_not_called()
