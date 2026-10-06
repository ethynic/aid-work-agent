"""skill-runner Provider 派发代理（M2 云端执行路由，plan-external-skill-plugin-m2.md §4.2）

职责：把「已审批插件 + execution=device」技能的 skill_execute 命令，经既有
invocation 队列（LocalInvocationService.enqueue）下发到用户 Windows 设备的
skill-runner 通用执行器执行并回传 stdout 结果。

架构定位（调研 §4.2 / 计划 §1）：skill-runner 是通用执行器 Provider，不是封闭
schema 的第一方 CLI——Provider 规范「禁止 command/raw_argv 等任意执行参数」的
约束由「云端命令门禁（entry ∈ 审批 entries 白名单 + 参数上限）+ exec_hash 对账
+ 设备侧命令门禁」三层补偿（计划 §3.1/§3.6）。

模块构成：
- 常量：SKILL_RUNNER_PROVIDER_KEY / SKILL_RUNNER_TOOL_NAME / 拦截区分码；
- DeviceExecutionDecision + evaluate_device_execution：§3.5 判定 a–d（不含命令解析 e），
  use_skill（手册尾注）与 skill_executor（执行路由）共用同一决策函数——
  「提示可执行 ⇔ 实际可执行」不漂移；
- DeviceCommandLimits + parse_device_skill_command：§3.1 命令解析（shlex 纯函数，
  独立可测）——解析成功即意味着原始命令字符串不再下传，设备收到的是结构化
  entry/args，绝不转发 shell 命令串；
- SkillScriptRunDispatchTool(LocalToolProxyTool)：内部派发器（不注册 LLM 可见面），
  复用基类 execute() 主体（设备闸门/计费预检/轮询/终态映射/超时取消），版本门第 1 层
  经覆写 _dispatch_and_wait 插入（基类在 _find_ready_device 与 enqueue 之间无插入钩子，
  计划事实 20）；
- decision_error_message / decision_use_skill_hint：§3.7 文案（含 substring 约束表——
  M1 既有断言优先于措辞偏好）。

M2 安全基线（与 M1 的「插件来源全量拦截」关系）：放行条件精确到
「已审批插件 + execution=device + 审批含 entries/exec_hash + 设备执行开关开启」
（§3.5 a–d 全满足）；server 声明插件与未审批插件仍全拦；存量审批条目（无 entries）
→ fail-closed 拒绝设备执行（判定 d 项）。
"""

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from src.config.settings import settings
from src.core import skill_plugin_gate as gate
from src.core.skill_executor import ExecutionResult
from src.local_tools.proxy_tool import LocalToolProxyTool

# skill-runner Provider 注册键（src/local_tools/catalog.py TRUSTED_PROVIDERS 同名条目）
SKILL_RUNNER_PROVIDER_KEY = "skill-runner"
# 台账/invocation 行 tool_name（单一科目；payload schema 见 dispatch）
SKILL_RUNNER_TOOL_NAME = "skill_script_run"

# §3.5 / §3.7 拦截区分码（ExecutionResult.error 与文案映射共用；稳定码不随措辞变）
BUILTIN_DEVICE_UNSUPPORTED = "BUILTIN_DEVICE_UNSUPPORTED"
PLUGIN_NOT_EXECUTABLE = "PLUGIN_NOT_EXECUTABLE"
DEVICE_EXECUTION_DISABLED = "DEVICE_EXECUTION_DISABLED"
INVALID_DEVICE_COMMAND = "INVALID_DEVICE_COMMAND"
INVALID_DEVICE_INPUT = "INVALID_DEVICE_INPUT"
SKILL_NOT_INSTALLED = "SKILL_NOT_INSTALLED"
SKILL_VERSION_MISMATCH = "SKILL_VERSION_MISMATCH"

# §3.1 参数总长上限（云端入队前 + 设备执行前双端强制；与设备端 TS 镜像同值）
MAX_TOTAL_ARG_CHARS = 4000
# §3.1 可选解释器前缀（匹配则剥离——设备端解释器由设备配置决定，云端不指定）
_INTERPRETER_PREFIXES = frozenset({"python", "python3", "py"})


# ---------------------------------------------------------------------------
# 设备执行判定（§3.5 判定 a–d，use_skill 与 executor 共用）
# ---------------------------------------------------------------------------

