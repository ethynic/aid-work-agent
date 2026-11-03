"""setup_logging 异常堆栈脱敏回归测试

背景（2026-11-03）：loguru 默认 diagnose=True 会在异常堆栈的出错行上标注
子表达式的实际值（如 `server.login(user, smtp_password)` 行会把密码明文
写入 error 日志）。生产环境因此泄漏过用户邮箱 SMTP 密码。

修复：setup_logging 所有 sink 统一 diagnose=False，本测试端到端验证
敏感值不再进入 error 文件日志。
"""

import pytest
from loguru import logger

from src.config.logging import setup_logging


class _FakeSMTPServer:
    def login(self, user, password):
        raise ValueError("451 4.3.2 Internal server error")


_server = _FakeSMTPServer()


def _send_email_with_password(password):
    # 出错行引用敏感变量，复现 loguru diagnose 值标注场景
    _server.login("user@example.com", password)


@pytest.fixture()
def isolated_log_dir(tmp_path):
    log_dir = tmp_path / "agent"
    setup_logging(log_level="INFO", log_dir=str(log_dir))
    yield log_dir
    logger.complete()
    logger.remove()


def test_error_stack_does_not_leak_sensitive_variables(isolated_log_dir):
    secret = "SuperSecret123"
    try:
        _send_email_with_password(secret)
    except Exception:
        logger.opt(exception=True).error("后端日志：发送邮件失败")

    logger.complete()

    # 找到当天 error 文件，断言堆栈已落盘但不包含敏感值
    error_files = list(isolated_log_dir.glob("error_*.log"))
    assert error_files, "error 日志文件未生成"
    content = "".join(f.read_text(encoding="utf-8") for f in error_files)

    assert "451 4.3.2" in content, "异常堆栈未写入 error 日志"
    assert secret not in content, "敏感变量值泄漏进 error 日志"
