"""
外部系统委托登录脚本（pre-sales-api 技能）。

带 Redis 缓存的委托登录：优先返回缓存的 client_token，未命中（或 force_refresh）
时调用登录接口获取并写缓存。避免每轮问答都执行「登录 -> 业务接口 -> 登出」全套，
token 由缓存 TTL 自然过期，无需主动登出。

适用「委托登录」类外部系统（如 10605 双 Token 模式）。登录地址等租户差异信息
由接口文档提供，脚本不硬编码。

用法: python scripts/delegate_login.py
输入: stdin JSON
  {
    "login_url": "https://xxx/api/v1/erp.delegate/login",  # 必填，来自接口文档
    "mobile": "归属员工手机号",                              # 必填，委托人手机号
    "name": "归属员工姓名",                                  # 可选，手机号不存在触发自动建号时使用
    "force_refresh": false                                   # 可选，Code=-99 时强制重新登录
  }
输出: stdout JSON
  成功: {"success": true, "client_token": str, "cached": bool, "record_id": int,
         "display_name": str, "agent_name": str, "created": bool}
  失败: {"success": false, "error": str, "hint": str}
"""

import json
import sys
import os
import urllib.request
import urllib.error

# 缓存 TTL：外部系统 token 有效期 1 天，缓存 23h 留 1h buffer 避开临界点
_CACHE_TTL_SECONDS = 23 * 3600
_HTTP_TIMEOUT_SECONDS = 15


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False))


def get_tenant_id():
    """获取当前租户 ID（与 load_api_config.py 的取值链路一致）"""
    tenant_id = os.environ.get("CURRENT_TENANT_ID")
    if tenant_id:
        return tenant_id

    try:
        from src.saas.context import get_current_tenant_id
        tid = get_current_tenant_id()
        if tid:
            return tid
    except Exception:
        pass

    return None


def _read_input():
    """读取 stdin JSON 入参"""
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


def get_subagent_id():
    """获取当前子智能体 ID（参与缓存键，隔离不同智能体对接的外部系统）"""
    return os.environ.get("AID_SUBAGENT_ID") or "pre-sales"


def _read_cache(tenant_id, subagent_id, mobile, login_url):
    """读缓存的 client_token；未命中 / login_url 不一致（跨系统串号防御）返回 None"""
    try:
        from src.core.cache_utils import CacheKeys, get_cached

        cached = get_cached(CacheKeys.EXTERNAL_LOGIN_TOKEN, tenant_id, subagent_id, mobile)
        if isinstance(cached, dict) and cached.get("client_token") \
                and cached.get("login_url") == login_url:
            return cached
    except Exception as e:
        # Redis 不可用等异常不阻断流程，降级为直接登录
        print(json.dumps({"_cache_read_error": str(e)}, ensure_ascii=False),
              file=sys.stderr)
    return None


def _write_cache(tenant_id, subagent_id, mobile, token_payload):
    """写缓存的 client_token，失败不阻断流程"""
    try:
        from src.core.cache_utils import CacheKeys, set_cached

        set_cached(
            CacheKeys.EXTERNAL_LOGIN_TOKEN, tenant_id, subagent_id, mobile,
            value=token_payload, ttl=_CACHE_TTL_SECONDS,
        )
    except Exception as e:
        print(json.dumps({"_cache_write_error": str(e)}, ensure_ascii=False),
              file=sys.stderr)


def _call_login_api(login_url, mobile, name=None):
    """调用委托登录接口，返回 (ok, data_or_error)"""
    agent_token = os.environ.get("AGENT_TOKEN", "")
    if not agent_token:
        return False, {
            "error": "缺少 AGENT_TOKEN 环境变量（代理人 token）",
            "hint": "请在租户环境变量中配置 AGENT_TOKEN 后重试",
        }

    login_payload = {"mobile": mobile}
    # name 仅在手机号不存在触发自动建号时被外部系统使用，空值不传以免覆盖兜底命名
    if name:
        login_payload["name"] = name
    payload = json.dumps(login_payload).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Api-Authorize-Token": agent_token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, {"error": f"登录接口 HTTP {e.code}", "hint": "请稍后重试"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return False, {"error": f"登录接口调用失败: {e}", "hint": "请稍后重试"}

    # 成功判定约定：本脚本适用于「Code=0 成功」的委托登录接口（如 10605），
    # 登录地址由接口文档提供；若外部系统响应结构不同，需按其文档调整此判定
    if not isinstance(body, dict) or body.get("Code") != 0:
        err = (body or {}).get("Error") or "未知业务错误"
        return False, {
            "error": f"委托登录业务错误: {err}",
            "hint": "核对归属员工手机号是否已在外部系统用户表中存在；"
                    "mobile 必须取归属员工手机号，不可用客户手机号",
        }

    response = body.get("Response") or {}
    return True, response


def delegate_login():
    data = _read_input()
    login_url = (data.get("login_url") or "").strip()
    mobile = (data.get("mobile") or "").strip()
    name = (data.get("name") or "").strip()
    force_refresh = bool(data.get("force_refresh"))

    if not login_url:
        _emit({"success": False, "error": "缺少 login_url 参数",
               "hint": "登录接口地址以接口文档为准，请按文档传入"})
        return
    if not login_url.startswith("https://"):
        _emit({"success": False, "error": "login_url 必须是 https 地址"})
        return
    if not mobile:
        _emit({"success": False,
               "error": "缺少 mobile 参数（归属员工手机号）",
               "hint": "mobile 取 record_lead_capture 成功结果中的 assignee_phone，"
                       "或 get_channel_user_info 返回的 assignee_phone，"
                       "切勿使用客户手机号或留空"})
        return

    tenant_id = get_tenant_id()
    if not tenant_id:
        _emit({"success": False, "error": "无法获取当前租户 ID"})
        return

    # 1. 缓存命中直接返回（force_refresh 时跳过；login_url 不一致视为未命中）
    subagent_id = get_subagent_id()
    if not force_refresh:
        cached = _read_cache(tenant_id, subagent_id, mobile, login_url)
        if cached:
            _emit({**cached, "success": True, "cached": True})
            return

    # 2. 调登录接口
    ok, result = _call_login_api(login_url, mobile, name)
    if not ok:
        _emit({"success": False, **result})
        return

    # 3. 写缓存并返回（value 带 login_url，读取时校验防跨系统串号）
    token_payload = {
        "client_token": result.get("client_token", ""),
        "record_id": result.get("record_id"),
        "display_name": result.get("display_name", ""),
        "agent_name": result.get("agent_name", ""),
        "created": bool(result.get("created")),
        "login_url": login_url,
    }
    if token_payload["client_token"]:
        _write_cache(tenant_id, subagent_id, mobile, token_payload)
    _emit({**token_payload, "success": True, "cached": False})


if __name__ == "__main__":
    try:
        delegate_login()
    except Exception as e:  # 兜底：任何未预期异常都以 JSON 失败返回，不抛栈
        _emit({"success": False, "error": f"委托登录脚本异常: {e}"})
