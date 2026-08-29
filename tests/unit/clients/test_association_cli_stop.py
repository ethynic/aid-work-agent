"""客户端协作式停止标志（runtime.stop_flag）与 PowerShell 轮询等待的单元测试。

通过 monkeypatch.syspath_prepend 把 clients/association-client-cli 加入 sys.path，
直接测试真实模块；停止文件用 tmp_path 下的真实文件，验证环境变量契约。
"""

import asyncio
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CLI_ROOT = (
    Path(__file__).resolve().parents[3] / "clients" / "association-client-cli"
)


@pytest.fixture()
def stop_flag_module(monkeypatch):
    """加载真实的 runtime.stop_flag 模块（每次全新 import，避免环境变量残留）。"""
    monkeypatch.syspath_prepend(str(CLI_ROOT))
    monkeypatch.delenv("ASSOCIATION_STOP_FILE", raising=False)
    for name in ("runtime.stop_flag", "runtime"):
        sys.modules.pop(name, None)
    import runtime.stop_flag as stop_flag

    yield stop_flag
    # 清理模块缓存，避免影响其他测试对 runtime 包的懒加载判断
    for name in ("runtime.stop_flag", "runtime"):
        sys.modules.pop(name, None)


class TestStopFlag:
    def test_env_not_set_is_noop(self, stop_flag_module):
        assert stop_flag_module.configured() is False
        assert stop_flag_module.is_set() is False
        stop_flag_module.check()  # 不抛异常

    def test_env_set_but_file_missing(self, stop_flag_module, monkeypatch, tmp_path):
        monkeypatch.setenv("ASSOCIATION_STOP_FILE", str(tmp_path / "stop"))
        assert stop_flag_module.configured() is True
        assert stop_flag_module.is_set() is False
        stop_flag_module.check()  # 不抛异常

    def test_file_created_raises_user_stopped(
        self, stop_flag_module, monkeypatch, tmp_path
    ):
        stop_file = tmp_path / "stop"
        monkeypatch.setenv("ASSOCIATION_STOP_FILE", str(stop_file))
        stop_file.write_text("stop", encoding="utf-8")
        assert stop_flag_module.is_set() is True
        with pytest.raises(stop_flag_module.UserStoppedError):
            stop_flag_module.check()

    def test_user_stopped_error_is_not_exception_subclass(self, stop_flag_module):
        # 继承 BaseException：穿透业务层 except Exception 兜底
        assert issubclass(stop_flag_module.UserStoppedError, BaseException)
        assert not issubclass(stop_flag_module.UserStoppedError, Exception)


class _FakeProcess:
    """模拟 asyncio 子进程：hang=True 时 communicate 永不返回。"""

    def __init__(self, result=(b"out", b"err"), hang=False):
        self._result = result
        self._hang = hang
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self._hang:
            await asyncio.Event().wait()
        return self._result

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


@pytest.fixture()
def powershell_runner_module(stop_flag_module, monkeypatch, tmp_path):
    """加载真实 runtime.powershell_runner，并配置停止文件环境变量。"""
    monkeypatch.setenv("ASSOCIATION_STOP_FILE", str(tmp_path / "stop"))
    sys.modules.pop("runtime.powershell_runner", None)
    import runtime.powershell_runner as runner

    yield runner
    sys.modules.pop("runtime.powershell_runner", None)


class TestPowershellRunnerStopPolling:
    @pytest.mark.asyncio
    async def test_normal_completion_returns_stdout_stderr(
        self, powershell_runner_module
    ):
        process = _FakeProcess(result=(b"hello", b""))
        stdout, stderr = await powershell_runner_module._communicate_with_stop_polling(
            process, timeout=5, interval=0.02
        )
        assert (stdout, stderr) == (b"hello", b"")
        assert process.killed is False

    @pytest.mark.asyncio
    async def test_stop_file_kills_process_and_raises(
        self, powershell_runner_module, tmp_path
    ):
        stop_file = Path(tmp_path / "stop")
        process = _FakeProcess(hang=True)

        async def create_stop_file():
            await asyncio.sleep(0.1)
            stop_file.write_text("stop", encoding="utf-8")

        task = asyncio.ensure_future(create_stop_file())
        with pytest.raises(powershell_runner_module.stop_flag.UserStoppedError):
            await powershell_runner_module._communicate_with_stop_polling(
                process, timeout=30, interval=0.02
            )
        await task
        assert process.killed is True
        assert process.waited is True

    @pytest.mark.asyncio
    async def test_timeout_semantics_preserved(self, powershell_runner_module):
        process = _FakeProcess(hang=True)
        with pytest.raises(RuntimeError, match="WECHAT_RPA_TIMEOUT"):
            await powershell_runner_module._communicate_with_stop_polling(
                process, timeout=0.1, interval=0.02
            )
        assert process.killed is True
        assert process.waited is True
