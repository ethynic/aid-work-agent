"""租户 API 文档（{subagent_name}-api.md）api-meta 公共读取入口

供「外部系统入口（SSO 打开第三方系统）」等非推送场景使用。推送侧
（src/services/recap/tasks/external_push.py）有独立的 parse_api_meta
（带 login_url 必填校验 + tlog），二者共用同一 api-meta 块但校验口径不同，
这里刻意不 import 推送模块（其顶层 import src.tools.base，会拉起重依赖链）。

文档按子智能体隔离：storage/tenants/{tid}/templates/{subagent_name}-api.md
（与上传 API tenant_config_file.py 命名一致）。system_id 即子智能体名。

api-meta 块格式（文档顶部围栏，YAML 风格 key: value 逐行）：
    ```api-meta
    login_url: https://...
    sso_enabled: true
    sso_mode: ticket_redirect
    ...
    ```
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

_DOC_SUFFIX = "-api.md"

_SSO_MODES = ("direct_url", "ticket_redirect", "token_param", "form_submit")
_ENABLED_VALUES = ("true", "1", "yes", "on")


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


def load_sso_config(tenant_id: str, subagent_name: str) -> Optional[Dict[str, Any]]:
    """读取单个子智能体文档并解析 SSO 配置的组合入口（文档缺失返回 None）"""
    doc = load_tenant_doc(tenant_id, subagent_name)
    if not doc:
        return None
    return parse_sso_config(extract_api_meta(doc), subagent_name)


def load_sso_configs(tenant_id: str) -> List[Dict[str, Any]]:
    """扫描租户全部接口文档，返回已声明 sso_* 的外部系统入口列表

    每份 {subagent_name}-api.md 至多对应一个外部系统；未声明 sso_enabled/sso_url
    的文档（纯推送用途）自动跳过。
    """
    configs: List[Dict[str, Any]] = []
    for name in list_doc_subagent_names(tenant_id):
        doc = load_tenant_doc(tenant_id, name)
        if not doc:
            continue
        cfg = parse_sso_config(extract_api_meta(doc), name)
        if cfg is not None:
            configs.append(cfg)
    return configs
