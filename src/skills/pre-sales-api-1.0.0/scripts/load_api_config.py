"""
售前咨询外部系统 API 配置加载脚本。

读取当前租户、当前子智能体的接口配置文件（templates/{subagent}-api.md）并返回内容。
供 LLM 了解如何调用外部售前咨询/客户管理系统的接口。

用法: python scripts/load_api_config.py
输入: stdin JSON (可选，可包含 tenant_id)
输出: stdout JSON {"success": bool, "content": str}
"""

import json
import sys
import os


def get_tenant_id():
    """获取当前租户 ID"""
    # 1. 从环境变量获取
    tenant_id = os.environ.get("CURRENT_TENANT_ID")
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

    # tenant_id 数据库带 `tenant_` 前缀，存储规范要求目录不带前缀，统一剥离（与 src.core.storage.normalize_tenant_id 一致）
    if tenant_id.startswith("tenant_"):
        tenant_id = tenant_id[len("tenant_"):]
    # 文档按子智能体隔离：{subagent}-api.md（skill_executor 注入 AID_SUBAGENT_ID；
    # 无上下文的调试场景回退 pre-sales 保持旧行为）
    subagent_id = os.environ.get("AID_SUBAGENT_ID") or "pre-sales"

    # 租户文档优先，未上传时按技能白名单兜底通用模板（configs/api_doc_templates/
    # pre-sales-api.md），并把 ${APP_ID} 等非凭证占位符渲染为租户环境变量实际值
    # （凭证类 ${AGENT_TOKEN}/${client_token} 保留，由 http_api 工具请求时替换）
    try:
        from src.services.tenant_api_doc import find_unresolved_placeholders, load_rendered_doc

        content = load_rendered_doc(tenant_id, subagent_id)
    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": f"读取接口文档失败: {e}",
            "content": ""
        }, ensure_ascii=False))
        return

    # 文档缺失，或模板渲染后仍残留非凭证占位符（租户环境变量如 APP_ID 未配置），视为未配置
    if not content or find_unresolved_placeholders(content):
        print(json.dumps({
            "success": True,
            "content": "未配置外部系统 API。请仅使用 record_lead_capture 工具完成本地留资记录，无需推送外部系统。",
            "configured": False
        }, ensure_ascii=False))
        return

    print(json.dumps({
        "success": True,
        "content": content,
        "configured": True,
        # API 说明文档是 LLM 调用外部系统接口的唯一依据，超长也须完整返回；
        # 声明 _no_truncate，agent 工具结果截断逻辑据此豁免（截断会导致 LLM 无法完成获取）。
        "_no_truncate": True
    }, ensure_ascii=False))


if __name__ == "__main__":
    load_api_config()