ROUTE_SERVER = "server"
ROUTE_PLUGIN_BLOCKED = "plugin_blocked"
ROUTE_BUILTIN_DEVICE_UNSUPPORTED = "builtin_device_unsupported"
ROUTE_PLUGIN_NOT_EXECUTABLE = "plugin_not_executable"
ROUTE_DEVICE_EXECUTION_DISABLED = "device_execution_disabled"
ROUTE_DEVICE_READY = "device_ready"


@dataclass(frozen=True)
class DeviceExecutionDecision:
    """evaluate_device_execution 的判定结果。

    status 取 ROUTE_* 之一；executable 仅在 ROUTE_DEVICE_READY（判定 a–d 全满足）。
    entries/exec_hash/version：审批条目快照（仅 device_ready 时有值）——entries 为
    命令解析（判定 e）的入口白名单，exec_hash 为设备端对账 hash，version 仅日志/展示
    （不参与对账判定，防版本字符串伪造）。
    reason：细项原因（日志/排障用；拦截文案按 status 走区分码常量，不依赖 reason 措辞）。
    """

    status: str
    skill_name: str
    entries: Tuple[str, ...] = ()
    exec_hash: Optional[str] = None
    version: str = ""
    reason: Optional[str] = None

    @property
    def executable(self) -> bool:
        return self.status == ROUTE_DEVICE_READY

    @property
    def reason_code(self) -> Optional[str]:
        """拦截区分码（ExecutionResult.error / 日志 extra 用）；server/放行为 None。

        PLUGIN_BLOCKED（插件未声明 device，维持 M1 拦截）无独立三段式码——
        沿用 M1 的语义串「External plugin execution not open」。
        """
        if self.status == ROUTE_PLUGIN_BLOCKED:
            return "External plugin execution not open"
        if self.status == ROUTE_BUILTIN_DEVICE_UNSUPPORTED:
            return BUILTIN_DEVICE_UNSUPPORTED
        if self.status == ROUTE_PLUGIN_NOT_EXECUTABLE:
            return PLUGIN_NOT_EXECUTABLE
        if self.status == ROUTE_DEVICE_EXECUTION_DISABLED:
            return DEVICE_EXECUTION_DISABLED
        return None


def _read_current_approval_entry(skill_name: str) -> Optional[Dict[str, Any]]:
    """实时二次读取审批清单中该 skill 的设备执行项（entries/exec_hash/version）。

    判定 d 项的数据源（计划 §4.2 实现者注意）：registry._plugin_hashes 值仅为
    (computedHash, skillMdHash) 二元组——entries/exec_hash/mutable 不在其中，
    必须每次判定时实时读清单（几 KB JSON，开销可忽略）。

    fail-closed：清单缺失/损坏（read_approvals → {}）、条目不存在、entries 空/形态非法、
    exec_hash 空/非字符串 → 一律返回 None（调用方判 PLUGIN_NOT_EXECUTABLE）——
    与判定 b（registry 进程内缓存态）两处独立 fail-closed，不互相兜底放行。
    """
    try:
        approvals = gate.read_approvals(gate.resolve_approvals_file())
    except Exception as e:  # noqa: BLE001 清单读取任何异常都 fail-closed（不兜底放行）
        logger.warning(f"[SkillRunnerProxy] 审批清单二次读取失败（fail-closed 拒绝设备执行）: {e}")
        return None
    entry = approvals.get(skill_name)
    if not isinstance(entry, dict):
        return None
    entries = entry.get("entries")
    if not isinstance(entries, list) or not [
        item for item in entries if isinstance(item, str) and item.strip()
    ]:
        return None
    exec_hash = entry.get("exec_hash")
    if not isinstance(exec_hash, str) or not exec_hash.strip():
        return None
    return entry


