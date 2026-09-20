"""boss.chat_reply.v1 受信注册点（B2）。

由受信初始化点调用（scheduler 组合根 / main.py 生命周期；决策 tick 自愈同微信
由各自 registration 承担）：boss 场景注册结构与微信 registration.py 同构——
- session_tasks.enabled + boss_conversation.enabled 双门控内才注册；
- 经 register_scenario 原子注册底座场景适配器（TrustedAdapterRegistry）、
  session_tasks 决策钩子（scenario_hooks）与场景描述器（scenario_descriptor），
  幂等且复核存活性；
- 默认（boss_conversation.enabled=false）零注册：组合根得到 False，通用层
  不出现 boss.chat_reply.v1 场景键，微信场景行为不受影响。
"""
from __future__ import annotations

import threading

from loguru import logger

from src.desktop_automation.adapters import TrustedAdapterRegistry
from src.session_tasks import scenario_descriptor, scenario_hooks
from src.session_tasks.config import get_session_tasks_config

from .constants import SCENARIO_KEY

_LOCK = threading.Lock()
_REGISTERED = False


def ensure_registered() -> bool:
    """幂等注册描述器骨架（双门控内；tick 每 tick 调用自愈）。

    双门控 = 通用层 session_tasks.enabled（进程快照）+ boss_conversation.enabled
    （热读，默认 false fail-closed）。任一门控关闭 → False 零注册。
    """
    global _REGISTERED
    cfg = get_session_tasks_config()
    from .config import scenario_enabled_gate

    if not cfg.enabled or not scenario_enabled_gate():
        return False
    with _LOCK:
        if (
            _REGISTERED
            and TrustedAdapterRegistry.get(SCENARIO_KEY) is not None
            and scenario_hooks.get_hooks(SCENARIO_KEY) is not None
            and scenario_descriptor.get_descriptor(SCENARIO_KEY) is not None
        ):
            return True
        from .descriptor import build_boss_descriptor

        scenario_descriptor.register_scenario(build_boss_descriptor())
        _REGISTERED = True
    logger.info("boss_conversation 场景描述器已注册（boss.chat_reply.v1；B2 场景包）")
    return True


def reset_registration() -> None:
    """测试清理用（描述器/决策钩子/适配器三处注册表，逆注册序清理）。"""
    global _REGISTERED
    with _LOCK:
        scenario_descriptor.unregister_descriptor(SCENARIO_KEY)
        scenario_hooks.unregister_hooks(SCENARIO_KEY)
        TrustedAdapterRegistry.unregister(SCENARIO_KEY)
        _REGISTERED = False
