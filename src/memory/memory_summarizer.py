"""
每日记忆自动总结模块

每天定时执行，收集当天有活跃会话的用户对话内容，
调用 LLM 提取值得长期记忆的信息，增量合并到用户记忆文件。
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from src.config.settings import settings
from src.memory.long_term import LongTermMemory


# LLM 提取 Prompt
EXTRACTION_PROMPT = """你是一个用户记忆分析助手。请分析以下今天的对话记录，提取值得长期记住的用户信息。

提取规则（仅提取以下类型的信息）：
1. 用户明确要求记住的事项（如"记住我的偏好是..."、"帮我记一下..."）
2. 用户的个人特征（职位、部门、职责范围）
3. 用户的偏好（回复风格、语言、格式偏好）
4. 用户的习惯性工作行为（常用工具、工作流程、工作时间段）
5. 用户提及的业务知识和规则
6. 用户频繁联系的同事或客户

不要提取：
- 一次性的任务内容（如"帮我查个订单"）
- 临时性的对话上下文
- 敏感信息（密码、密钥、完整身份证号等）

当前用户记忆文件内容：
{existing_memory}

今天的对话记录：
{conversations}

请输出需要新增或修改的记忆条目，格式为：
## [分类标题]
- [记忆内容]

如果某个已有分类需要更新，直接输出该分类和更新后的完整条目。
如果是新分类，使用新的二级标题。
如果今天的对话没有值得长期记忆的信息，输出：无更新"""


async def run_memory_summarization() -> Dict[str, int]:
    """
    执行一次全量记忆总结。

    Returns:
        统计信息 {"processed": N, "updated": N, "skipped": N, "errors": N}
    """
    stats = {"processed": 0, "updated": 0, "skipped": 0, "errors": 0}

    if not settings.memory.long_term.enabled:
        logger.info("Long-term memory is disabled, skipping summarization")
        return stats

    ltm = LongTermMemory(storage_dir=settings.memory.long_term.storage_dir)

    # 获取当天有活跃会话的用户列表
    active_users = _get_active_users()
    if not active_users:
        logger.info("No active users found for memory summarization")
        return stats

    max_users = settings.memory.long_term.max_users_per_run
    if len(active_users) > max_users:
        logger.info(f"Limiting summarization to {max_users} users (total: {len(active_users)})")
        active_users = active_users[:max_users]

    for tenant_id, user_id in active_users:
        stats["processed"] += 1
        try:
            updated = await _summarize_user(ltm, tenant_id, user_id)
            if updated:
                stats["updated"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.error(f"Failed to summarize memory for user {user_id} (tenant={tenant_id}): {e}")

    logger.info(f"Memory summarization completed: {stats}")
    return stats


async def _summarize_user(
    ltm: LongTermMemory,
    tenant_id: Optional[str],
    user_id: str,
) -> bool:
    """
    为单个用户执行记忆总结。

    Returns:
        True 如果有更新，False 如果跳过
    """
    # 1. 收集当天对话
    conversations = _get_user_conversations(tenant_id, user_id)
    if not conversations:
        return False

    # 2. 读取现有记忆
    existing_memory = ltm.get_memory(tenant_id, user_id)

    # 3. 调用 LLM 提取
    prompt = EXTRACTION_PROMPT.format(
        existing_memory=existing_memory[:3000],
        conversations=conversations[:8000],
    )

    llm_output = await _call_llm(prompt)
    if not llm_output or llm_output.strip() == "无更新":
        return False

    # 4. 解析 LLM 输出为 {分类: 条目} 结构
    new_sections = _parse_llm_output(llm_output)
    if not new_sections:
        return False

    # 5. 增量合并
    ltm.merge_memory(tenant_id, user_id, new_sections)
    logger.info(f"Updated memory for user {user_id} (tenant={tenant_id}): {list(new_sections.keys())}")
    return True


def _get_active_users() -> List[Tuple[Optional[str], str]]:
    """
    获取当天有活跃会话的用户列表（含 tenant_id）。

    Returns:
        [(tenant_id, user_id), ...] 列表
    """
    try:
        from src.db.database import get_db_connection

        today = datetime.now().strftime("%Y-%m-%d")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT s.tenant_id, s.user_id
                FROM chat_sessions s
                WHERE s.created_at >= %s::date
                   OR s.updated_at >= %s::date
                UNION
                SELECT DISTINCT cs.tenant_id, cs.user_id
                FROM channel_sessions cs
                WHERE cs.created_at >= %s
                   OR cs.updated_at >= %s
                ORDER BY user_id
                """,
                (today, today, today, today),
            )
            rows = cursor.fetchall()
            return [(row[0], row[1]) for row in rows if row[1]]
    except Exception as e:
        logger.error(f"Failed to get active users: {e}")
        return []


