"""有界整数 adapter 契约版本：派发时随 checkpoint 持久化，恢复时校验。

单一版本覆盖整个工具+恢复适配器契约族（ToolDispatcher phase/wait kind、
LocalToolAdapter recover 协议、browser_* resources 键形、children 形状、
恢复证明要求）。这是人工 bump 的显式兼容策略声明，不是源码 hash：
文案/prompt/格式改动不触碰本版本，不让既有任务不可续；也不能默默用改变
语义的新适配器执行旧阶段——未知/被淘汰版本必须明确拒绝恢复。

缺字段遗留行按 ADAPTER_CONTRACT_BASELINE 声明式解释：当前所有 in-flight
checkpoint 均由现行（唯一）v1 语义适配器写入。首次 bump 且淘汰 1 时，
全部遗留缺字段行将被明确拒绝，这是有意的兼容策略决定，兼容窗口决定
登记于 docs/plans/plan-agent-runner-service.md M7。per-fact 微版本先例
（model_phase_version，local_recovery/domain_recovery）不在此重复建面；
若某适配器未来独立演进，再拆分独立版本。
"""

# 缺字段遗留行的声明式基线。
ADAPTER_CONTRACT_BASELINE = 1
# 当前代码派发时写入每个 ExecutionState.resources 的值。
ADAPTER_CONTRACT_VERSION = 1
# 兼容策略显式声明：恢复只接受这些版本，人工 bump。
SUPPORTED_ADAPTER_CONTRACT_VERSIONS = frozenset({1})


def persisted_adapter_contract_version(resources):
    """缺字段遗留行按基线解释；存在但非法的值原样返回，由支持性判断拒绝。"""
    return (resources or {}).get('adapter_contract_version', ADAPTER_CONTRACT_BASELINE)


def adapter_contract_supported(version):
    """严格 int 契约：JSON 篡改出的 true/"1" 不是合法版本，按未知拒绝。"""
    return type(version) is int and version in SUPPORTED_ADAPTER_CONTRACT_VERSIONS


def adapter_contract_mismatch(resources):
    """True 当持久版本未知/被淘汰（或被篡改成非整数）；缺字段按基线放行。"""
    return not adapter_contract_supported(persisted_adapter_contract_version(resources))
