"""
LLM 摘要生成器

使用 deepseek-v4-flash 小模型生成日报/周报/月报摘要。
- 通过 LLMProviderConfig.get_report_model() 获取专用模型，未配置时 fallback 到主模型
- 调用 LLM Gateway 的 chat() 接口，通过 kwargs 传 model 覆盖默认模型
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from src.config.settings import create_settings
from src.llm.gateway import llm_gateway


def get_report_model() -> str:
    """获取报告专用模型名

    优先级：当前 provider 的 report_model > 当前 provider 的 model
    """
    settings = create_settings()
    provider_name = settings.llm.provider
    provider_cfg = getattr(settings.llm, provider_name, None)
    if provider_cfg is None:
        return ""
    return provider_cfg.get_report_model()


async def summarize_personal(
    user_name: str,
    department: Optional[str],
    report_date_str: str,
    records: List[Dict[str, Any]],
    report_type: str = "daily",
) -> Tuple[str, Dict[str, int]]:
    """生成个人报告摘要

    Args:
        user_name: 用户姓名
        department: 部门（可空）
        report_date_str: 报告日期字符串（如 "2026-07-22"）
        records: chat_records 列表，每条含 user_message / assistant_message / execution_details
        report_type: daily / weekly / monthly

    Returns:
        (摘要正文, usage_dict)
        usage_dict: {"prompt_tokens": int, "completion_tokens": int}
    """
    report_model = get_report_model()
    type_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(report_type, "报告")

    # 构造对话摘要输入：每条对话的 user_message（截断到 200 字）+ 关键工具调用
    dialog_summaries: List[str] = []
    for i, rec in enumerate(records[:50], start=1):  # 上限 50 条
        user_msg = (rec.get("user_message") or "").strip()[:200]
        if not user_msg:
            continue
        # 提取工具调用
        tool_names = _extract_tool_names(rec)
        tool_str = f"（工具：{', '.join(tool_names)}）" if tool_names else ""
        dialog_summaries.append(f"{i}. {user_msg}{tool_str}")

    dialog_text = "\n".join(dialog_summaries) if dialog_summaries else "（无对话记录）"
    period = {"daily": "今天", "weekly": "本周", "monthly": "本月"}.get(report_type, "本期")
    next_period_label = {
        "daily": "明日建议（1-2 条，基于今日工作内容给出可执行建议）",
        "weekly": "下周建议（1-2 条，基于本周工作内容给出可执行建议）",
        "monthly": "下月建议（1-2 条，基于本月工作内容给出可执行建议）",
    }.get(report_type, "下期建议（1-2 条，基于本期工作内容给出可执行建议）")

    system_prompt = f"""你是数字员工的{type_label}助手，请根据以下用户{period}的对话记录，生成一份简洁的工作{type_label}。

【用户信息】
- 姓名：{user_name}
- 部门：{department or '未填写'}
- 日期：{report_date_str}

【{period}对话记录】（共 {len(records)} 条）
{dialog_text}

【请按以下结构输出】
1. 工作内容摘要（3-5 条要点，每条 1-2 句，按工作主题归类，不要按对话顺序罗列）
2. 高光时刻（选 1 条最有价值的对话，说明价值点）
3. {next_period_label}

