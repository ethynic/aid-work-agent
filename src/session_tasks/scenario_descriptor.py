"""session_tasks 通用层 → 场景实现的受信场景描述器注册表（B1.1，设计 §4.1）。

ScenarioDescriptor 是场景接入通用层的单一注册对象：spec 校验入口、发送能力、
operation descriptor、回执策略（双命名空间）、绑定查询面、决策钩子、场景适配器、
工作台 label 解析与可选发送门禁（send_eligibility_gate，仅 BOSS 注册；微信为 None）。

职责切分（设计 §5.5.1）：描述器纯计算/纯声明，全部写操作由通用层持有；通用层
在 B1.1 阶段仍读旧硬编码路径，B1.2 才切换调用点（本模块只搭基础设施）。

依赖方向：session_tasks → desktop_automation 允许（TrustedAdapterRegistry /
ScenarioAdapter）；desktop_automation 不得反向 import 本模块。
注册点唯一：register_scenario(descriptor) 原子注册（一致性预检 + 写前快照 +
失败恢复调用前状态，不破坏既有稳定注册）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Protocol

from loguru import logger

from src.desktop_automation.adapters import ScenarioAdapter, TrustedAdapterRegistry

from . import scenario_hooks


class BindingResolver(Protocol):
    """场景绑定查询面契约（B1.1 只登记；B1.2 切换 service/workbench 调用点）。

    成员语义以微信现状直查 SQL 为基准（service._binding_valid_for_allocation /
    resume_claim 运行时身份查询），BOSS 场景按同名语义实现自己的绑定表查询。
    """

    def get_binding_by_id(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:
        """按 id 取绑定行（含属主/验证字段列集；不存在返回 None）。"""
        ...

    def get_runtime_identity(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:
        """运行时身份字段（identity_version/verifier_version/conversation_label/
        account_version），claim 响应契约身份段使用；不存在返回 None。"""
        ...

    def is_valid_for_allocation(self, conn, tenant_id: str, binding_id: str) -> bool:
        """领取/分配时的绑定有效性复核（verified + 验证字段完整且未过期）。"""
        ...


class ScenarioDescriptor(Protocol):
    """场景描述器契约（设计 §4.1：单一注册对象；BOSS chat_reply.v1 为第二实现）。"""

    scenario_key: str
    spec_validator: Callable[[dict], dict]
    required_send_capability: str
    operation_descriptor: Dict[str, Any]
    receipt_policy: Dict[str, Any]  # {mode, context, submission_evidence_namespace, verified_evidence_namespace}
    binding_resolver: BindingResolver
    decision_hooks: scenario_hooks.ScenarioDecisionHooks
    adapter: ScenarioAdapter  # 含 settle_operation_result / validate_submission_evidence / validate_evidence
    workbench_label_resolver: Callable[..., Any]
    send_eligibility_gate: Optional[Callable[..., Any]]  # BOSS 使用；纯计算只读；微信为 None


class ScenarioDescriptorError(Exception):
    """场景描述器缺失/非法。"""


# 注册/注销串行化锁（RLock：容忍同线程重入；与 registration._LOCK 的关系见
# register_scenario docstring——两把不同粒度的锁，获取顺序全局唯一，无嵌套死锁）
_LOCK = threading.RLock()

_DESCRIPTORS: Dict[str, ScenarioDescriptor] = {}


def register_descriptor(descriptor: ScenarioDescriptor) -> None:
    _DESCRIPTORS[descriptor.scenario_key] = descriptor


def unregister_descriptor(scenario_key: str) -> None:
    _DESCRIPTORS.pop(scenario_key, None)


def get_descriptor(scenario_key: str) -> Optional[ScenarioDescriptor]:
    return _DESCRIPTORS.get(scenario_key)


def registered_keys() -> List[str]:
    return list(_DESCRIPTORS.keys())


def require_descriptor(scenario_key: str) -> ScenarioDescriptor:
    descriptor = _DESCRIPTORS.get(scenario_key)
    if descriptor is None:
        raise ScenarioDescriptorError(f"场景描述器未注册: {scenario_key}")
    return descriptor


def register_scenario(descriptor: ScenarioDescriptor) -> None:
    """原子注册（设计 §4.1 注册点唯一）：一致性预检 → 写前快照 → 适配器 → 决策钩子 → 描述器。

    scenario_key 三处不一致（或缺成员）在任何写入前抛出，零写入。持模块级
    注册锁串行化整个注册与恢复过程；任一步抛异常按调用前快照**恢复调用前
    状态**（旧对象原样恢复、本次新增值删除，恢复顺序与写入相反）后原样重抛
    ——同 key 已有稳定注册（如微信）不因注册失败被清掉，不留半注册状态。
    回滚自身失败时以 logger.error 明确记录（此时三表可能偏离调用前状态，
    不再满足全有或全无），不覆盖原始异常。

    锁关系：本模块 _LOCK 与 weixin_conversation.registration._LOCK 是两把不同
    粒度的锁，全局只存在 "registration._LOCK → 本锁" 的唯一获取顺序（ensure_registered
    持外层锁后调本函数），无反向获取路径，不会嵌套死锁；选 RLock 容忍同线程
    重入（上层装配代码持本锁时再次调用本函数）。
    """
    scenario_key = descriptor.scenario_key
    adapter_key = descriptor.adapter.scenario_key
    hooks_key = descriptor.decision_hooks.scenario_key
    if scenario_key != adapter_key or scenario_key != hooks_key:
        raise ScenarioDescriptorError(
            f"scenario_key 不一致: descriptor={scenario_key} adapter={adapter_key} hooks={hooks_key}"
        )
    with _LOCK:
        # 三个底层注册均为覆盖写：写入前快照调用前对象，失败按快照恢复而非简单清空
        old_adapter = TrustedAdapterRegistry.get(scenario_key)
        old_hooks = scenario_hooks.get_hooks(scenario_key)
        old_descriptor = get_descriptor(scenario_key)
        TrustedAdapterRegistry.register(descriptor.adapter)
        try:
            scenario_hooks.register_hooks(descriptor.decision_hooks)
        except Exception as exc:
            try:
                _restore_adapter(scenario_key, old_adapter)
            except Exception as restore_exc:
                logger.error(
                    "register_scenario 回滚失败（决策钩子步，适配器表未恢复）: "
                    f"scenario_key={scenario_key} 原始异常={exc!r} 回滚异常={restore_exc!r}"
                )
            raise
        try:
            register_descriptor(descriptor)
        except Exception as exc:
            try:
                _restore_hooks(scenario_key, old_hooks)
                _restore_adapter(scenario_key, old_adapter)
            except Exception as restore_exc:
                logger.error(
                    "register_scenario 回滚失败（描述器步，钩子/适配器表未恢复）: "
                    f"scenario_key={scenario_key} 原始异常={exc!r} 回滚异常={restore_exc!r}"
                )
            raise


def _restore_adapter(scenario_key: str, old_adapter: Optional[ScenarioAdapter]) -> None:
    """恢复适配器表到调用前状态（旧对象原样恢复 / 无旧值则删除本次新增）。"""
    if old_adapter is not None:
        TrustedAdapterRegistry.register(old_adapter)
    else:
        TrustedAdapterRegistry.unregister(scenario_key)


def _restore_hooks(scenario_key: str, old_hooks: Optional[scenario_hooks.ScenarioDecisionHooks]) -> None:
    """恢复决策钩子表到调用前状态（旧对象原样恢复 / 无旧值则删除本次新增）。"""
    if old_hooks is not None:
        scenario_hooks.register_hooks(old_hooks)
    else:
        scenario_hooks.unregister_hooks(scenario_key)
