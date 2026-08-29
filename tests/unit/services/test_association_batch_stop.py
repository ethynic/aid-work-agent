"""用户停止（停止文件）对 enrich_many 批量流程与增量落盘的影响。"""

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.services.association_batch_enrichment import (
    AssociationBatchEnricher,
    write_enrichment_workbook,
)

pytestmark = pytest.mark.unit

CLI_ROOT = (
    Path(__file__).resolve().parents[3] / "clients" / "association-client-cli"
)


def _install_fake_stop_flag(monkeypatch, flag: dict):
    """注入假的 runtime.stop_flag：flag["set"] 控制 is_set() 返回值。

    仓库测试环境没有客户端 runtime 包，enrich_many 的懒加载会失败并视为
    「未配置停止文件」；这里注入假模块模拟客户端环境。
    """

    class UserStoppedError(BaseException):
        pass

    stop_flag = types.ModuleType("runtime.stop_flag")
    stop_flag.UserStoppedError = UserStoppedError
    stop_flag.is_set = lambda: flag["set"]
    stop_flag.configured = lambda: True
    runtime_pkg = types.ModuleType("runtime")
    runtime_pkg.stop_flag = stop_flag
    monkeypatch.setitem(sys.modules, "runtime", runtime_pkg)
    monkeypatch.setitem(sys.modules, "runtime.stop_flag", stop_flag)
    return stop_flag


def _make_enricher(**overrides):
    providers = {
        "official_profile_collector": overrides.get(
            "collect", _async_none_collect
        ),
        "fallback_profile_provider": overrides.get("fallback", _async_empty),
        "wechat_mobile_provider": overrides.get("wechat", _async_none_wechat),
    }
    extra = {}
    if "progress_reporter" in overrides:
        extra["progress_reporter"] = overrides["progress_reporter"]
    return AssociationBatchEnricher(headless=False, **providers, **extra)


async def _async_empty(_name):
    return {}


async def _async_none_collect(_url, _headless, **_kwargs):
    return {}


async def _async_none_wechat(_association, _person, _role):
    return None


@pytest.mark.asyncio
async def test_stop_flag_set_before_batch_marks_all_rows_stopped(monkeypatch):
    flag = {"set": True}
    _install_fake_stop_flag(monkeypatch, flag)
    calls = []

    async def fallback(name):
        calls.append(name)
        return {}

    enricher = _make_enricher(fallback=fallback)
    rows = await enricher.enrich_many(["甲协会", "乙协会"])

    assert rows.stopped is True
    assert rows.aborted is False
    assert [row.association_name for row in rows] == ["甲协会", "乙协会"]
    assert all(row.processing_status == "stopped" for row in rows)
    assert calls == []  # 任何 provider 都不该被调用


@pytest.mark.asyncio
async def test_stop_flag_mid_batch_keeps_completed_and_marks_remaining(monkeypatch):
    flag = {"set": False}
    _install_fake_stop_flag(monkeypatch, flag)

    async def fallback(name):
        result = {"address": f"{name}地址"}
        flag["set"] = True  # 第一个协会完成后用户点击停止
        return result

    enricher = _make_enricher(fallback=fallback)
    rows = await enricher.enrich_many(["甲协会", "乙协会", "丙协会"])

    assert rows.stopped is True
    assert [row.association_name for row in rows] == ["甲协会", "乙协会", "丙协会"]
    assert rows[0].processing_status == "partial"
    assert rows[0].values["address"] == "甲协会地址"
    assert rows[1].processing_status == "stopped"
    assert rows[2].processing_status == "stopped"