def evaluate_device_execution(registry, skill_name: str) -> DeviceExecutionDecision:
    """§3.5 放行判定 a–d（不含命令解析 e——解析在 executor 路由分支对 command 单独做）。

    判定顺序（与 §3.5 编号一致；任一不满足即 fail-closed 拦截）：
    1. decl.execution != "device" → 插件来源拦截（ROUTE_PLUGIN_BLOCKED，维持 M1 规则）；
       内置 → ROUTE_SERVER（现状行为不变）；
    2. execution == "device" 时 a–d 全满足才 ROUTE_DEVICE_READY：
       a. registry.is_plugin_skill（目录身份 fail-closed）→ 否则 BUILTIN_DEVICE_UNSUPPORTED
          （内置 skill 无审批 hash，无法设备对账，不支持设备执行）；
       b. name ∈ registry._plugin_hashes（当前审批集；租户 TTL 半状态窗口缺名 fail-closed）；
       c. settings.skills.plugins.device_execution.enabled（M2 开关，默认关）；
       d. 审批条目含非空 entries + exec_hash（实时 read_approvals 二次读取；
          旧清单条目无 entries → 拒绝，即向后兼容 fail-closed）。
    b/d 均不满足 → PLUGIN_NOT_EXECUTABLE；c 不满足 → DEVICE_EXECUTION_DISABLED。
    """
    decl = None
    if hasattr(registry, "get_execution_decl"):
        try:
            decl = registry.get_execution_decl(skill_name)
        except Exception as e:
            logger.warning(f"skill '{skill_name}' 执行声明读取失败，按默认 server 处理: {e}")
            decl = None
    is_device = bool(decl and decl.get("execution") == "device")

    is_plugin = False
    if hasattr(registry, "is_plugin_skill"):
        try:
            is_plugin = bool(registry.is_plugin_skill(skill_name))
        except Exception as e:
            logger.warning(f"skill '{skill_name}' 插件身份判定失败，按非插件处理: {e}")
            is_plugin = False

    if not is_device:
        # §3.5 第 1 条：未声明 device → 维持 M1 规则——插件来源拦截、内置 server 现状不变
        if is_plugin:
            return DeviceExecutionDecision(
                status=ROUTE_PLUGIN_BLOCKED, skill_name=skill_name,
                reason="插件未声明 metadata.execution=device（云端命令执行不对外开放）")
        return DeviceExecutionDecision(status=ROUTE_SERVER, skill_name=skill_name)

    # a. 内置声明 device → 无审批 hash 可对账，不支持设备执行
    if not is_plugin:
        return DeviceExecutionDecision(
            status=ROUTE_BUILTIN_DEVICE_UNSUPPORTED, skill_name=skill_name,
            reason="内置 skill 声明 device 执行（无审批 exec_hash，无法设备对账）")

    # b. 当前审批集（registry 进程内缓存态；TTL 半状态窗口缺名 fail-closed）
    plugin_hashes = getattr(registry, "_plugin_hashes", None) or {}
    if skill_name not in plugin_hashes:
        return DeviceExecutionDecision(
            status=ROUTE_PLUGIN_NOT_EXECUTABLE, skill_name=skill_name,
            reason="skill 不在 registry._plugin_hashes（租户 TTL 半状态残留或审批已撤销）")

    # c. M2 设备执行开关（默认关）
    if not settings.skills.plugins.device_execution.enabled:
        return DeviceExecutionDecision(
            status=ROUTE_DEVICE_EXECUTION_DISABLED, skill_name=skill_name,
            reason="skills.plugins.device_execution.enabled=false（管理员配置）")

    # d. 审批条目二次读取（实时清单；缺 entries/exec_hash fail-closed）
    approval_entry = _read_current_approval_entry(skill_name)
    if approval_entry is None:
        return DeviceExecutionDecision(
            status=ROUTE_PLUGIN_NOT_EXECUTABLE, skill_name=skill_name,
            reason="审批清单缺失/损坏或条目缺 entries/exec_hash（旧格式条目不支持设备执行，需重新审批）")
    return DeviceExecutionDecision(
        status=ROUTE_DEVICE_READY, skill_name=skill_name,
        entries=tuple(str(item).strip() for item in approval_entry["entries"]),
        exec_hash=str(approval_entry["exec_hash"]).strip(),
        version=str(approval_entry.get("version") or ""),
    )


# ---------------------------------------------------------------------------
# 命令解析（§3.1，纯函数独立可测）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceCommandLimits:
    """命令参数上限（云端入队前 + 设备执行前双端强制；from_settings 与设备端 payload 同源）"""
    max_args: int = 16
    max_arg_chars: int = 500
    max_total_chars: int = MAX_TOTAL_ARG_CHARS

    @classmethod
    def from_settings(cls) -> "DeviceCommandLimits":
        cfg = settings.skills.plugins.device_execution
        return cls(max_args=int(cfg.max_args), max_arg_chars=int(cfg.max_arg_chars))


@dataclass(frozen=True)
class DeviceCommandParse:
    """parse_device_skill_command 的解析结果（ok=True 时 entry/args 可信，error 恒空）"""
    ok: bool
    entry: str = ""
    args: Tuple[str, ...] = ()
    error: str = ""


