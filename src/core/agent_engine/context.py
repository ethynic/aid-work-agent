"""Pure formatting: callers resolve templates and data before entering here."""

from src.prompts.renderer import render_template as render_prompt

__all__ = ["render_prompt"]

from typing import List, Dict, Any
from loguru import logger

def reorder_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将（可能因 DB 排序错乱而乱序的）历史消息重建为 LLM API 合法序列。

    不依赖输入顺序：按 tool_call_id 重新配对，确保每条 assistant(tool_calls)
    后立即、连续地跟随其匹配 tool 结果（按声明顺序）；丢弃孤儿 tool，降级悬空 tool_calls。
    """
    messages: List[Dict[str, Any]] = []

    # 第一遍：tool_call_id -> tool 结果消息（tool_call_id 唯一，取首条，忽略重复）
    tool_result_by_id: Dict[str, Dict[str, Any]] = {}
    for msg in history:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id and tc_id not in tool_result_by_id:
                tool_result_by_id[tc_id] = msg

    # 第二遍：按历史顺序输出；assistant(tool_calls) 立即消费其全部匹配 tool 结果
    consumed_tool_ids: set = set()  # 已随某条 assistant 输出的 tool_call_id
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "assistant" and msg.get("tool_calls"):
            tool_calls = msg.get("tool_calls", []) or []
            # 按 tool_calls 声明顺序，收集「存在结果且尚未被消费」的配对
            matched = [
                (tc, tool_result_by_id[tc["id"]])
                for tc in tool_calls
                if tc.get("id") and tc["id"] in tool_result_by_id and tc["id"] not in consumed_tool_ids
            ]
            if matched:
                kept_tc = [tc for tc, _ in matched]
                asst_msg = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": kept_tc,
                }
                if msg.get("reasoning_content"):
                    asst_msg["reasoning_content"] = msg["reasoning_content"]
                messages.append(asst_msg)
                for tc, tool_msg in matched:
                    consumed_tool_ids.add(tc["id"])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_msg.get("content", ""),
                    })
            else:
                # 悬空 tool_calls：无任何匹配结果，降级为普通 assistant 避免 API 400
                tc_id_list = [tc.get("id", "") for tc in tool_calls]
                logger.warning(
                    f"后端日志：_build_messages 降级悬空 tool_calls（无匹配 tool 结果），"
                    f"tc_ids={tc_id_list}"
                )
                if content:
                    asst_msg = {"role": "assistant", "content": content}
                    if msg.get("reasoning_content"):
                        asst_msg["reasoning_content"] = msg["reasoning_content"]
                    messages.append(asst_msg)
        elif role == "tool":
            # tool 结果已在 assistant(tool_calls) 分支随其调用方连续输出，此处跳过
            continue
        elif role == "assistant":
            # 普通 assistant 消息，跳过空 content
            if content:
                asst_msg = {"role": "assistant", "content": content}
                if msg.get("reasoning_content"):
                    asst_msg["reasoning_content"] = msg["reasoning_content"]
                messages.append(asst_msg)
        elif role == "system":
            # system 标记消息已在上层 _build_messages 提取并拼接到 system_prompt，
            # 此处不应再出现在对话序列中；转成 user 会与其后的真实 user 形成连续
            # user，被清洗丢弃且污染对话语义。直接跳过。
            continue
        else:
            # user 消息（含未知角色兜底），跳过空 content
            if content:
                messages.append({"role": "user", "content": content})

    # P0-3 兜底：清洗连续 user（保留最新一条），防御历史脏数据 / 极端 race。
    # 连续 user 会导致 LLM API 行为异常（多数提供商把第二条 user 视作新轮次输入，
    # 历史 assistant 上下文失效）。此处丢弃较早的，仅保留最新 user。
    cleaned: List[Dict[str, Any]] = []
    prev_role: Optional[str] = None
    dropped_user_count = 0
    dropped_users: List[Dict[str, Any]] = []  # 诊断：记录被丢弃的 user 消息
    for msg in messages:
        role = msg.get("role")
        if role == "user" and prev_role == "user":
            # 前一条 user 已 append，弹出它（丢弃较早的），保留当前最新一条
            dropped = cleaned.pop()
            dropped_user_count += 1
            dropped_users.append(dropped)
        cleaned.append(msg)
        prev_role = role
    if dropped_user_count > 0:
        # 诊断：输出被丢弃的 user 内容 + 完整序列概览，定位"连续 user"来源
        # 可能来源：① 上一轮 agent 返回空响应 -> assistant 空内容被跳过
        #          ② 飞书事件去重失效（多 worker 下 _feishu_event_dedup 是进程内 dict）
        #          ③ 批量写入部分失败
        def _preview(m: Dict[str, Any], n: int = 120) -> str:
            c = m.get("content", "")
            if not isinstance(c, str):
                c = str(c)
            return repr(c[:n])

        # 被丢弃 user 预览加长到 500 字符：语音消息内容为 "[ASR识别结果] 文本"，
        # 完整记录识别文本，供人工评判丢弃是否合理（无需回听语音文件）
        dropped_preview = [_preview(m, 500) for m in dropped_users]
        seq_preview = [
            f"{m.get('role')}:{_preview(m, 60)}"
            for m in messages[:30]
        ]
        logger.warning(
            f"后端日志：_reorder_messages_for_llm 检测到连续 user，"
            f"已丢弃较早的 {dropped_user_count} 条（保留最新）。"
            f"被丢弃 user 内容: {dropped_preview}. "
            f"完整序列前30条: {seq_preview}"
        )

    # 裁剪窗口起始边界：长会话取最近 N 条后，开头可能落在 assistant（甚至 tool 结果）上，
    # 而多数 LLM API（DeepSeek/OpenAI 兼容）要求序列首条（system 之后）必须是 user，
    # 否则报 400。丢弃开头的非 user 消息，直到第一条 user，使截断边界对齐到安全位置。
    # （孤儿 tool 已在上方被跳过，不会出现在开头；此处主要裁掉开头的 assistant。
    #   连同其后的 tool 一起被丢弃，不会产生新的孤儿 tool。）
    start = 0
    while start < len(cleaned) and cleaned[start].get("role") != "user":
        start += 1
    if 0 < start < len(cleaned):
        logger.info(
            f"后端日志：_reorder_messages_for_llm 裁剪窗口起始 {start} 条非 user 消息"
            f"（长会话截断边界对齐到 user，避免首条非 user 触发 LLM API 400）"
        )
        cleaned = cleaned[start:]

    return cleaned
