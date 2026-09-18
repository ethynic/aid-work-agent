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

    def resolve_draft_targets(self, conn, tenant_id: str, user_id: str, device_id: str, resolution_invocation_id: str):  # noqa: ANN001
        """名称定位结果 → (account_binding_id, conversation_binding_id)。

        仅支持 resolution 草稿路径的场景实现（微信）；BOSS 等其他场景可抛
        ScenarioDescriptorError（models 层已约束 resolution 与非微信场景互斥）。"""
        ...

    def ensure_valid_for_publish(self, binding: Dict[str, Any]) -> None:  # noqa: ANN001
        """发布有效性校验（require_verified 分支）：非法时抛 SessionTaskError
        （场景持有逐条错误文案与状态码语义）。"""
        ...

    def runtime_target_policy(self, binding_row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        """claim 响应运行时目标策略（如微信 _runtime_target；无则 None）。"""
        ...

    def account_identity_version(self, binding_row: Optional[Dict[str, Any]]) -> int:  # noqa: ANN001
        """claim 响应 account_identity_version（无账号 scope 语义的场景返回 0）。"""
        ...

    def list_bindings(self, tenant_id: str, user_id: str, device_id: str, limit: int):  # noqa: ANN001
        """绑定管理 API 列表（bindings_router 按 scenario_key 路由；不支持的场景抛
        ScenarioDescriptorError，API 层转 fail-closed 4xx）。"""
        ...

    def create_binding(self, tenant_id: str, user_id: str, device_id: str,
                       account_binding_id: str, binding_type: str, label: str):  # noqa: ANN001
        """绑定管理 API 创建（同上；不支持的场景抛 ScenarioDescriptorError）。"""
        ...


class BindingGuard(Protocol):
    """场景 binding 同步门禁契约（B1.2，设计 §5.5.2/§5.5.5）。

    场景在自己的绑定表上实现全部行级操作；通用层只负责定位（受信 business_ref
    → task.conversation_binding_id）并传同一 cursor。微信描述器 binding_guard=None，
    通用层完全绕过该扩展（锁面不变，B1.0 特征锁定）。

    锁序约束（设计 §5.5.5 矩阵）：guard 的锁总是在调用方已持 subject/task（或
    invocation/delivery）锁之后获取——场景实现不得在 guard 内反向获取 subject/task。
    """

    def lock_binding(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        """场景绑定行 FOR UPDATE（operation-result/permits/Phase A 共用入口）；
        不存在返回 None。"""
        ...

    def check_blocked(self, binding_row: Dict[str, Any]) -> Optional[str]:  # noqa: ANN001
        """已锁定绑定行的同步阻断检查（幂等重 prepare 等只锁不计数路径使用）：
        blocked 返回原因（受控 reason），未阻断返回 None。"""
        ...

    def gate_transaction(self, cursor, task: Dict[str, Any], decision: Dict[str, Any],
                         gate: Callable[..., Any]) -> Dict[str, Any]:  # noqa: ANN001
        """prepare-send Phase A 门禁（同一事务/同一 cursor 内）。

        职责切分（V1.9 冻结）：guard 只做锁/检查/落库——锁场景 binding 行 →
        检查 automation_blocked（blocked → terminal，不调 gate）→ 跨日归一化
        计数 → **调用纯计算 send_eligibility_gate(cursor, task, decision)**（只读，
        零写入）→ 按冻结词汇落库 effective_count（eligible/deferred 都落，
        按 last_rate_decision_id 去重）→ 原样返回 gate 的 V1.9 判别联合之一：
        {"eligible": True, "effective_count": int}
        | {"deferred": True, "effective_count": int, "server_now": aware-dt,
           "deferred_until": aware-dt, "retry_after_ms": 正 int,
           "deferred_reason": str, "response_revision": int}
        | {"terminal": "human_required", "reason": 受控码}
        gate 返回畸形词汇/未知标记时 guard 不得擅自解释——原样上抛由通用层
        fail-closed（只有显式 eligible/deferred/terminal 被处理）。terminal/deferred
        的任务迁移与响应构造由通用层持有（设计 §5.5.1 职责表）。时间口径统一
        DB 侧（clock_timestamp/now()），不依赖应用机墙钟。"""
        ...

    def block_binding(self, cursor, task: Dict[str, Any], reason: str) -> int:  # noqa: ANN001
        """置 automation_blocked=true 且 block_epoch+1（供 permits 拒绝副作用与
        operation_result 异常升级复用）；返回新 block epoch。禁止任何自动清除。"""
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
    # 纯计算只读（V1.9 严格判别联合，见设计卷首变更记录 2）；微信为 None；与 binding_guard 同有同无
    send_eligibility_gate: Optional[Callable[..., Any]]
    binding_guard: Optional[BindingGuard]  # 锁/检查/落库；微信为 None
    scenario_enabled: Callable[[str], bool]  # 场景热读门控（通用生命周期按 task.scenario_key 分派）


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
    恢复动作逐表独立 best-effort：一张表恢复失败不影响其余表的恢复，恢复异常
    分别以 logger.error 显式记录（此时注册表可能偏离调用前状态，不再满足全有
    或全无），不覆盖原始异常。

    锁关系：本模块 _LOCK 与 weixin_conversation.registration._LOCK 是两把不同
    粒度的锁，全局只存在 "registration._LOCK → 本锁" 的唯一获取顺序（ensure_registered
    持外层锁后调本函数），无反向获取路径，不会嵌套死锁；选 RLock 容忍同线程
    重入（上层装配代码持本锁时再次调用本函数）。

    演进注意：未来 register 实现若含写入后动作（校验/通知等），异常分支同样
    必须恢复本表——把该步包进 try/except 并在上面按逆序追加独立的
    best-effort 恢复，不得只回滚注册表写入而遗留写入后动作的副作用。
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
            # 每表独立 best-effort：一张表恢复失败不阻断其余恢复（分别 logger）
            _best_effort_restore("适配器", lambda: _restore_adapter(scenario_key, old_adapter), exc)
            raise
        try:
            register_descriptor(descriptor)
        except Exception as exc:
            _best_effort_restore("决策钩子", lambda: _restore_hooks(scenario_key, old_hooks), exc)
            _best_effort_restore("适配器", lambda: _restore_adapter(scenario_key, old_adapter), exc)
            raise


def _best_effort_restore(table_name: str, restore, original_exc: Exception) -> None:  # noqa: ANN001
    """恢复单张注册表；失败仅 logger.error，不向调用方传播（不覆盖原始异常）。"""
    try:
        restore()
    except Exception as restore_exc:  # noqa: BLE001 恢复失败如实记录，注册表可能偏离调用前状态
        logger.error(
            f"register_scenario 恢复{table_name}失败（原始异常={original_exc!r} "
            f"恢复异常={restore_exc!r}）"
        )


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
