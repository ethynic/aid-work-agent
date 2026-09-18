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
    # 结构化拒绝（B1.2，设计 §5.5.4）：适配器只返回结构化判断，不自行提交事务。
    # control_action 非空表示拒绝需要通用层在同一许可事务内落地控制副作用
    # （binding 同步阻断 + 控制请求 + 审计）并先 commit 再返回拒绝；
    # None（微信现状）→ 既有 denied → 整体 rollback 路径不变。
    # audit_code：脱敏审计码（不携带敏感正文）。
    control_action: Optional[str] = None
    audit_code: Optional[str] = None


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
        invocation: Optional[Dict[str, Any]] = None,
        cursor: Optional[Any] = None,
    ) -> AuthorizeDecision:
        """写动作许可的场景授权校验（许可事务内调用，同时锁定 subject/epoch）；
        返回额度层级（quota_scopes），任一不足由底座在许可事务内整体回滚。
        invocation：许可目标 invocation 行（含 business_ref）——会话任务等需要
        精确执行归属的场景据此复核 assignment/fence 等执行上下文。
        cursor（B1.2，设计 §5.5.4）：调用方许可事务的游标——场景复判必须在
        同一事务/连接上执行（其读与许可事务的行锁/写可见性一致）；None 时
        场景自开连接（仅限 executor 预检等非许可只读路径）。
        拒绝时可通过 control_action/audit_code 返回结构化控制请求（§5.5.4）。"""
        ...

    def validate_submission_evidence(self, ctx: EvidenceContext) -> bool:
        """提交证据（applied/submitted）存在性与归属校验（receipt_policy.mode=
        submission 的场景实现；命名空间如 <ns>:<request_id>:1 由场景冻结）。"""
        ...

    def validate_evidence(
        self, ctx: EvidenceContext
    ) -> bool:
        """写后验证证据的存在性与归属校验（R27 判定链第一环，受信代码内实现）。

        applied+verified 落账前调用：场景按自身证据命名空间/存储校验 evidence_ref
        是否真实存在且归属本次 request_id；不通过返回 False（delivery 收敛 unknown）。
        """
        ...

    def settle_operation_result(self, cursor, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        """operation-result 事务内的场景结算钩子（B1.2，设计 §5.5.4 顺序 3–6）。

        通用层在 evidence 判定后以 SAVEPOINT rate_settlement 包裹调用；同一
        cursor 即结果事务游标（attempt/delivery 行锁已在调用前按冻结矩阵获取）。
        结构化返回（CR 阻断 9）：None=normal；{"status": "anomaly_committed",
        "reason": <受控码>}=补建/落账写入保留且通用层升级（阻断+控制请求+审计
        +ACK）；抛异常=通用层 ROLLBACK TO SAVEPOINT 撤销本结算写入并走异常升级。
        无场景账本的场景（微信）实现为 no-op 返回 None。
        result：受控回执事实（tenant_id/task_id/invocation_id/delivery_id/
        attempt_id/effect/phase/evidence_invalid/request_id/permit_id）。
        """
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
