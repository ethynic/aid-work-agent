"""wecom_personal_rpa 单元测试本地环境隔离

本子目录的测试导入 ``src.channels.wecom_personal_rpa.*`` 会触发
``src.channels.__init__`` → ``session`` → ``db.database`` → ``psycopg2`` /
``bcrypt`` 的导入链。CI/开发环境若未安装这两个原生扩展包，会在模块加载期
抛 ``AttributeError``/``ModuleNotFoundError``，与被测逻辑无关。

此处与根 ``tests/conftest.py`` 的 dashscope/vector_db mock 同思路，在导入
``src.*`` 之前注入轻量 stub，仅用于本子目录的单元测试隔离。

注意：这是测试基础设施，不参与生产代码路径。
"""
import sys
import types
from unittest.mock import MagicMock


def _ensure_stub(name: str, **attrs) -> None:
    """若模块未安装，注入一个最小 stub 到 sys.modules。"""
    if name in sys.modules:
        return
    try:
        __import__(name)
    except Exception:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


# psycopg2（含 pool / extras 子模块）—— database.py 模块加载期引用
# 注意：必须在真实包可导入时不注入 stub，否则会污染同进程后续测试
# （如 test_idempotency 依赖真实的 psycopg2.IntegrityError）。
_ensure_stub(
    "psycopg2",
    extensions=MagicMock(),
)
_ensure_stub(
    "psycopg2.pool",
    ThreadedConnectionPool=MagicMock(),
)
_ensure_stub(
    "psycopg2.extras",
    RealDictCursor=MagicMock(),
    Json=MagicMock(),
)

# bcrypt —— db.models 加载期引用
_ensure_stub("bcrypt")
