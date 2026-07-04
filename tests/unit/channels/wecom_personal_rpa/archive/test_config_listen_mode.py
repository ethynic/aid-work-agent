"""RpaConfigResponse listen_mode 字段单元测试（Phase 7）

覆盖：
- schema 接受 'server' / 'client' / None
- schema 拒绝非法值
- 默认值为 'server'
"""
import pytest
from pydantic import ValidationError

from src.channels.wecom_personal_rpa.schemas import RpaConfigResponse


def _base_kwargs(**overrides):
    """构造 RpaConfigResponse 必填字段。"""
    from datetime import datetime

    base = {
        "protocol_version": "1.0.0",
        "min_client_version": "1.0.0",
        "server_time": datetime.now(),
    }
    base.update(overrides)
    return base


def test_listen_mode_default_is_server():
    """listen_mode 缺省时默认 'server'。"""
    resp = RpaConfigResponse(**_base_kwargs())
    assert resp.listen_mode == "server"


def test_listen_mode_accepts_server():
    resp = RpaConfigResponse(**_base_kwargs(listen_mode="server"))
    assert resp.listen_mode == "server"


def test_listen_mode_accepts_client():
    """client 值被 schema 接受（第一期前端禁用，但契约保留）。"""
    resp = RpaConfigResponse(**_base_kwargs(listen_mode="client"))
    assert resp.listen_mode == "client"


def test_listen_mode_accepts_none():
    """显式传 None 时字段值为 None（Pydantic v2 行为，不被 default 覆盖）。

    路由层 wecom_personal_rpa_config 永远传字符串（'server' / 'client'），
    None 路径不可达；此测试仅为契约完整性记录 Pydantic v2 行为。
    """
    resp = RpaConfigResponse(**_base_kwargs(listen_mode=None))
    assert resp.listen_mode is None


def test_listen_mode_rejects_invalid_value():
    """非法值（既不是 server 也不是 client）应被 schema 拒绝。"""
    with pytest.raises(ValidationError):
        RpaConfigResponse(**_base_kwargs(listen_mode="invalid"))


def test_listen_mode_rejects_empty_string():
    with pytest.raises(ValidationError):
        RpaConfigResponse(**_base_kwargs(listen_mode=""))
