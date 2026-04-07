"""单元测试专用 fixtures"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_unit_tests(monkeypatch):
    """确保单元测试不会意外发起真实网络调用。

    autouse fixture：自动应用于所有 unit/ 下的测试。
    如需在单个测试中覆盖，可用 monkeypatch.undo()。
    """
    # 标记当前为单元测试环境
    monkeypatch.setenv("TESTING", "unit")