@pytest.mark.asyncio
async def test_user_stopped_error_from_provider_returns_partial_results(monkeypatch):
    """子进程轮询点命中停止文件抛 UserStoppedError：穿透 enrich_one 的
    except Exception 兜底，当前及剩余协会标记 stopped，已完成行保留。"""
    flag = {"set": False}
    stop_flag = _install_fake_stop_flag(monkeypatch, flag)
    processed = []

    async def fallback(name):
        processed.append(name)
        if name == "乙协会":
            flag["set"] = True
            raise stop_flag.UserStoppedError("USER_STOPPED")
        return {"address": f"{name}地址"}

    enricher = _make_enricher(fallback=fallback)
    rows = await enricher.enrich_many(["甲协会", "乙协会", "丙协会"])

    assert rows.stopped is True
    assert processed == ["甲协会", "乙协会"]  # 丙协会未开始
    assert rows[0].processing_status == "partial"
    assert rows[1].processing_status == "stopped"
    assert rows[1].association_name == "乙协会"
    assert rows[2].processing_status == "stopped"


@pytest.mark.asyncio
async def test_partial_jsonl_records_every_processed_row(monkeypatch, tmp_path):
    flag = {"set": False}
    _install_fake_stop_flag(monkeypatch, flag)
    partial_path = tmp_path / "result.xlsx.partial.jsonl"

    async def fallback(name):
        result = {"address": f"{name}地址"}
        if name == "甲协会":
            flag["set"] = True
        return result

    enricher = _make_enricher(fallback=fallback)
    rows = await enricher.enrich_many(["甲协会", "乙协会"], partial_path=partial_path)

    assert rows.stopped is True
    lines = partial_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # 已完成行 + stopped 占位行都已落盘
    first = json.loads(lines[0])
    assert first["association_name"] == "甲协会"
    assert first["processing_status"] == "partial"
    assert first["address"] == "甲协会地址"
    second = json.loads(lines[1])
    assert second["processing_status"] == "stopped"


@pytest.mark.asyncio
async def test_partial_jsonl_not_written_without_path(tmp_path):
    # 未传 partial_path（其他入口默认行为）：不写任何增量文件
    enricher = _make_enricher()
    rows = await enricher.enrich_many(["甲协会"])
    assert len(rows) == 1
    assert rows.stopped is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_stopped_rows_written_to_final_excel(monkeypatch, tmp_path):
    flag = {"set": True}
    _install_fake_stop_flag(monkeypatch, flag)
    enricher = _make_enricher()
    rows = await enricher.enrich_many(["甲协会"])

    output = write_enrichment_workbook(rows, tmp_path / "result.xlsx")
    workbook = load_workbook(output, data_only=True)
    try:
        worksheet = workbook.active
        headers = [cell.value for cell in worksheet[1]]
        values = dict(zip(headers, [cell.value for cell in worksheet[2]]))
    finally:
        workbook.close()
    assert values["客户名称"] == "甲协会"
    assert values["处理状态"] == "stopped"


@pytest.mark.asyncio
async def test_report_final_hook_called_with_terminal_status(monkeypatch):
    calls = []

    class _Reporter:
        def __call__(self, _message):
            pass

        def report_final(self, association, status):
            calls.append((association, status))

    async def fallback(name):
        if name == "坏协会":
            raise RuntimeError("search down")
        return {"address": "北京市"}

    enricher = _make_enricher(fallback=fallback, progress_reporter=_Reporter())
    rows = await enricher.enrich_many(["好协会", "坏协会"])

    assert rows[0].processing_status == "partial"
    assert rows[1].processing_status == "failed"
    assert ("好协会", "success") in calls
    assert ("坏协会", "failed") in calls


