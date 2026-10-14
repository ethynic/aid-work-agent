"""
表单 schema 元数据加载脚本（form-filling-api 技能）。

调用第三方系统的 module_schema 元数据接口，返回指定模块的字段元数据
（英文名/中文名/控件类型/必填/枚举/填写说明），并按租户+子智能体+module
做 Redis 缓存（TTL 1h），避免每轮拉取。

client_token 从会话级托管缓存读取（delegate_login.py 写入），不进 LLM 上下文。

用法: echo '{"module": "kehuxinxi", "base_url": "https://..."}' | python scripts/load_form_schema.py
输入: stdin JSON
  {
    "module": "kehuxinxi",        # 必填，业务模块名
    "base_url": "https://...",    # 必填，来自接口文档
    "force_refresh": false        # 可选，跳过缓存强制刷新
  }
输出: stdout JSON
  成功: {"success": true, "module": str, "display_name": str, "tables": [...],
         "cached": bool, "_no_truncate": true}
  失败: {"success": false, "error": str, "hint": str}
"""

import json
import sys
import os
import urllib.request
import urllib.error

_CACHE_TTL_SECONDS = 3600  # 元数据变更频率低，缓存 1h
_HTTP_TIMEOUT_SECONDS = 15


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False))


def _read_input():
    try:
        raw = sys.stdin.read().strip()
    except Exception:
        raw = ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _context_ids():
    """读取 skill_executor 注入的租户/子智能体标识"""
    tenant_id = (os.environ.get("AID_TENANT_ID")
                 or os.environ.get("CURRENT_TENANT_ID") or "").strip()
    if tenant_id.startswith("tenant_"):
        tenant_id = tenant_id[len("tenant_"):]
    subagent_id = (os.environ.get("AID_SUBAGENT_ID") or "auto-form-filler").strip()
    return tenant_id, subagent_id


def _read_cached_schema(tenant_id, subagent_id, module):
    try:
        from src.core.cache_utils import CacheKeys, get_cached
        cached = get_cached(CacheKeys.FORM_SCHEMA, tenant_id, subagent_id, module)
        if isinstance(cached, dict) and cached.get("tables"):
            return cached
    except Exception as e:
        print(json.dumps({"_cache_read_error": str(e)}, ensure_ascii=False),
              file=sys.stderr)
    return None


def _write_cached_schema(tenant_id, subagent_id, module, schema_payload):
    try:
        from src.core.cache_utils import CacheKeys, set_cached
        set_cached(
            CacheKeys.FORM_SCHEMA, tenant_id, subagent_id, module,
            value=schema_payload, ttl=_CACHE_TTL_SECONDS,
        )
    except Exception as e:
        print(json.dumps({"_cache_write_error": str(e)}, ensure_ascii=False),
              file=sys.stderr)


def _read_session_client_token(tenant_id, subagent_id):
    """从会话级托管缓存读取 client_token（delegate_login.py 写入）"""
    session_id = (os.environ.get("AID_SESSION_ID") or "").strip()
    if not session_id:
        return None
    try:
        from src.core.cache_utils import CacheKeys, get_cached
        cached = get_cached(
            CacheKeys.EXTERNAL_LOGIN_TOKEN_SESSION,
            tenant_id, subagent_id, session_id,
        )
        if isinstance(cached, dict) and cached.get("client_token"):
            return cached["client_token"]
    except Exception:
        pass
    return None


_ATTR_KEYS = ("attr_name", "display_name", "component", "required", "options",
              "description", "create_hidden", "edit_hidden", "is_system")


def _slim_attrs(attrs):
    """精简字段元数据，去除接口实现细节键，降低上下文占用"""
    slimmed = []
    for attr in attrs or []:
        if not isinstance(attr, dict):
            continue
        slim = {k: attr[k] for k in _ATTR_KEYS if k in attr and attr[k] not in (None, "")}
        if slim.get("attr_name"):
            slimmed.append(slim)
    return slimmed


