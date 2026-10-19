"""
开放模块清单加载脚本（form-filling-api 技能）。

调用第三方系统的 open_modules 接口，返回当前 agent_token（代理人）在本应用下
开放给智能体填写的模块清单（模块名 / 中文名 / 主表），并按租户+子智能体做
Redis 缓存（TTL 读 FORM_FILLING_CACHE_TTL，默认 600s/10min），避免每轮拉取。

鉴权口径与业务接口一致：
- 仅带 Api-Authorize-Token（agent_token）时，返回 agent_token 配置的开放模块；
- 同时带 Client-Authorize-Token（client_token）时，与委托人角色可写模块求交集。
  client_token 从会话级托管缓存读取（delegate_login.py 写入），不进 LLM 上下文。

缓存按 scope 区分上述两种口径（delegate / agent），避免互相污染。

用法: echo '{"base_url": "https://..."}' | python scripts/load_open_modules.py
输入: stdin JSON
  {
    "base_url": "https://...",   # 必填，来自接口文档
    "force_refresh": false       # 可选，跳过缓存强制刷新
  }
输出: stdout JSON
  成功: {"success": true, "modules": [{"module", "display_name", "primary_table"}],
         "cached": bool, "_no_truncate": true}
  失败: {"success": false, "error": str, "hint": str}
"""

import json
import sys
import os
import urllib.request
import urllib.error

_DEFAULT_CACHE_TTL_SECONDS = 600  # 默认 10 分钟（.env 的 FORM_FILLING_CACHE_TTL 可覆盖）
_HTTP_TIMEOUT_SECONDS = 15


def _cache_ttl_seconds():
    """表单元数据缓存有效期（秒）。

    读 .env 的 FORM_FILLING_CACHE_TTL，缺省 600（10 分钟）。与
    load_form_schema.py 共用同一配置，保证两个元数据接口的缓存窗口一致。
    """
    raw = (os.environ.get("FORM_FILLING_CACHE_TTL") or "").strip()
    if raw:
        try:
            ttl = int(raw)
            if ttl > 0:
                return ttl
        except ValueError:
            pass
    return _DEFAULT_CACHE_TTL_SECONDS


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


def _read_session_client_token(tenant_id, subagent_id):
    """从会话级托管缓存读取 client_token（delegate_login.py 写入）；无则返回 None"""
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


def _read_cached_modules(tenant_id, subagent_id, scope):
    try:
        from src.core.cache_utils import CacheKeys, get_cached
        cached = get_cached(CacheKeys.OPEN_MODULES, tenant_id, subagent_id, scope)
        if isinstance(cached, dict) and "modules" in cached:
            return cached
    except Exception as e:
        print(json.dumps({"_cache_read_error": str(e)}, ensure_ascii=False),
              file=sys.stderr)
    return None


def _write_cached_modules(tenant_id, subagent_id, scope, payload):
    try:
        from src.core.cache_utils import CacheKeys, set_cached
        set_cached(
            CacheKeys.OPEN_MODULES, tenant_id, subagent_id, scope,
            value=payload, ttl=_cache_ttl_seconds(),
        )
    except Exception as e:
        print(json.dumps({"_cache_write_error": str(e)}, ensure_ascii=False),
              file=sys.stderr)


def _slim_modules(response):
    """精简模块清单，仅保留呈现所需字段"""
    modules = []
    for item in (response or {}).get("modules") or []:
        if not isinstance(item, dict) or not item.get("module"):
            continue
        modules.append({
            "module": item.get("module"),
            "display_name": item.get("display_name"),
            "primary_table": item.get("primary_table"),
        })
    return {"modules": modules}


def load_open_modules():
    data = _read_input()
    base_url = (data.get("base_url") or "").strip().rstrip("/")
    force_refresh = bool(data.get("force_refresh"))

    if not base_url.startswith("https://"):
        _emit({"success": False, "error": "base_url 必须是 https 地址（以接口文档为准）"})
        return

    tenant_id, subagent_id = _context_ids()
    if not tenant_id:
        _emit({"success": False, "error": "无法获取当前租户 ID"})
        return

    agent_token = os.environ.get("AGENT_TOKEN", "")
    if not agent_token:
        _emit({"success": False, "error": "缺少 AGENT_TOKEN 环境变量（代理人 token）",
               "hint": "请在租户环境变量中配置 AGENT_TOKEN 后重试"})
        return

    # 委托会话存在时带上 client_token，返回与委托人可写权限一致的交集
    client_token = _read_session_client_token(tenant_id, subagent_id)
    scope = "delegate" if client_token else "agent"

    # 1. 缓存命中直接返回
    if not force_refresh:
        cached = _read_cached_modules(tenant_id, subagent_id, scope)
        if cached:
            _emit({**cached, "success": True, "cached": True, "_no_truncate": True})
            return

    # 2. 调 open_modules 接口
    headers = {
        "Content-Type": "application/json",
        "Api-Authorize-Token": agent_token,
    }
    if client_token:
        headers["Client-Authorize-Token"] = client_token
    req = urllib.request.Request(
        f"{base_url}/api/v1/erp.module/open_modules",
        data=b"{}",
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _emit({"success": False, "error": f"open_modules 接口 HTTP {e.code}",
               "hint": "请稍后重试"})
        return
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        _emit({"success": False, "error": f"open_modules 接口调用失败: {e}",
               "hint": "核对 base_url 是否与接口文档一致"})
        return

    if not isinstance(body, dict) or body.get("Code") == -99:
        hint = ("请带 force_refresh 重新执行 delegate_login.py 后重试"
                if client_token else "核对 AGENT_TOKEN 是否为有效的代理人 token")
        _emit({"success": False, "error": "鉴权失效（Code=-99）", "hint": hint})
        return
    if body.get("Code") != 0:
        err = body.get("Error") or "未知业务错误"
        _emit({"success": False, "error": f"open_modules 接口业务错误: {err}",
               "hint": "联系系统方确认开放模块配置"})
        return

    # 3. 精简后写缓存并返回
    payload = _slim_modules(body.get("Response") or {})
    _write_cached_modules(tenant_id, subagent_id, scope, payload)
    _emit({**payload, "success": True, "cached": False, "_no_truncate": True})


if __name__ == "__main__":
    try:
        load_open_modules()
    except Exception as e:  # 兜底：任何未预期异常都以 JSON 失败返回，不抛栈
        _emit({"success": False, "error": f"open_modules 加载脚本异常: {e}"})
