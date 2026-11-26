"""
自动填表外部系统 API 配置加载脚本（form-filling-api 技能）。

读取当前租户、当前子智能体的接口配置文件（templates/{subagent}-api.md）并返回内容。
供 LLM 了解如何调用外部系统接口（Base URL、鉴权、module 清单、业务规则）。

用法: python scripts/load_api_config.py
输入: stdin JSON (可选，可包含 tenant_id)
输出: stdout JSON {"success": bool, "content": str, "configured": bool}
"""

import json
import sys
import os


def get_tenant_id():
    """获取当前租户 ID"""
    # 1. 从环境变量获取（skill_executor 注入）
    tenant_id = os.environ.get("AID_TENANT_ID") or os.environ.get("CURRENT_TENANT_ID")
    if tenant_id:
        return tenant_id

    # 2. 从 stdin 获取（skill_execute 通过 stdin 注入）
    try:
        raw = sys.stdin.read().strip()
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("tenant_id"):
                return data["tenant_id"]
    except (json.JSONDecodeError, EOFError):
        pass

    # 3. 从 ContextVar 获取
    try:
        from src.saas.context import get_current_tenant_id
        tid = get_current_tenant_id()
        if tid:
            return tid
    except Exception:
        pass

    return None


def load_api_config():
    """加载租户的 API 配置文件"""
    tenant_id = get_tenant_id()

    if not tenant_id:
        print(json.dumps({
            "success": False,
            "error": "无法获取当前租户 ID",
            "content": ""
        }, ensure_ascii=False))
        return

    # tenant_id 数据库带 `tenant_` 前缀，存储规范要求目录不带前缀，统一剥离
    if tenant_id.startswith("tenant_"):
        tenant_id = tenant_id[len("tenant_"):]
    # 文档按子智能体隔离：{subagent}-api.md（skill_executor 注入 AID_SUBAGENT_ID；
    # 无上下文的调试场景回退 auto-form-filler）
    subagent_id = os.environ.get("AID_SUBAGENT_ID") or "auto-form-filler"

    # 租户文档优先，未上传时按技能白名单兜底通用模板（configs/api_doc_templates/
    # form-filling-api.md），并把 ${APP_ID} 等非凭证占位符渲染为租户环境变量实际值
    # （凭证类 ${AGENT_TOKEN}/${client_token} 保留，由 http_api 工具请求时替换）。
    # 用带状态入口区分「租户未配置」与「配置读取失败」——后者是基础设施故障，
    # 不能与前者共用文案伪装成配置问题（2026-11-26 08e40836 回归教训）
    try:
        from src.services.tenant_api_doc import load_api_doc_with_status

        result = load_api_doc_with_status(tenant_id, subagent_id)
    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": f"读取接口文档失败: {e}",
            "content": ""
        }, ensure_ascii=False))
        return

    status = result.get("status")
    if status == "ok":
        print(json.dumps({
            "success": True,
            "content": result["content"],
            "configured": True,
            # API 说明文档是 LLM 调用外部系统接口的唯一依据，超长也须完整返回；
            # 声明 _no_truncate，agent 工具结果截断逻辑据此豁免
            "_no_truncate": True
        }, ensure_ascii=False))
        return

    if status == "db_unavailable":
        # 配置读取失败：系统数据库暂时不可访问，属基础设施故障，须提示重试
        print(json.dumps({
            "success": True,
            "content": "接口配置读取失败（系统数据库暂时不可访问，非租户配置问题）。"
                       "请告知用户稍后重试；若持续出现，请联系平台管理员检查系统状态。"
                       "不要尝试其他写入途径。",
            "configured": False,
            "reason": "db_unavailable",
            "error": result.get("error", "")
        }, ensure_ascii=False))
        return

    if status == "missing_env_vars":
        # 文档就绪但非凭证占位符（如 APP_ID）未渲染齐全：租户环境变量缺配置
        unresolved = ", ".join(result.get("unresolved") or [])
        print(json.dumps({
            "success": True,
            "content": f"外部系统接口文档已就绪，但租户环境变量未配置齐全（缺少: {unresolved}）。"
                       "请告知用户联系管理员在管理后台「数字员工授权 > 环境变量」中配置，"
                       "不要尝试其他写入途径。",
            "configured": False,
            "reason": "missing_env_vars"
        }, ensure_ascii=False))
        return

    # no_doc：租户未上传文档且技能白名单无匹配模板
    print(json.dumps({
        "success": True,
        "content": "未配置外部系统 API，无法执行表单写入。请告知用户联系管理员在管理后台"
                   "上传接口文档并配置环境变量，不要尝试其他写入途径。",
        "configured": False,
        "reason": "no_doc"
    }, ensure_ascii=False))


if __name__ == "__main__":
    load_api_config()