def _slim_schema(response):
    """精简 schema 响应：保留主/子表角色与精简 attrs"""
    tables = []
    for table in (response or {}).get("tables") or []:
        if not isinstance(table, dict):
            continue
        tables.append({
            "table_name": table.get("table_name"),
            "display_name": table.get("display_name"),
            "role": table.get("role") or "main",
            "foreign_key": table.get("foreign_key"),
            "attrs": _slim_attrs(table.get("attrs")),
        })
    return {
        "module": response.get("module"),
        "display_name": response.get("display_name"),
        "primary_key": response.get("primary_key"),
        "tables": tables,
    }


def load_form_schema():
    data = _read_input()
    module = (data.get("module") or "").strip()
    base_url = (data.get("base_url") or "").strip().rstrip("/")
    force_refresh = bool(data.get("force_refresh"))

    if not module:
        _emit({"success": False, "error": "缺少 module 参数",
               "hint": "module 为主表表名去 t_ 前缀（如 kehuxinxi），以接口文档模块清单为准"})
        return
    if not base_url.startswith("https://"):
        _emit({"success": False, "error": "base_url 必须是 https 地址（以接口文档为准）"})
        return

    tenant_id, subagent_id = _context_ids()
    if not tenant_id:
        _emit({"success": False, "error": "无法获取当前租户 ID"})
        return

    # 1. 缓存命中直接返回
    if not force_refresh:
        cached = _read_cached_schema(tenant_id, subagent_id, module)
        if cached:
            _emit({**cached, "success": True, "cached": True, "_no_truncate": True})
            return

    # 2. 双 Token 组装（client_token 来自会话级托管缓存）
    agent_token = os.environ.get("AGENT_TOKEN", "")
    if not agent_token:
        _emit({"success": False, "error": "缺少 AGENT_TOKEN 环境变量（代理人 token）",
               "hint": "请在租户环境变量中配置 AGENT_TOKEN 后重试"})
        return
    client_token = _read_session_client_token(tenant_id, subagent_id)
    if not client_token:
        _emit({"success": False, "error": "委托会话不存在或已过期",
               "hint": "请先执行 delegate_login.py 完成委托登录，再拉取表单 schema"})
        return

    # 3. 调 schema 接口
    req = urllib.request.Request(
        f"{base_url}/api/v1/erp.module/module_schema",
        data=json.dumps({"module": module}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Api-Authorize-Token": agent_token,
            "Client-Authorize-Token": client_token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _emit({"success": False, "error": f"schema 接口 HTTP {e.code}",
               "hint": "请稍后重试"})
        return
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        _emit({"success": False, "error": f"schema 接口调用失败: {e}",
               "hint": "核对 base_url 是否与接口文档一致"})
        return

    if not isinstance(body, dict) or body.get("Code") == -99:
        _emit({"success": False, "error": "委托会话已失效（Code=-99）",
               "hint": "请带 force_refresh 重新执行 delegate_login.py 后重试"})
        return
    if not isinstance(body, dict) or body.get("Code") != 0:
        err = (body or {}).get("Error") or "未知业务错误"
        _emit({"success": False, "error": f"schema 接口业务错误: {err}",
               "hint": "核对 module 名是否在接口文档模块清单中"})
        return

    # 4. 精简后写缓存并返回
    schema_payload = _slim_schema(body.get("Response") or {})
    if not schema_payload.get("tables"):
        _emit({"success": False, "error": f"模块 {module} 未返回字段元数据",
               "hint": "联系系统方确认该模块已配置"})
        return
    _write_cached_schema(tenant_id, subagent_id, module, schema_payload)
    _emit({**schema_payload, "success": True, "cached": False, "_no_truncate": True})


if __name__ == "__main__":
    try:
        load_form_schema()
    except Exception as e:  # 兜底：任何未预期异常都以 JSON 失败返回，不抛栈
        _emit({"success": False, "error": f"schema 加载脚本异常: {e}"})