def parse_device_skill_command(
    command: str,
    allowed_entries: Sequence[str],
    limits: DeviceCommandLimits,
) -> DeviceCommandParse:
    """命令字符串 → 结构化 (entry, args)（§3.1 解析规则，纯函数）。

    规则：shlex.split → 剥离可选解释器前缀（首 token 匹配 python/python3/py 时
    丢弃——设备端解释器由设备配置决定，云端不指定）→ 首 token 为 entry（去 ./
    前缀、反斜杠归一化为正斜杠）→ entry 必须 ∈ 审批条目 entries 白名单（审批时
    快照，非当前磁盘声明——信任锚是审批）→ 其余 token 依序为 args。
    任何一步失败 → ok=False（error 为中文原因，含合法 entries 清单供 LLM 自纠）；
    解析成功也意味着原始命令字符串不再下传。
    """
    try:
        tokens = shlex.split(command or "")
    except ValueError as e:
        return DeviceCommandParse(ok=False, error=f"命令引号/转义形态无法解析: {e}")
    if not tokens:
        return DeviceCommandParse(ok=False, error="命令为空（无可执行的入口脚本）")
    if tokens[0].lower() in _INTERPRETER_PREFIXES:
        tokens = tokens[1:]
        if not tokens:
            return DeviceCommandParse(
                ok=False, error="命令仅含解释器前缀（python/python3/py），缺少入口脚本")
    entry_raw = tokens[0]
    entry = entry_raw.replace("\\", "/")
    while entry.startswith("./"):
        entry = entry[2:]
    if not entry or entry.endswith("/") or any(part == "" for part in entry.split("/")):
        return DeviceCommandParse(ok=False, error=f"入口 '{entry_raw}' 不是有效的脚本路径")
    allowed = [str(item).strip() for item in allowed_entries if str(item).strip()]
    if entry not in allowed:
        listing = "、".join(allowed) if allowed else "（无——该技能审批未含入口白名单，需重新审批）"
        return DeviceCommandParse(
            ok=False,
            error=f"入口 '{entry}' 不在该技能审批的入口白名单内；合法入口：{listing}")
    args = tuple(tokens[1:])
    if len(args) > limits.max_args:
        return DeviceCommandParse(
            ok=False, error=f"参数数量 {len(args)} 超过上限 {limits.max_args}")
    for arg in args:
        if len(arg) > limits.max_arg_chars:
            return DeviceCommandParse(
                ok=False,
                error=f"参数 '{arg[:60]}…' 长度 {len(arg)} 超过单参数上限 {limits.max_arg_chars}")
    total = sum(len(arg) for arg in args)
    if total > limits.max_total_chars:
        return DeviceCommandParse(
            ok=False, error=f"参数总长度 {total} 超过上限 {limits.max_total_chars}")
    return DeviceCommandParse(ok=True, entry=entry, args=args)


# ---------------------------------------------------------------------------
# 拦截文案（§3.7 + substring 约束表——M1 既有断言优先于措辞偏好）
# ---------------------------------------------------------------------------

