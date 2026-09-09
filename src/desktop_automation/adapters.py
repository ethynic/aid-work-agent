"""场景适配器协议与受信注册表（desktop_automation 设计 §1）

依赖方向为场景→底座→本地操作通道；底座不得 import 微信或 BOSS 业务模块。
场景通过注册的适配器提供 validate_revision / resolve_target / authorize_operation /
compile_operations / aggregate_result / validate_evidence / serve_payload；
只接受受信代码在进程内显式注册，不允许运行时加载任意脚本
（TrustedAdapterRegistry 无任何动态导入能力）。
"""

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class AdapterContext:
    """适配器调用上下文：租户/属主来自持久任务或受信认证上下文，不来自模型参数"""

    tenant_id: str
    user_id: str
    scenario_key: str
    task_ref: str
    revision_ref: str


@dataclass(frozen=True)
class EvidenceContext:
    """validate_evidence 调用上下文（R27）：租户/场景/本次请求与待验证据引用。

    request_id 为本次 attempt 的请求标识；evidence_ref 必须由适配器按场景证据
    命名空间校验存在性与归属（是否编码本次 request_id 由场景自定），不受信引用
    返回 False——判定链据 此拒绝「不存在的证据」「其他操作未登记证据」。
    """

    tenant_id: str
    scenario_key: str
    request_id: str
    evidence_ref: str


@dataclass(frozen=True)
class RevisionValidation:
    ok: bool
    reason: Optional[str] = None
    # 冻结调度配置（compile/接纳路径使用；底座不重新解析自然语言）：
    # kind/timezone/anchor/interval/cron/day_of_week/grace/miss_policy/max_count/one_shot 等
    schedule_specs: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TargetResolution:
    ok: bool
    target_handle: Optional[str] = None
    target_version: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class QuotaScopeSpec:
    """一次写动作的额度层级（R9）：scope_type/scope_id/opaque + limit + 窗口"""

    scope_type: str
    scope_id: str
    limit_count: int
    window_seconds: int


@dataclass(frozen=True)
class AuthorizeDecision:
    allowed: bool
    reason: Optional[str] = None
    quota_scopes: List[QuotaScopeSpec] = field(default_factory=list)


@dataclass(frozen=True)
class CompiledOperation:
    """场景编译出的中立操作描述分量（正文不进底座，只存 payload_ref/hash 引用）"""

    position: int
    operation: str
    provider_key: str
    target_ref: str
    target_handle: Optional[str] = None
    target_version: Optional[str] = None
    payload_ref: Optional[str] = None
    payload_hash: Optional[str] = None


@dataclass(frozen=True)
class RunBusinessResult:
    """场景对整轮 run 的业务判定（与机器 effect 分别存储，人工判定不覆盖机器证据）"""

    verdict: str
    summary: Optional[str] = None


@runtime_checkable
class ScenarioAdapter(Protocol):
    """场景适配器协议（执行器为 weixin.fixed_content.v1 / boss.chat_reply.v1 等场景实现）"""

    scenario_key: str

    def validate_revision(
        self, ctx: AdapterContext, revision_config: Dict[str, Any]
    ) -> RevisionValidation:
        """校验版本配置合法性并产出冻结调度配置（发布事务内调用）"""
        ...

    def resolve_target(self, ctx: AdapterContext, target_ref: str) -> TargetResolution:
        """持久 target_ref → 本次设备/账号短期 handle + 版本（probe/target resolve 链路）"""
        ...

    def authorize_operation(
        self,
        ctx: AdapterContext,
        *,
        operation: str,
        target_ref: str,
        target_version: Optional[str],
        payload_hash: Optional[str],
        authorization_revision: Optional[str],
        authorization_epoch: Optional[int],
    ) -> AuthorizeDecision:
        """写动作许可的场景授权校验（许可事务内调用，同时锁定 subject/epoch）；
        返回额度层级（quota_scopes），任一不足由底座在许可事务内整体回滚"""
        ...

    def compile_operations(
        self, ctx: AdapterContext, revision_config: Dict[str, Any]
    ) -> List[CompiledOperation]:
        """按冻结配置编译本轮 deliveries（有序操作描述）"""
        ...

    def aggregate_result(
        self, ctx: AdapterContext, delivery_results: List[Dict[str, Any]]
    ) -> RunBusinessResult:
        """场景业务判定（run 终态后调用；不改变底座机器聚合结果）"""
        ...

    def validate_evidence(self, ctx: EvidenceContext) -> bool:
        """写后验证证据的存在性与归属校验（R27 判定链第一环，受信代码内实现）。

        applied+verified 落账前调用：场景按自身证据命名空间/存储校验 evidence_ref
        是否真实存在且归属本次 request_id；不通过返回 False（delivery 收敛 unknown）。
        """
        ...

    def serve_payload(self, ctx: AdapterContext, payload_ref: str) -> bytes:
        """受控 payload 字节（resolver 来源；不接任意 URL/路径）"""
        ...


class AdapterNotFoundError(Exception):
    """scenario_key 未注册适配器（fail-loud，不静默降级）"""


class TrustedAdapterRegistry:
    """进程内受信适配器注册表：仅显式 register 调用可写入，无运行时脚本加载"""

    _adapters: Dict[str, ScenarioAdapter] = {}
    _lock = threading.Lock()

    @classmethod
    def register(cls, adapter: ScenarioAdapter) -> None:
        """注册受信适配器（代码内显式调用；测试与场景装配时使用）"""
        with cls._lock:
            cls._adapters[adapter.scenario_key] = adapter

    @classmethod
    def unregister(cls, scenario_key: str) -> None:
        """注销适配器（测试清理用；生产路径不调用）"""
        with cls._lock:
            cls._adapters.pop(scenario_key, None)

    @classmethod
    def get(cls, scenario_key: str) -> Optional[ScenarioAdapter]:
        with cls._lock:
            return cls._adapters.get(scenario_key)

    @classmethod
    def require(cls, scenario_key: str) -> ScenarioAdapter:
        adapter = cls.get(scenario_key)
        if adapter is None:
            raise AdapterNotFoundError(f"场景 {scenario_key} 未注册受信适配器")
        return adapter

    @classmethod
    def registered_keys(cls) -> List[str]:
        with cls._lock:
            return sorted(cls._adapters.keys())
