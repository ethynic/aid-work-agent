"""
售后服务外部系统 API 配置加载脚本。

读取当前租户的 after-sales-api.md 配置文件并返回内容。
供 LLM 了解如何调用外部售后系统的接口。

用法: python scripts/load_api_config.py
输入: stdin JSON (可选，可包含 tenant_id)
输出: stdout JSON {"success": bool, "content": str}
"""

import json
import sys
import os

def get_tenant_id():
    """获取当前租户 ID"""
    # 1. 从 stdin 输入获取
    try:
        raw = sys.stdin.read().strip()
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("tenant_id"):
                return data["tenant_id"]
    except (json.JSONDecodeError, EOFError):
        pass

    # 2. 从环境变量获取
    tenant_id = os.environ.get("CURRENT_TENANT_ID")
    if tenant_id:
        return tenant_id

    # 3. 从 ContextVar 获取（需要导入 Python 模块）
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

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))),
        "storage", "tenants", tenant_id, "after-sales-api.md"
    )

    if not os.path.exists(config_path):
        print(json.dumps({
            "success": True,
            "content": "未配置外部系统 API。请使用 after_sales_action 工具进行内部工单操作。",
            "configured": False
        }, ensure_ascii=False))
        return

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    print(json.dumps({
        "success": True,
        "content": content,
        "configured": True
    }, ensure_ascii=False))


if __name__ == "__main__":
    load_api_config()