# 插件未声明 device（execute_skill_command / execute_skill_script 两入口共用）：
# 必含「外部 Skill 插件」「请勿重试」（test_skill_execute_plugin_guard.py :118-119/:141-142）
_PLUGIN_EXECUTION_BLOCKED_MESSAGE = (
    "技能 '{name}' 来自外部 Skill 插件，且未声明设备执行（metadata.execution=device）。"
    "外部 Skill 插件的云端命令执行不对外开放：仅支持声明设备执行（metadata.execution=device）"
    "且经审批含入口白名单的技能。请勿重试 skill_execute 或尝试改写命令形式；"
    "可基于已加载手册回答用户咨询，或向用户说明该技能当前不支持云端执行。"
)
# 内置声明 device：必含「设备执行」「metadata.execution=device」「请勿重试」（:147-153）
_BUILTIN_DEVICE_UNSUPPORTED_MESSAGE = (
    "技能 '{name}' 为内置技能且声明为设备执行（metadata.execution=device）。"
    "内置技能无审批内容 hash（exec_hash），无法进行设备端内容对账，不支持设备执行。"
    "请勿重试 skill_execute；可向用户说明该技能的执行方式当前不受支持。"
)
# device 插件但审批/清单不满足（b 或 d 不满足）：含「设备执行」+「请勿重试」；
# 不含「外部 Skill 插件」连续字样（:161 负向断言——措辞用「外部插件技能」避开）
_PLUGIN_NOT_EXECUTABLE_MESSAGE = (
    "技能 '{name}' 为外部插件技能并声明设备执行，但当前不在有效审批集内"
    "（或审批条目缺少设备执行入口 entries/exec_hash——旧格式清单条目不支持设备执行）。"
    "需管理员（重新）审批该技能并写入设备执行入口白名单后方可执行。"
    "请勿重试 skill_execute；可向用户说明该技能需先完成设备执行审批。"
)
# device 插件且审批/审批集满足，但 M2 开关未开：同上 substring 约束
_DEVICE_EXECUTION_DISABLED_MESSAGE = (
    "技能 '{name}' 为外部插件技能并声明设备执行，但设备执行链路未开启"
    "（管理员配置 skills.plugins.device_execution.enabled=false）。"
    "请勿重试 skill_execute；可向用户说明该技能的设备执行功能暂未开启、"
    "需管理员启用后再用。"
)
# 第二执行入口（execute_skill_script）对 device_ready 技能的全拦文案
# （第二入口不路由设备，防绕过；M2 维持 M1 全拦语义，仅文案更新）
_DEVICE_ROUTE_ONLY_MESSAGE = (
    "技能 '{name}' 为设备执行路由技能，不支持脚本直接执行入口（execute_skill_script）。"
    "请经 skill_execute 命令入口（python <入口> <参数…> 或 <入口> <参数…>）执行。"
    "请勿重试 execute_skill_script。"
)
# files/content 的 fail-closed 拒绝文案（§3.1——不静默丢弃）。
# 注意 files 文案含字面量 ${filename} 占位语法示例：.format 模板中写作 {{filename}}
# （format 转义 → 输出 {filename}，与 $ 连读为 ${filename}，避免被当作 format 字段）
_INVALID_DEVICE_INPUT_FILES_MESSAGE = (
    "技能 '{name}' 为设备执行路由技能，不支持 files 文件参数——${{filename}} 占位替换"
    "仅在云端容器工作目录语义下生效，透传到设备端只会把字面量塞进参数。"
    "请将所需内容直接写入命令参数后重试。"
)
_INVALID_DEVICE_INPUT_CONTENT_MESSAGE = (
    "技能 '{name}' 为设备执行路由技能，不支持 content 参数——内容无法经 stdin 传递到"
    "设备端脚本（设备侧脚本 stdin 恒为关闭状态）。请将所需内容直接写入命令参数后重试。"
)
# 版本门第 1 层文案（§3.2，dispatch 侧——不建 invocation）
_SKILL_NOT_INSTALLED_MESSAGE = (
    "技能 '{name}' 尚未安装到选定设备。请在设备端安装该技能（Runtime skills 目录）后重试，"
    "或向用户说明需要先在电脑上安装"
)
_SKILL_VERSION_MISMATCH_MESSAGE = (
    "设备上的技能 '{name}' 内容与云端审批版本不一致。请更新设备端技能后重试；"
    "若设备为新版本需云端重新审批"
)

# executor 侧（ExecutionResult.stderr）文案映射——拦截分支共用
_DECISION_EXECUTOR_MESSAGES = {
    ROUTE_PLUGIN_BLOCKED: _PLUGIN_EXECUTION_BLOCKED_MESSAGE,
    ROUTE_BUILTIN_DEVICE_UNSUPPORTED: _BUILTIN_DEVICE_UNSUPPORTED_MESSAGE,
    ROUTE_PLUGIN_NOT_EXECUTABLE: _PLUGIN_NOT_EXECUTABLE_MESSAGE,
    ROUTE_DEVICE_EXECUTION_DISABLED: _DEVICE_EXECUTION_DISABLED_MESSAGE,
    ROUTE_DEVICE_READY: _DEVICE_ROUTE_ONLY_MESSAGE,
}
# use_skill 手册尾注文案映射——尾动词「请勿调用 skill_execute」（executor 侧为
# 「请勿重试 skill_execute」，M1 既有断言分别锚定两种形态）
_DECISION_USE_SKILL_HINTS = {
    ROUTE_PLUGIN_BLOCKED: (
        "**注意**：技能 '{name}' 来自外部 Skill 插件，且未声明设备执行"
        "（metadata.execution=device）。外部 Skill 插件的云端命令执行不对外开放："
        "仅支持声明设备执行且经审批含入口白名单的技能。请勿调用 skill_execute"
        " 或尝试改写命令形式；可基于本手册回答用户咨询，或向用户说明该技能"
        " 当前不支持云端执行。"
    ),
    ROUTE_BUILTIN_DEVICE_UNSUPPORTED: (
        "**注意**：技能 '{name}' 为内置技能且声明为设备执行"
        "（metadata.execution=device），但内置技能无审批内容 hash（exec_hash），"
        "无法进行设备端内容对账，不支持设备执行。请勿调用 skill_execute，"
        "可向用户说明该技能的执行方式当前不受支持。"
    ),
    ROUTE_PLUGIN_NOT_EXECUTABLE: (
        "**注意**：技能 '{name}' 为外部插件技能并声明设备执行，但当前不在有效审批集内"
        "（或审批条目缺少设备执行入口 entries/exec_hash——旧格式清单条目不支持设备执行）。"
        "请勿调用 skill_execute，可向用户说明该技能需先完成设备执行审批。"
    ),
    ROUTE_DEVICE_EXECUTION_DISABLED: (
        "**注意**：技能 '{name}' 为外部插件技能并声明设备执行，但设备执行链路未开启"
        "（管理员配置 skills.plugins.device_execution.enabled=false）。"
        "请勿调用 skill_execute，可向用户说明该技能的设备执行功能暂未开启、"
        "需管理员启用后再用。"
    ),
    # 放行路由达成：设备执行说明（非「请勿」类拦截提示——断言要求含「设备执行」
    # 且不含「请勿」字样）
    ROUTE_DEVICE_READY: (
        "**注意**：技能 '{name}' 将通过本机设备执行：调用 skill_execute 后任务会下发到"
        "你配对的电脑（Runtime 需在线、已安装同版本技能）执行；执行期间会操作该电脑"
        "桌面（键鼠/窗口），请提醒用户不要同时使用鼠标键盘。"
    ),
}


