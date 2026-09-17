"""BOSS 本地工具价目查询（计费唯一取价口，2026-09-01 计费时机迁移）

价目来源 settings.boss_tool_billing（configs/config.yaml 可覆盖）。计费点在
repository.write_result（结果落库同事务计费，docs/design/billing/
client-billing-integration-design.md §4.1），本模块只做取价，不碰 DB：

- tool_credit_price：repository.write_result 落 succeeded 终态时按价计费；
  proxy_tool 扣费前余额预检复用同一取价，保证「预检收费的工具」与「落库收费的工具」
  永远是同一份价目
- overlay_heal_price：弹层自愈专项费（无独立 invocation，仍在 proxy_tool 编排层直记）

注意：混合模式工具（boss_jobs_list / boss_interview_notify 覆写 execute 为纯云端逻辑，
不建 invocation）永远不会经过 write_result 计费，预检同样不触发——给它们配价是
无效配置，价目表只应包含走本机 invocation 链路的工具。
"""

from src.config.settings import settings


def tool_credit_price(tool_name: str) -> float:
    """工具单次积分价格：总开关关 → 0；未列入价目表 → default_credit_price（默认 0）"""
    cfg = settings.boss_tool_billing
    if not cfg.enabled:
        return 0.0
    try:
        return max(0.0, float(cfg.tool_credit_prices.get(tool_name, cfg.default_credit_price)))
    except (TypeError, ValueError):
        return 0.0


def overlay_heal_price() -> float:
    """弹层自愈专项费：自愈总开关关 → 0"""
    cfg = settings.boss_tool_billing
    if not cfg.overlay_heal_enabled:
        return 0.0
    try:
        return max(0.0, float(cfg.overlay_heal_price))
    except (TypeError, ValueError):
        return 0.0


def resume_recognition_price() -> float:
    """简历识别费单价（积分/份）：计费总开关关 → 0（截图本就免费，识别费一并停扣）。

    两个简历工具的预检覆写与本扣费取价同源，保证「预检收费的工具」与
    「实际落账的工具」永远是同一份价目（同 tool_credit_price 的约定）。
    """
    cfg = settings.boss_tool_billing
    if not cfg.enabled:
        return 0.0
    try:
        return max(0.0, float(cfg.resume_recognition_price))
    except (TypeError, ValueError):
        return 0.0


# 简历识别费在台账中的 tool_name（2026-09-17 去 OCR 化）：接口直记与工具层兜底路径同科目，
# 对账同源。放在 pricing（计费唯一取价口）避免 api 层反向依赖 proxy_tool 重模块
RESUME_RECOGNITION_TOOL_NAME = "boss_resume_recognition"