def _get_user_conversations(tenant_id: Optional[str], user_id: str) -> str:
    """
    收集用户当天的对话内容，只保留用户问题和助手最终回答。
    去除附件上下文、时间戳前缀等工具调用的中间信息，减少 LLM 上下文。
    """
    try:
        from src.db.database import get_db_connection

        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT m.role, m.content
                FROM chat_messages m
                JOIN chat_sessions s ON m.session_id = s.session_id
                WHERE s.user_id = %s
                  AND m.created_at >= %s::date
                  AND m.created_at < %s::date
                  AND m.role IN ('user', 'assistant')
                ORDER BY m.created_at ASC
                LIMIT 200
                """,
                (user_id, today, tomorrow),
            )
            rows = cursor.fetchall()

            # 同时查询渠道消息（channel 表日期为 TEXT 类型，使用字符串比较）
            cursor.execute(
                """
                SELECT m.role, m.content
                FROM channel_messages m
                JOIN channel_sessions s ON m.session_id = s.session_id
                WHERE s.user_id = %s
                  AND m.created_at >= %s
                  AND m.created_at < %s
                  AND m.role IN ('user', 'assistant')
                ORDER BY m.created_at ASC
                LIMIT 200
                """,
                (user_id, today, tomorrow),
            )
            channel_rows = cursor.fetchall()
            rows.extend(channel_rows)

            if not rows:
                return ""

            lines = []
            for role, content in rows:
                cleaned = _clean_message_content(role, content)
                if not cleaned:
                    continue
                label = "用户" if role == "user" else "助手"
                truncated = cleaned[:500] + "..." if len(cleaned) > 500 else cleaned
                lines.append(f"{label}: {truncated}")

            return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get conversations for user {user_id}: {e}")
        return ""


def _clean_message_content(role: str, content: str) -> str:
    """
    清理消息内容，只保留有记忆价值的部分。

    - 用户消息：去除时间戳前缀、附件上下文（文件路径等工具调用中间信息）
    - 助手消息：保留最终回答（已经不包含工具调用细节）
    """
    if not content:
        return ""

    text = content.strip()

    if role == "user":
        # 去除时间戳前缀：[当前时间: 2026年05月19日 15:30:00, Monday, 今年是2026年]
        text = re.sub(r'^\[当前时间[^\]]*\]\s*', '', text)

        # 去除附件区块：[Attachments] 及其后面所有内容
        text = re.split(r'\[Attachments\]', text)[0]

        # 去除残留的附件路径信息
        text = re.sub(r'\n+\*\*📎 Uploaded files available:\*\*.*', '', text, flags=re.DOTALL)
        text = re.sub(r'\n+\*\*IMPORTANT:.*?\*\*', '', text, flags=re.DOTALL)

    text = text.strip()
    return text


async def _call_llm(prompt: str) -> Optional[str]:
    """调用 LLM 进行记忆提取"""
    try:
        from src.llm.gateway import llm_gateway

        messages = [{"role": "user", "content": prompt}]
        response = await llm_gateway.chat(messages=messages)
        return response
    except Exception as e:
        logger.error(f"LLM call for memory summarization failed: {e}")
        return None


def _parse_llm_output(output: str) -> Dict[str, List[str]]:
    """
    解析 LLM 输出为 {分类标题: [条目列表]} 结构。

    支持格式：
    ## 分类标题
    - 条目1
    - 条目2
    """
    sections: Dict[str, List[str]] = {}
    current_section = None

    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
            if current_section not in sections:
                sections[current_section] = []
        elif stripped.startswith("- ") and current_section is not None:
            sections[current_section].append(stripped[2:])

    return sections