def decision_error_message(decision: DeviceExecutionDecision) -> str:
    """拦截分支的 executor 侧文案（ExecutionResult.stderr）——按区分码映射，未注册返回兜底。"""
    template = _DECISION_EXECUTOR_MESSAGES.get(decision.status)
    if template is None:
        logger.warning(f"[SkillRunnerProxy] 未知判定状态（兜底拦截文案）: {decision}")
        return f"技能 '{decision.skill_name}' 当前不支持设备执行路由，请勿重试 skill_execute。"
    return template.format(name=decision.skill_name)


def decision_use_skill_hint(decision: DeviceExecutionDecision) -> Optional[str]:
    """use_skill 手册尾注文案——内置 server 技能返回 None（走通用执行指引）。

    与 executor 侧文案同源决策（evaluate_device_execution 共用），保证
    「提示可执行 ⇔ 实际可执行」不漂移；各 status 的尾动词按 M1 既有断言
    分别锚定「请勿调用 skill_execute」/（executor 侧）「请勿重试 skill_execute」。
    """
    template = _DECISION_USE_SKILL_HINTS.get(decision.status)
    if template is None:
        return None
    return template.format(name=decision.skill_name)


def invalid_device_input_message(kind: str, skill_name: str) -> str:
    """§3.1 files/content 的 fail-closed 拒绝文案（INVALID_DEVICE_INPUT）"""
    if kind == "files":
        return _INVALID_DEVICE_INPUT_FILES_MESSAGE.format(name=skill_name)
    return _INVALID_DEVICE_INPUT_CONTENT_MESSAGE.format(name=skill_name)


def invalid_device_command_message(skill_name: str, parse: DeviceCommandParse) -> str:
    """INVALID_DEVICE_COMMAND 文案：附失败原因与合法入口清单（LLM 可自纠——命令级错误，
    允许改写为正确形态后重试，不写「请勿重试」类绝对化提示）"""
    return (
        f"技能 '{skill_name}' 的设备执行命令解析失败：{parse.error}。"
        "请按「python <入口> <参数…>」或「<入口> <参数…>」形式改写命令后重新调用 skill_execute。"
    )


# ---------------------------------------------------------------------------
# 派发工具（LocalToolProxyTool 内部派发器，不注册 LLM 可见面）
# ---------------------------------------------------------------------------

def _tail_keep(text: str, max_chars: int) -> str:
    """尾部保留截断（traceback 尾部最有诊断价值；与设备端 stdout 截断策略一致）"""
    text = str(text or "")
    if max_chars > 0 and len(text) > max_chars:
        return text[-max_chars:]
    return text


