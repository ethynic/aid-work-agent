"""InterceptHandler：标准库 logging 转发进 loguru 的单测

覆盖：ERROR+exc_info 堆栈透传、INFO 转发、%s 占位符格式化、
真实调用帧回溯（depth）、_install_intercept_handler 幂等注册。
"""

import logging

import pytest
from loguru import logger

from src.config.logging import InterceptHandler, _install_intercept_handler


@pytest.fixture()
def captured():
    records = []
    sink_id = logger.add(lambda msg: records.append(msg.record), level=0)
    yield records
    logger.remove(sink_id)


def _attach_intercept(std_logger: logging.Logger) -> None:
    std_logger.handlers = [InterceptHandler()]
    std_logger.setLevel(logging.DEBUG)
    std_logger.propagate = False


def _detach_intercept(std_logger: logging.Logger) -> None:
    std_logger.handlers = []
    std_logger.propagate = True


def test_error_with_exc_info_forwarded(captured):
    std_logger = logging.getLogger("test_intercept_error")
    _attach_intercept(std_logger)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            std_logger.error("出错了: %s", "参数", exc_info=True)
    finally:
        _detach_intercept(std_logger)

    assert len(captured) == 1
    record = captured[0]
    assert record["level"].name == "ERROR"
    assert record["message"] == "出错了: 参数"
    assert record["exception"] is not None
    # loguru 的 record["exception"] 为 (type, value, traceback) 元组
    assert record["exception"][0] is ValueError
    assert isinstance(record["exception"][1], ValueError)


def test_info_forwarded(captured):
    std_logger = logging.getLogger("test_intercept_info")
    _attach_intercept(std_logger)
    try:
        std_logger.info("普通消息 %d", 42)
    finally:
        _detach_intercept(std_logger)

    assert len(captured) == 1
    assert captured[0]["level"].name == "INFO"
    assert captured[0]["message"] == "普通消息 42"


def test_depth_points_to_real_call_site(captured):
    std_logger = logging.getLogger("test_intercept_depth")
    _attach_intercept(std_logger)
    try:
        std_logger.warning("回溯帧测试")
    finally:
        _detach_intercept(std_logger)

    assert len(captured) == 1
    record = captured[0]
    # {name} 应指向本测试模块（真实调用点），而不是 logging 内部帧
    assert record["name"] == __name__
    assert record["function"] == "test_depth_points_to_real_call_site"


def test_unknown_level_name_falls_back_to_levelno(captured):
    class _FakeLogger:
        def _log(self, level_name, msg):
            record = logging.LogRecord(
                name="test_intercept_fake",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg=msg,
                args=(),
                exc_info=None,
            )
            record.levelname = level_name
            InterceptHandler().emit(record)

    _FakeLogger()._log("FATAL", "未知级别")

    assert len(captured) == 1
    assert captured[0]["message"] == "未知级别"


def test_install_intercept_handler_idempotent():
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    try:
        _install_intercept_handler()
        _install_intercept_handler()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], InterceptHandler)
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)


def test_noisy_third_party_loggers_suppressed_to_warning():
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    try:
        _install_intercept_handler()
        # 高频出站库（每请求一行 INFO）必须压到 WARNING，防止主日志量暴涨
        for name in ("httpx", "httpcore", "openai", "urllib3", "apscheduler.executors.default"):
            assert logging.getLogger(name).level == logging.WARNING, name
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)
