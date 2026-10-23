"""租户 API 文档（{subagent_name}-api.md）api-meta 公共读取入口

供「外部系统入口（SSO 打开第三方系统）」等非推送场景使用。推送侧
（src/services/recap/tasks/external_push.py）有独立的 parse_api_meta
（带 login_url 必填校验 + tlog），二者共用同一 api-meta 块但校验口径不同，
这里刻意不 import 推送模块（其顶层 import src.tools.base，会拉起重依赖链）。

文档按子智能体隔离：storage/tenants/{tid}/templates/{subagent_name}-api.md
（与上传 API tenant_config_file.py 命名一致）。system_id 即子智能体名。

通用模板 fallback：租户未上传文档时，若该子智能体技能白名单包含某个技能
（如 pre-sales-api），且 configs/api_doc_templates/{skill}.md 存在，则使用
通用模板（应用号等租户差异用 ${APP_ID} 占位符，加载期由租户环境变量渲染，
见 render_doc_placeholders）。

api-meta 块格式（文档顶部围栏，YAML 风格 key: value 逐行）：
    ```api-meta
    login_url: https://...
    sso_enabled: true
    sso_mode: ticket_redirect
    ...
    ```
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

_DOC_SUFFIX = "-api.md"

_SSO_MODES = ("direct_url", "ticket_redirect", "token_param", "form_submit")
_ENABLED_VALUES = ("true", "1", "yes", "on")

# 通用 API 文档模板目录，按技能基础名命名（如 pre-sales-api.md）
_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "configs" / "api_doc_templates"

_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


def tenant_doc_path(tenant_id: str, subagent_name: str) -> Path:
    """租户接口文档路径：storage/tenants/{去前缀租户ID}/templates/{subagent_name}-api.md"""
    from src.core.storage import normalize_tenant_id

    return (
        Path(__file__).resolve().parents[2]
        / "storage" / "tenants" / normalize_tenant_id(tenant_id) / "templates" / f"{subagent_name}{_DOC_SUFFIX}"
    )


def list_doc_subagent_names(tenant_id: str) -> List[str]:
    """列出租户 templates 目录下全部接口文档对应的子智能体名（{name}-api.md）"""
    from src.core.storage import normalize_tenant_id

    templates_dir = (
        Path(__file__).resolve().parents[2]
        / "storage" / "tenants" / normalize_tenant_id(tenant_id) / "templates"
    )
    if not templates_dir.is_dir():
        return []
    names = []
    for path in sorted(templates_dir.glob(f"*{_DOC_SUFFIX}")):
        name = path.name[: -len(_DOC_SUFFIX)]
        if name:
            names.append(name)
    return names


def load_tenant_doc(tenant_id: str, subagent_name: str) -> Optional[str]:
    """读取租户接口文档全文；缺失返回 None"""
    path = tenant_doc_path(tenant_id, subagent_name)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[external_systems] 租户文档读取失败 tenant={tenant_id}, subagent={subagent_name}: {e}")
        return None


def extract_api_meta(doc_text: str) -> Dict[str, str]:
    """宽松版 api-meta 抽取：无块 / 缺 login_url 均返回空 dict（不校验业务键）"""
    meta: Dict[str, str] = {}
    match = re.search(r"```\s*api-meta[^\n]*\n(.*?)```", doc_text or "", re.DOTALL)
    if not match:
        return meta
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = value.strip().rstrip(",")
    return meta


def parse_sso_config(meta: Dict[str, str], subagent_name: str = "") -> Optional[Dict[str, Any]]:
    """从 api-meta 键值解析外部系统入口（SSO）配置

    返回 None：未声明 sso_enabled/sso_url（该文档不显示入口）。
    返回 dict：system_id 即子智能体名；sso_ready 表示入口是否可用
    （不可用时前端按钮禁用并提示）。未知 sso_mode 不猜——按 sso_ready=False 处理。
    """
    enabled = (meta.get("sso_enabled") or "").strip().lower() in _ENABLED_VALUES
    sso_url = (meta.get("sso_url") or "").strip()
    if not enabled or not sso_url:
        return None

    mode = (meta.get("sso_mode") or "direct_url").strip().lower()
    if mode not in _SSO_MODES:
        logger.warning(f"[external_systems] 未知 sso_mode={mode}，按不可用处理")
    ticket_param = (meta.get("sso_ticket_param") or "").strip()
    grant_url = (meta.get("sso_grant_url") or "").strip()
    login_sso_url = (meta.get("sso_login_url") or "").strip()
    fallback_url = (meta.get("sso_fallback_url") or "").strip() or sso_url

    if mode == "direct_url":
        sso_ready = True
    elif mode == "token_param":
        sso_ready = bool(ticket_param)
    elif mode == "ticket_redirect":
        sso_ready = bool(grant_url)
    else:
        sso_ready = False  # form_submit 预留 / 未知模式

    return {
        "system_id": subagent_name,
        "system_name": (meta.get("sso_system_name") or "").strip() or "第三方系统",
        "login_url": (meta.get("login_url") or "").strip(),
        "mode": mode,
        "sso_url": sso_url,
        "ticket_param": ticket_param,
        "grant_url": grant_url,
        "login_sso_url": login_sso_url,
        "fallback_url": fallback_url,
        "sso_ready": sso_ready,
    }


def load_sso_config(
    tenant_id: str,
    subagent_name: str,
    *,
    env_vars: Optional[Dict[str, str]] = None,
    allowed_skills: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """读取单个子智能体文档（租户文档或通用模板）并解析 SSO 配置的组合入口

    文档缺失 / 渲染后仍残留占位符（租户未配置 APP_ID 等环境变量）均返回 None
    （入口不显示）。env_vars / allowed_skills 可由调用方批量预取后传入，
    避免列表场景逐智能体重复查库。
    """
    doc = load_rendered_doc(
        tenant_id, subagent_name, env_vars=env_vars, allowed_skills=allowed_skills
    )
    if not doc:
        return None
    cfg = parse_sso_config(extract_api_meta(doc), subagent_name)
    if cfg is None:
        return None
    if "${" in (cfg.get("login_url") or "") or "${" in (cfg.get("sso_url") or ""):
        logger.warning(
            f"[external_systems] 接口文档占位符未解析（环境变量未配置），入口按未配置处理 "
            f"tenant={tenant_id}, subagent={subagent_name}"
        )
        return None
    return cfg


def _list_builtin_subagent_names() -> List[str]:
    """内置子智能体名（subagents/<name>/），用于发现走通用模板兜底的 SSO 入口"""
    try:
        from src.services.agent_runner.runtime.resource_cache import cached_builtin_subagent_registry

        builtins_dir = Path(__file__).resolve().parents[2] / "subagents"
        registry = cached_builtin_subagent_registry(builtins_dir)
        return [
            str(item.get("agent_id"))
            for item in registry.get_all_subagents_with_type()
            if item.get("agent_id")
        ]
    except Exception as e:
        logger.debug(f"[tenant_api_doc] 枚举内置子智能体失败: {e}")
        return []


def _list_custom_agent_ids() -> List[str]:
    """DB 自定义智能体 ID（subagent_definitions active），用于发现模板兜底的 SSO 入口"""
    try:
        from src.db.subagent_definition_db import SubagentDefinitionDB

        return [
            str(row.get("agent_id"))
            for row in SubagentDefinitionDB.list_active()
            if row.get("agent_id")
        ]
    except Exception as e:
        logger.debug(f"[tenant_api_doc] 枚举自定义智能体失败: {e}")
        return []


def _allowed_skills_map(subagent_names: List[str]) -> Dict[str, List[str]]:
    """批量读取技能白名单，语义同 _subagent_allowed_skills（DB 优先，内置兜底）

    一次 list_active 替代逐智能体 get_by_agent_id，避免入口列表 N+1 查询。
    """
    names = [name for name in subagent_names if name]
    db_skills: Dict[str, List[str]] = {}
    try:
        from src.db.subagent_definition_db import SubagentDefinitionDB

        for row in SubagentDefinitionDB.list_active():
            agent_id = row.get("agent_id")
            allowed = (row.get("skills") or {}).get("allowed") or []
            if agent_id and allowed:
                db_skills[str(agent_id)] = [str(s) for s in allowed]
    except Exception as e:
        logger.debug(f"[tenant_api_doc] 批量读取 DB 智能体技能白名单失败: {e}")

    builtin_skills: Dict[str, List[str]] = {}
    try:
        from src.services.agent_runner.runtime.resource_cache import cached_builtin_subagent_registry

        builtins_dir = Path(__file__).resolve().parents[2] / "subagents"
        registry = cached_builtin_subagent_registry(builtins_dir)
        for name in names:
            cfg = registry.get(name)
            if cfg:
                builtin_skills[name] = [str(s) for s in (cfg.get_allowed_skills() or [])]
    except Exception as e:
        logger.debug(f"[tenant_api_doc] 读取内置智能体技能白名单失败: {e}")

    return {name: (db_skills.get(name) or builtin_skills.get(name) or []) for name in names}


def list_sso_candidate_subagent_names(tenant_id: str) -> List[str]:
    """外部系统入口候选子智能体名：租户文档 + 内置 + 自定义（去重保序）

    租户未上传 {subagent}-api.md 时，内置/自定义智能体可经技能白名单命中
    configs/api_doc_templates/{skill}.md 通用模板（如 auto-form-filler 的
    form-filling-api），仅扫描租户文档会漏掉这类入口。
    """
    names: List[str] = []
    seen: Set[str] = set()
    for name in (
        list_doc_subagent_names(tenant_id)
        + _list_builtin_subagent_names()
        + _list_custom_agent_ids()
    ):
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def load_sso_configs(tenant_id: str) -> List[Dict[str, Any]]:
    """扫描候选智能体的接口文档，返回已声明 sso_* 的外部系统入口列表

    每份生效文档（租户上传或技能模板兜底）至多对应一个外部系统；未声明
    sso_enabled/sso_url 的文档（纯推送用途）自动跳过。多个智能体指向同一
    外部系统（同名同地址同模式）时只保留首个，避免页面出现重复卡片。
    """
    names = list_sso_candidate_subagent_names(tenant_id)
    if not names:
        return []
    # 批量预取技能白名单与租户环境变量，避免逐智能体重复查库（页面加载 N+1）
    skills_map = _allowed_skills_map(names)
    env_map = load_tenant_env_vars_map(tenant_id)

    configs_by_key: Dict[tuple, Dict[str, Any]] = {}
    for name in names:
        cfg = load_sso_config(
            tenant_id,
            name,
            env_vars=env_map.get(name, env_map.get("*", {})),
            allowed_skills=skills_map.get(name, []),
        )
        if cfg is None:
            continue
        key = (cfg.get("system_name"), cfg.get("mode"), cfg.get("sso_url"))
        prev = configs_by_key.get(key)
        # 同一系统多份声明时保留首个；若首个未就绪、后者就绪，则替换（dict 保序）
        if prev is None or (not prev.get("sso_ready") and cfg.get("sso_ready")):
            configs_by_key[key] = cfg
    return list(configs_by_key.values())


# ============== 通用模板 fallback 与 ${VAR} 加载期渲染 ==============


def _subagent_allowed_skills(subagent_name: str) -> List[str]:
    """子智能体技能白名单（基础技能名，与 SKILL.md name / agent.py 过滤口径一致）

    DB 自定义智能体（subagent_definitions.skills.allowed）优先，内置智能体
    （SUBAGENT.md，经 subagent_registry）兜底。两处读取失败均降级为空列表。
    """
    if not subagent_name:
        return []
    try:
        from src.db.subagent_definition_db import SubagentDefinitionDB

        row = SubagentDefinitionDB.get_by_agent_id(subagent_name)
        if row:
            allowed = (row.get("skills") or {}).get("allowed") or []
            if allowed:
                return [str(s) for s in allowed]
    except Exception as e:
        logger.debug(f"[tenant_api_doc] 读取 DB 智能体技能白名单失败 agent={subagent_name}: {e}")
    try:
        # 用 cached_builtin_subagent_registry（带目录构造 + 进程级缓存），不要用
        # src.subagents.registry.subagent_registry 全局单例——后者无参构造不加载，
        # 在 skill 脚本子进程（create_subprocess_shell 起的独立解释器）里恒为空，
        # 导致内置子智能体（如 auto-form-filler）的技能白名单读不到、兜底模板失效
        from src.services.agent_runner.runtime.resource_cache import cached_builtin_subagent_registry

        builtins_dir = Path(__file__).resolve().parents[2] / "subagents"
        cfg = cached_builtin_subagent_registry(builtins_dir).get(subagent_name)
        if cfg:
            return [str(s) for s in (cfg.get_allowed_skills() or [])]
    except Exception as e:
        logger.debug(f"[tenant_api_doc] 读取内置智能体技能白名单失败 agent={subagent_name}: {e}")
    return []


def resolve_doc_text(
    tenant_id: str,
    subagent_name: str,
    *,
    allowed_skills: Optional[List[str]] = None,
) -> Optional[str]:
    """解析子智能体生效的接口文档文本：租户文档优先，技能模板兜底

    判定条件是技能而非固定子智能体名：技能白名单包含某技能（如 pre-sales-api）
    且 configs/api_doc_templates/{skill}.md 存在即可用通用模板，自定义数字员工
    （如 sales-assistant）同样适用。allowed_skills 传入时不再逐智能体查库。
    副本实例先解析为父定义名（文档与技能白名单均按父定义读取）。
    """
    subagent_name = resolve_base_agent_id(tenant_id, subagent_name)
    tenant_doc = load_tenant_doc(tenant_id, subagent_name)
    if tenant_doc is not None:
        return tenant_doc
    skills = allowed_skills if allowed_skills is not None else _subagent_allowed_skills(subagent_name)
    for skill in skills:
        template_path = _TEMPLATE_DIR / f"{skill}.md"
        if template_path.is_file():
            try:
                return template_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"[tenant_api_doc] 模板文档读取失败 skill={skill}: {e}")
    return None


def db_tenant_id(tenant_id: str) -> str:
    """把租户 ID 规范化到 DB 口径（`subagent_env_vars` 等表存带 `tenant_` 前缀的 ID）

    调用方口径不一：skill 脚本（load_api_config.py）为存储路径剥离了 `tenant_`
    前缀后透传，external_push 则直接传带前缀的 payload.tenant_id。查环境变量前
    统一补齐前缀，否则无前缀 ID 查不到配置、模板 ${APP_ID} 等占位符不渲染。
    以 `_` 开头的匿名占位（如 `_anonymous`）不是真实租户，原样返回。
    """
    if tenant_id and not tenant_id.startswith(("tenant_", "_")):
        return f"tenant_{tenant_id}"
    return tenant_id


def resolve_base_agent_id(tenant_id: str, subagent_name: str) -> str:
    """副本实例名解析为父定义名（tenant_agent_instances.base_agent_id）

    副本实例不进 subagent_definitions / 内置 registry，且前端无副本级接口文档
    与环境变量配置入口——接口文档、技能白名单、环境变量一律按父定义读取。
    直用实例行（agent_id = base_agent_id）、非实例名、查询失败均原样返回。
    """
    if not subagent_name or not tenant_id:
        return subagent_name
    try:
        from src.db.tenant_agent_instance_db import TenantAgentInstanceDB

        row = TenantAgentInstanceDB.get(db_tenant_id(tenant_id), subagent_name)
        base = (row or {}).get("base_agent_id")
        if row and base and row.get("agent_id") != base:
            return str(base)
    except Exception as e:
        logger.debug(f"[tenant_api_doc] 解析副本实例父定义失败 tenant={tenant_id}, agent={subagent_name}: {e}")
    return subagent_name


def load_tenant_env_vars(tenant_id: str, subagent_name: str = "") -> Dict[str, str]:
    """租户环境变量：子智能体精确 -> 租户任意智能体兜底（忽略空值）"""
    db_tid = db_tenant_id(tenant_id)
    base_name = resolve_base_agent_id(tenant_id, subagent_name) if subagent_name else ""
    env_vars: Dict[str, str] = {}
    try:
        from src.db.subagent_env_var import SubagentEnvVarDB

        if base_name:
            for var in SubagentEnvVarDB.get_vars(db_tid, base_name):
                if var.get("var_name") and var.get("var_value"):
                    env_vars[var["var_name"]] = var["var_value"]
        for var in SubagentEnvVarDB.get_all_vars_for_tenant(db_tid):
            name = var.get("var_name")
            if name and var.get("var_value") and name not in env_vars:
                env_vars[name] = var["var_value"]
    except Exception as e:
        logger.warning(f"[tenant_api_doc] 读取租户环境变量失败 tenant={tenant_id}: {e}")
    return env_vars


def load_tenant_env_vars_map(tenant_id: str) -> Dict[str, Dict[str, str]]:
    """一次查询租户全部环境变量，按子智能体分组（结果含 `*` 键为租户级兜底）

    语义与 load_tenant_env_vars 一致：同一变量子智能体精确配置优先，缺失的用
    租户任意智能体配置兜底。列表场景批量预取，避免逐智能体 N+1 查询。
    """
    grouped: Dict[str, Dict[str, str]] = {}
    tenant_wide: Dict[str, str] = {}
    try:
        from src.db.subagent_env_var import SubagentEnvVarDB

        for var in SubagentEnvVarDB.get_all_vars_for_tenant(db_tenant_id(tenant_id)):
            name = var.get("var_name")
            value = var.get("var_value")
            if not name or not value:
                continue
            agent = var.get("subagent_name") or ""
            if agent:
                grouped.setdefault(agent, {})[name] = value
            tenant_wide.setdefault(name, value)
    except Exception as e:
        logger.warning(f"[tenant_api_doc] 读取租户环境变量失败 tenant={tenant_id}: {e}")
    for agent, exact in grouped.items():
        merged = dict(tenant_wide)
        merged.update(exact)
        grouped[agent] = merged
    grouped["*"] = tenant_wide
    return grouped


def render_doc_placeholders(
    doc_text: str,
    tenant_id: str,
    subagent_name: str,
    *,
    env_vars: Optional[Dict[str, str]] = None,
) -> str:
    """文档加载期渲染 ${VAR}（如 ${APP_ID}），租户环境变量 -> 进程环境变量兜底

    凭证类占位符刻意**不**渲染：AGENT_TOKEN 与 api-meta user_token_name 声明的
    用户身份 token（如 client_token）若在加载期替换，明文凭证会随文档注入 LLM
    上下文造成泄漏；它们由 http_api 工具在每次请求时经 ToolExecutionContext
    替换。未解析的非凭证占位符保留原样并 warning，调用方用
    find_unresolved_placeholders 判定是否配置齐全。
    """
    if not doc_text or "${" not in doc_text:
        return doc_text

    # 租户级环境变量（subagent_env_vars）：子智能体精确 -> 租户任意智能体兜底。
    # recap 等后台任务无请求上下文（env_vars 不注入 os.environ），必须按 tenant 显式查库
    if env_vars is None:
        env_vars = load_tenant_env_vars(tenant_id, subagent_name)

    excluded: Set[str] = {"AGENT_TOKEN"}
    user_token_name = (extract_api_meta(doc_text).get("user_token_name") or "").strip()
    if user_token_name:
        excluded.add(user_token_name)

    unresolved: Set[str] = set()

    def _replace(match: re.Match) -> str:
        var_name = match.group(1).strip()
        if var_name in excluded:
            return match.group(0)
        value = env_vars.get(var_name)
        if value is None:
            value = os.environ.get(var_name)
        if value is None:
            unresolved.add(var_name)
            return match.group(0)
        return value

    rendered = _PLACEHOLDER_RE.sub(_replace, doc_text)
    if unresolved:
        logger.warning(
            f"[tenant_api_doc] 租户环境变量未配置，占位符保留原样 tenant={tenant_id}, "
            f"subagent={subagent_name}, vars={sorted(unresolved)}"
        )
    return rendered


def load_rendered_doc(
    tenant_id: str,
    subagent_name: str,
    *,
    env_vars: Optional[Dict[str, str]] = None,
    allowed_skills: Optional[List[str]] = None,
) -> Optional[str]:
    """解析 + 渲染的组合入口：resolve_doc_text -> render_doc_placeholders"""
    doc = resolve_doc_text(tenant_id, subagent_name, allowed_skills=allowed_skills)
    if doc is None:
        return None
    return render_doc_placeholders(doc, tenant_id, subagent_name, env_vars=env_vars)


def find_unresolved_placeholders(doc_text: str) -> List[str]:
    """渲染后文档中仍未解析的非凭证占位符变量名（凭证类本就不该渲染，不算未解析）

    凭证类 = AGENT_TOKEN + api-meta user_token_name 声明的用户身份 token。
    返回非空即视为租户环境变量未配置齐全（如缺 APP_ID）。
    """
    excluded: Set[str] = {"AGENT_TOKEN"}
    user_token_name = (extract_api_meta(doc_text).get("user_token_name") or "").strip()
    if user_token_name:
        excluded.add(user_token_name)
    return sorted({m.group(1).strip() for m in _PLACEHOLDER_RE.finditer(doc_text or "")} - excluded)