class SkillScriptRunDispatchTool(LocalToolProxyTool):
    """内部派发器（M2）：已审批插件 device 技能 → skill-runner Provider invocation 下发。

    不注册 LLM 可见面（不加 LOCAL_PROXY_TOOL_CLASSES——基类 catalog=False 不进自动目录；
    SUBAGENT 白名单按名称交集注册的机制不受影响，skill_script_run 不在任何白名单）。
    skill_execute 保持单入口，路由对 LLM 透明（计划 §1.2）。

    复用基类 execute() 主体：设备闸门 / 计费预检（价 0 预检跳过） / enqueue /
    进度轮询 / 超时 request_cancel / 终态映射全部继承；版本门第 1 层经覆写
    _dispatch_and_wait 插入（enqueue 前校验设备 capabilities_json.skills，失败直接
    返回错误 dict、不建 invocation）。stdin_content 恒不透传（§3.1——服务端注入的
    身份 JSON 是内部管道数据，设备侧脚本无消费方；设备 spawn stdin 'ignore'，
    读 stdin 的脚本立即 EOF 快速失败）。
    """

    name = SKILL_RUNNER_TOOL_NAME
    display_name = "本机设备技能脚本执行"
    description = (
        "（内部派发器，不经 LLM 直调）已审批插件设备技能脚本经 skill-runner Provider"
        " 下发本机执行；入口白名单与 exec_hash 来自审批快照，payload 为结构化"
        " skill/entry/args/exec_hash，不转发 shell 命令串"
    )
    category = "local_skill_runner"
    provider_key = SKILL_RUNNER_PROVIDER_KEY
    # invocation 行 provider_key：claim 行级过滤只派给覆盖 skill-runner 能力的设备
    invocation_provider_key = SKILL_RUNNER_PROVIDER_KEY
    # 弹层自愈是 BOSS 页面编排，对设备脚本执行不适用，绝不触发
    heal_eligible = False
    unsupported_provider_message = (
        "选定设备不支持技能脚本执行。请确认本机 Runtime 已启用 skill-runner 能力"
        "（配置 skills.python 受管解释器并安装对应技能）后再试"
    )
    timeout_seconds = 900  # 默认值；dispatch 时按 settings.device_execution.timeout_seconds 现读注入

    def _check_device_skill_capabilities(
        self, device: Dict[str, Any], payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """版本门第 1 层（§3.2 下发前校验：设备闸门之后、enqueue 之前）。

        从选定设备 capabilities_json.skills（设备心跳上报的已安装清单）查找
        name == payload.skill 的条目：无该 name（或清单形态非法/缺失）→ SKILL_NOT_INSTALLED；
        有 name 但 hash != 审批 exec_hash → SKILL_VERSION_MISMATCH。均不建 invocation、
        设备不出工（fail-closed；设备端另有执行前重算对账兜底心跳窗口内的陈旧 capabilities）。
        """
        skill_name = str(payload.get("skill") or "")
        exec_hash = str(payload.get("exec_hash") or "")
        capabilities = device.get("capabilities_json")
        skills_list = capabilities.get("skills") if isinstance(capabilities, dict) else None
        if not isinstance(skills_list, list):
            # 设备未上报技能清单（老版本 Runtime / 未配置 skills.python）→ 视为未安装
            logger.info(f"后端日志：skill_script_run 设备未上报技能清单（按未安装 fail-closed）", extra={
                "skill_name": skill_name, "device_id": str(device.get("id") or ""),
            })
            return {"success": False, "code": SKILL_NOT_INSTALLED,
                    "message": _SKILL_NOT_INSTALLED_MESSAGE.format(name=skill_name),
                    "effect": None, "data": None, "invocation_id": None}
        installed = [
            item for item in skills_list
            if isinstance(item, dict) and str(item.get("name") or "") == skill_name
        ]
        if not installed:
            logger.info(f"后端日志：skill_script_run 设备未安装该技能（SKILL_NOT_INSTALLED）", extra={
                "skill_name": skill_name, "device_id": str(device.get("id") or ""),
            })
            return {"success": False, "code": SKILL_NOT_INSTALLED,
                    "message": _SKILL_NOT_INSTALLED_MESSAGE.format(name=skill_name),
                    "effect": None, "data": None, "invocation_id": None}
        device_hash = str(installed[0].get("hash") or "")
        if device_hash != exec_hash:
            logger.warning(
                f"后端日志：skill_script_run 设备技能 hash 与云端审批不一致"
                f"（SKILL_VERSION_MISMATCH，设备不执行）", extra={
                    "skill_name": skill_name,
                    "device_id": str(device.get("id") or ""),
                    "device_hash_prefix": device_hash[:12],
                    "approval_hash_prefix": exec_hash[:12],
                })
            return {"success": False, "code": SKILL_VERSION_MISMATCH,
                    "message": _SKILL_VERSION_MISMATCH_MESSAGE.format(name=skill_name),
                    "effect": None, "data": None, "invocation_id": None}
        return None

    async def _dispatch_and_wait(
        self,
        tenant_id: str,
        user_id: str,
        session_id: Optional[str],
        device: Dict[str, Any],
        args: Dict[str, Any],
        progress_queue: Optional[asyncio.Queue],
    ) -> Dict[str, Any]:
        """版本门第 1 层插入点（计划事实 20：基类在 _find_ready_device 与 enqueue 之间
        无插入钩子，只能经覆写本方法实现）——校验失败直接返回错误 dict（不建 invocation），
        通过后按基类同语义 enqueue/轮询。"""
        gate_result = self._check_device_skill_capabilities(device, args)
        if gate_result is not None:
            return gate_result
        return await super()._dispatch_and_wait(
            tenant_id, user_id, session_id, device, args, progress_queue)

    async def dispatch(
        self,
        *,
        skill: str,
        entry: str,
        args: Sequence[str],
        exec_hash: str,
        version: str = "",
        tenant_id: str,
        user_id: str,
        session_id: Optional[str] = None,
        context=None,
        progress_queue: Optional[asyncio.Queue] = None,
    ) -> ExecutionResult:
        """executor 路由分支入口：组装 payload kwargs 调 execute() 并映射回 ExecutionResult。

        payload（§3.1 schema，经基类 execute 的非下划线参数收集成为 invocation
        arguments_json，设备侧 inv.arguments 直接消费）：
            {skill, version, entry, args, exec_hash, timeout_seconds}
        version 仅日志/展示（不参与对账判定）；timeout_seconds 由云端配置下发，
        设备取 min(下发值, 设备硬顶 1800s)。stdin_content 恒不透传（§3.1）。
        """
        device_cfg = settings.skills.plugins.device_execution
        timeout_seconds = int(device_cfg.timeout_seconds)
        self.timeout_seconds = timeout_seconds
        result = await self.execute(
            skill=skill,
            version=str(version or ""),
            entry=entry,
            args=list(args),
            exec_hash=exec_hash,
            timeout_seconds=timeout_seconds,
            _trusted_tenant_id=tenant_id,
            _trusted_user_id=user_id,
            _session_id=session_id,
            _progress_queue=progress_queue,
        )
        return _proxy_result_to_execution_result(result, skill_name=skill)


def _proxy_result_to_execution_result(
    result: Dict[str, Any], skill_name: str
) -> ExecutionResult:
    """proxy 结果 dict → ExecutionResult（计划 §4.2 映射，供 skill_execute_tool 现有
    响应构造零改动消费）。

    成功：stdout 取 result.data.stdout（尾部保留截断，上限 max_stdout_chars）、
    stderr、exit_code、duration（duration_ms → 秒）；失败：stdout 置空、stderr=message
    （用户可读中文文案）、error=区分码；TIMEOUT → timed_out=True。
    """
    max_stdout = int(settings.skills.plugins.device_execution.max_stdout_chars)
    if result.get("success"):
        raw_data = result.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}
        try:
            exit_code = int(data.get("exit_code") or 0)
        except (TypeError, ValueError):
            exit_code = 0
        try:
            duration = float(data.get("duration_ms") or 0) / 1000.0
        except (TypeError, ValueError):
            duration = 0.0
        return ExecutionResult(
            success=True,
            stdout=_tail_keep(data.get("stdout") or "", max_stdout),
            stderr=str(data.get("stderr") or ""),
            exit_code=exit_code,
            duration=duration,
            timed_out=False,
            error=None,
        )
    code = str(result.get("code") or "FAILED")
    return ExecutionResult(
        success=False,
        stdout="",
        stderr=str(result.get("message") or "本机设备技能执行失败"),
        exit_code=-1,
        duration=0.0,
        timed_out=code == "TIMEOUT",
        error=code,
    )


async def dispatch_device_skill_script(
    *,
    skill: str,
    entry: str,
    args: Sequence[str],
    exec_hash: str,
    version: str = "",
    tenant_id: str,
    user_id: str,
    session_id: Optional[str] = None,
    context=None,
    progress_queue: Optional[asyncio.Queue] = None,
) -> ExecutionResult:
    """模块级便捷入口（executor 路由分支经延迟 import 调用——避免 src.core.skill_executor
    顶层依赖 src.local_tools 反向 import ExecutionResult 造成循环）。"""
    tool = SkillScriptRunDispatchTool()
    return await tool.dispatch(
        skill=skill, entry=entry, args=args, exec_hash=exec_hash, version=version,
        tenant_id=tenant_id, user_id=user_id, session_id=session_id,
        context=context, progress_queue=progress_queue,
    )