【约束】
- 客户姓名、金额、内部系统名等敏感信息用「某客户」「某金额」替代
- 不编造未在对话中出现的内容
- 使用第一人称「你」称呼用户
- 总字数控制在 300-400 字
"""

    start_time = time.perf_counter()
    try:
        result = await llm_gateway.chat(
            messages=[{"role": "user", "content": system_prompt}],
            temperature=0.3,
            max_tokens=2048,
            model=report_model,
        )
        duration_ms = (time.perf_counter() - start_time) * 1000
        content = result.get("content", "") or ""
        usage = result.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cached_input_tokens = int(usage.get("cached_tokens") or 0)
        cache_creation_input_tokens = int(usage.get("cache_creation_tokens") or 0)

        logger.info(
            f"个人{type_label}摘要生成: user={user_name}, model={report_model}, "
            f"prompt_tokens={prompt_tokens}, completion_tokens={completion_tokens}, "
            f"duration={duration_ms:.0f}ms"
        )
        return content, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_input_tokens,
            "cache_creation_tokens": cache_creation_input_tokens,
        }
    except Exception as e:
        logger.error(f"个人{type_label}摘要生成失败: {e}", exc_info=True)
        raise


async def summarize_team(
    tenant_name: str,
    report_date_str: str,
    total_users: int,
    active_users: int,
    member_dialogs: List[Dict[str, Any]],
    report_type: str = "daily",
) -> Tuple[str, Dict[str, int]]:
    """生成团队报告摘要（基于成员对话记录，1 次 LLM 调用）

    Args:
        tenant_name: 租户名称
        report_date_str: 报告日期
        total_users: 团队总人数
        active_users: 本期活跃人数
        member_dialogs: 活跃成员的对话记录列表，每个元素形如：
            {
                "user_id": str,
                "dialog_count": int,        # 该成员本期总对话数
                "user_messages": List[str], # 已采样截断的对话片段（每条 ≤200 字，最多 50 条）
            }
            已在 aggregate_team 中按 80K 字符预算采样完成，此处不再截断。
        report_type: daily / weekly / monthly

    Returns:
        (摘要正文, usage_dict)
    """
    report_model = get_report_model()
    type_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(report_type, "报告")
    period = {"daily": "今日", "weekly": "本周", "monthly": "本月"}.get(report_type, "本期")

    # 构造每成员一段对话记录
    member_sections: List[str] = []
    for i, m in enumerate(member_dialogs[:30], start=1):  # 上限 30 个成员
        uid = m.get("user_id", f"unknown-{i}")
        dc = int(m.get("dialog_count") or 0)
        msgs = m.get("user_messages") or []
        if not msgs:
            continue
        # 展示该成员进入采样的对话数（注意：dialog_count 是全期总数，采样后可能更少）
        header = f"【成员 {i}】（user_id: {uid}）对话数: {dc}（展示最近 {len(msgs)} 条）"
        lines = [header]
        for j, msg in enumerate(msgs, start=1):
            lines.append(f"{j}. {msg}")
        member_sections.append("\n".join(lines))

    if member_sections:
        members_text = "\n\n".join(member_sections)
        truncation_note = "（注：若数据量较大，仅展示部分代表性对话，统计指标已完整聚合）"
    else:
        members_text = "（无活跃成员）"
        truncation_note = ""

    system_prompt = f"""你是团队 AI 使用{type_label}助手，请基于以下团队成员{period}的对话记录，生成团队{type_label}。{truncation_note}

【团队信息】
- 租户：{tenant_name}
- 日期：{report_date_str}
- 团队规模：{total_users} 人，{period}活跃：{active_users} 人

【成员对话记录】
{members_text}

【请按以下结构输出】
1. 团队工作成果（5-8 条要点，按业务主题归类，不要按员工罗列）
2. 协作亮点（如多人解决同类问题等）
3. 改进建议（如「部分员工尚未使用 XX 数字员工，建议推广」）

【约束】
- 不点名批评任何员工，对未使用员工用「部分成员」表达
- 客户姓名、金额、内部系统名等敏感信息用「某客户」「某金额」替代
- 不编造未在对话中出现的内容
- 总字数控制在 300-600 字
"""

    start_time = time.perf_counter()
    try:
        result = await llm_gateway.chat(
            messages=[{"role": "user", "content": system_prompt}],
            temperature=0.3,
            max_tokens=4096,
            model=report_model,
        )
        duration_ms = (time.perf_counter() - start_time) * 1000
        content = result.get("content", "") or ""
        usage = result.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cached_input_tokens = int(usage.get("cached_tokens") or 0)
        cache_creation_input_tokens = int(usage.get("cache_creation_tokens") or 0)

        logger.info(
            f"团队{type_label}摘要生成: tenant={tenant_name}, model={report_model}, "
            f"prompt_tokens={prompt_tokens}, completion_tokens={completion_tokens}, "
            f"duration={duration_ms:.0f}ms"
        )
        return content, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_input_tokens,
            "cache_creation_tokens": cache_creation_input_tokens,
        }
    except Exception as e:
        logger.error(f"团队{type_label}摘要生成失败: {e}", exc_info=True)
        raise


def _extract_tool_names(record: Dict[str, Any]) -> List[str]:
    """从单条 chat_record 提取工具调用名列表"""
    import json
    execution_details = record.get("execution_details")
    if isinstance(execution_details, str):
        try:
            execution_details = json.loads(execution_details)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(execution_details, dict):
        return []
    tool_executions = execution_details.get("tool_executions") or []
    names: List[str] = []
    for t in tool_executions:
        if isinstance(t, dict):
            name = t.get("tool_name")
            if name:
                names.append(name)
    return names