@pytest.mark.asyncio
async def test_communicate_with_timeout_polling_raises_on_stop_file(
    monkeypatch, tmp_path
):
    """providers 的子进程等待点：配置停止文件后走轮询，命中即抛 UserStoppedError。"""
    stop_file = tmp_path / "stop"
    monkeypatch.syspath_prepend(str(CLI_ROOT))
    monkeypatch.setenv("ASSOCIATION_STOP_FILE", str(stop_file))
    for name in ("runtime.stop_flag", "runtime"):
        sys.modules.pop(name, None)
    try:
        import runtime.stop_flag as stop_flag

        from src.services.association_enrichment_providers import (
            ProjectAssociationProviders,
        )

        class _HangingProcess:
            def __init__(self):
                self.killed = False
                self.waited = False

            async def communicate(self):
                await asyncio.Event().wait()

            def kill(self):
                self.killed = True

            async def wait(self):
                self.waited = True

        process = _HangingProcess()

        async def create_stop_file():
            await asyncio.sleep(0.1)
            stop_file.write_text("stop", encoding="utf-8")

        task = asyncio.ensure_future(create_stop_file())
        try:
            with pytest.raises(stop_flag.UserStoppedError):
                await ProjectAssociationProviders._communicate_with_timeout(
                    process,
                    timeout_seconds=30,
                    error_code="WECHAT_RPA_TIMEOUT",
                )
        finally:
            await task
        assert process.killed is True
        assert process.waited is True
    finally:
        for name in ("runtime.stop_flag", "runtime"):
            sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_await_with_stop_polling_raises_on_stop_file(monkeypatch, tmp_path):
    """文心采集等进程内长任务的停止感知等待：命中停止文件即取消任务并抛
    UserStoppedError（穿透 _collect_wenxin_query 的 except Exception 降级兜底）。"""
    stop_file = tmp_path / "stop"
    monkeypatch.syspath_prepend(str(CLI_ROOT))
    monkeypatch.setenv("ASSOCIATION_STOP_FILE", str(stop_file))
    for name in ("runtime.stop_flag", "runtime"):
        sys.modules.pop(name, None)
    try:
        import runtime.stop_flag as stop_flag

        from src.services.association_enrichment_providers import (
            _await_with_stop_polling,
        )

        cancelled = False

        async def hanging():
            nonlocal cancelled
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled = True
                raise

        async def create_stop_file():
            await asyncio.sleep(0.1)
            stop_file.write_text("stop", encoding="utf-8")

        task = asyncio.ensure_future(create_stop_file())
        try:
            with pytest.raises(stop_flag.UserStoppedError):
                await _await_with_stop_polling(hanging(), 30, stop_flag, interval=0.02)
        finally:
            await task
        assert cancelled is True  # 采集任务被取消，不留悬挂协程
    finally:
        for name in ("runtime.stop_flag", "runtime"):
            sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_await_with_stop_polling_normal_and_timeout(monkeypatch, tmp_path):
    """停止感知等待的另两个分支：正常完成返回结果；超时抛 TimeoutError（与
    asyncio.wait_for 语义一致，调用方按采集失败降级）。"""
    monkeypatch.syspath_prepend(str(CLI_ROOT))
    monkeypatch.setenv("ASSOCIATION_STOP_FILE", str(tmp_path / "stop"))
    for name in ("runtime.stop_flag", "runtime"):
        sys.modules.pop(name, None)
    try:
        import runtime.stop_flag as stop_flag

        from src.services.association_enrichment_providers import (
            _await_with_stop_polling,
        )

        async def quick():
            return {"ok": True}

        result = await _await_with_stop_polling(quick(), 5, stop_flag, interval=0.02)
        assert result == {"ok": True}

        async def hanging():
            await asyncio.Event().wait()

        with pytest.raises(TimeoutError):
            await _await_with_stop_polling(hanging(), 0.05, stop_flag, interval=0.02)
    finally:
        for name in ("runtime.stop_flag", "runtime"):
            sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_communicate_with_timeout_unchanged_without_env(monkeypatch):
    """ASSOCIATION_STOP_FILE 未设置：仍走原 wait_for 路径，超时语义不变。"""
    monkeypatch.delenv("ASSOCIATION_STOP_FILE", raising=False)
    for name in ("runtime.stop_flag", "runtime"):
        sys.modules.pop(name, None)

    from src.services.association_enrichment_providers import (
        ProjectAssociationProviders,
    )

    class _HangingProcess:
        def __init__(self):
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    process = _HangingProcess()
    with pytest.raises(RuntimeError, match="CHILD_TIMEOUT"):
        await ProjectAssociationProviders._communicate_with_timeout(
            process,
            timeout_seconds=0.05,
            error_code="CHILD_TIMEOUT",
        )
    assert process.killed is True
    assert process.waited is True
