"""
邮件工具 — Agent 唯一入口（email_process）

三合一（原 email_send / email_read / email_list_folders）：
- action 确定性分发（send / read），不建 LLM 路由器
- 文件夹列表并入 read 返回的 folders 字段
- 网络同步 IO 全部走 email_lib，并用 asyncio.to_thread 包裹避免阻塞事件循环
"""

import asyncio
import imaplib
import smtplib
from typing import Any, Dict, List, Optional, Union

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools._helpers import sanitize_error
from src.models.user import UserEmail
from src.tools.email import email_lib


class EmailProcessInput(BaseModel):
    """email_process 工具入参（按 action 区分参数组）"""
    action: str = Field(..., description="操作类型：send=发送邮件 / read=读取邮件")
    # ---- send 动作参数 ----
    to: Optional[Union[str, List[str]]] = Field(
        None, description="send 必填：收件人邮箱地址，字符串（多个用逗号分隔）或列表"
    )
    subject: Optional[str] = Field(None, description="send 必填：邮件主题")
    body: Optional[str] = Field(None, description="send 必填：邮件正文内容")
    cc: Optional[Union[str, List[str]]] = Field(
        None, description="send 可选：抄送人邮箱地址，字符串（逗号分隔）或列表"
    )
    # ---- read 动作参数 ----
    folder: Optional[str] = Field("INBOX", description="read 可选：邮件文件夹，默认INBOX")
    limit: Optional[int] = Field(
        10, description="read 可选：收取邮件数量，默认10封。查询近期邮件时可设大些，精确查找时设小值"
    )
    unseen_only: Optional[bool] = Field(
        False, description="read 可选：是否只收取未读邮件，默认False。用户问'未读邮件'时设True"
    )
    from_filter: Optional[str] = Field("", description="read 可选：发件人过滤条件（本地过滤）")
    subject_filter: Optional[str] = Field("", description="read 可选：主题过滤条件（本地过滤）")
    body_preview_len: Optional[int] = Field(
        500, description="read 可选：正文预览长度（字符），默认500"
    )


TOOL_DESCRIPTION = """邮件处理工具。通过用户绑定的邮箱发送和读取邮件。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求发送邮件（action="send"）
- 用户要求查看/收取邮件、查未读邮件、按发件人或主题筛选邮件（action="read"）
- 用户询问邮箱有哪些文件夹（action="read"，看返回的 folders 字段）

参数说明（action 必填，按动作确定性分发）：
- send 动作：to（收件人，必填）、subject（主题，必填）、body（正文，必填）、cc（抄送，可选）
- read 动作：folder（默认INBOX）、limit（默认10封）、unseen_only（默认False）、
  from_filter/subject_filter（可选，按发件人/主题筛选）、body_preview_len（正文预览长度，默认500）

调用注意：
- 未绑定邮箱时会返回错误提示，需引导用户先在设置中绑定邮箱
- read 返回结构：emails（uid/subject/from/to/date/body_preview）+ folders（文件夹列表）+ count
- 按发件人/主题筛选时工具会先只拉取邮件头做本地过滤，命中才拉取全文"""


def _clamp_int(value, default: int, low: int, high: int) -> int:
    """
    数值入参防御：宽容转换 + 夹紧到 [low, high]

    主执行链（core/executor.py execute_task）对工具参数不做 Pydantic 校验，
    LLM 可能传字符串/None/浮点/超大值，直接透传会在库层比较或切片时抛
    TypeError（落入兜底文案）或造成资源放大（逐封拉全信），故在此归一。
    """
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))


# 发送失败白名单：类型命中返回固定安全文案，未命中走 fallback（不透传 str(e)）
_SEND_SAFE_MESSAGES = {
    email_lib.EmailLibError: "收件人地址无效，请检查收件人邮箱地址",
    smtplib.SMTPAuthenticationError: "邮箱账号或密码（授权码）错误，请检查邮箱配置",
    smtplib.SMTPConnectError: "无法连接SMTP服务器，请检查邮箱服务器配置",
    smtplib.SMTPServerDisconnected: "SMTP服务器连接中断，请稍后重试",
    ConnectionRefusedError: "无法连接SMTP服务器，请检查网络或端口配置",
    TimeoutError: "连接SMTP服务器超时，请检查网络",
    OSError: "网络异常，邮件发送失败",
}

# 读取失败白名单（imaplib.IMAP4.error 覆盖登录失败/命令失败等，统一固定文案）
_READ_SAFE_MESSAGES = {
    imaplib.IMAP4.error: "邮箱登录或读取失败，请检查邮箱配置",
    ConnectionRefusedError: "无法连接IMAP服务器，请检查网络或端口配置",
    TimeoutError: "连接IMAP服务器超时，请检查网络",
    OSError: "网络异常，邮件收取失败",
}


class EmailProcessTool(BaseTool):
    """邮件处理工具（send / read 确定性分发）"""

    name = "email_process"
    description = TOOL_DESCRIPTION
    display_name = "邮件处理"
    category = "email"
    InputModel = EmailProcessInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名：按 action 展示收件人或文件夹+数量"""
        if tool_args:
            action = tool_args.get("action")
            if action == "send":
                to = tool_args.get("to") or ""
                if to:
                    to_display = ", ".join(to) if isinstance(to, list) else to
                    return f"发送邮件至「{to_display}」"
                return "发送邮件"
            if action == "read":
                folder = tool_args.get("folder") or "INBOX"
                limit = tool_args.get("limit", 10)
                return f"读取邮件（{folder}，{limit}封）"
        return self.display_name

    def __init__(self, user_email: Optional[UserEmail] = None):
        """
        初始化邮件处理工具

        Args:
            user_email: 用户邮箱配置（可选，不传则运行时从数据库读取）
        """
        self.user_email = user_email

    def _resolve_user_email(self) -> Optional[UserEmail]:
        """获取用户邮箱配置：优先使用注入的配置，否则从DB读取"""
        if self.user_email:
            return self.user_email
        from src.tools.context import current_tool_execution_context
        context = current_tool_execution_context()
        if context and context.user_id:
            from src.db.email_credential import EmailCredentialDB
            return EmailCredentialDB.get_user_email_model(context.user_id)
        return None

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行邮件处理（按 action 确定性分发）

        Args:
            action: send / read
            其余参数见 EmailProcessInput

        Returns:
            执行结果
        """
        # 数据库凭据解析是同步调用；async 工具入口必须放到工作线程，避免
        # 邮件调用前的账号查询阻塞同一 worker 上的其他请求。
        user_email = await asyncio.to_thread(self._resolve_user_email)
        if not user_email:
            return {"success": False, "error": "未绑定邮箱，请先去设置中绑定邮箱"}

        action = str(kwargs.get("action") or "").strip().lower()
        if action == "send":
            return await self._execute_send(user_email, **kwargs)
        if action == "read":
            return await self._execute_read(user_email, **kwargs)

        return {
            "success": False,
            "error": f"不支持的操作类型: {action or '(空)'}，action 必须为 send 或 read",
        }

    async def _execute_send(self, user_email: UserEmail, **kwargs) -> Dict[str, Any]:
        """发送邮件（同步 SMTP 调用经 to_thread 避免阻塞事件循环）"""
        to = kwargs.get("to")
        subject = kwargs.get("subject") or ""
        body = kwargs.get("body") or ""
        cc = kwargs.get("cc") or ""

        if not to or not subject:
            return {"success": False, "error": "收件人和邮件主题为必填项"}

        try:
            details = await asyncio.to_thread(
                email_lib.send_email, user_email, to, subject, body, cc
            )
        except Exception as e:
            logger.opt(exception=True).error(
                f"邮件发送失败: from={user_email.email_address}, to={to}"
            )
            return {
                "success": False,
                "error": sanitize_error(
                    e, safe_messages=_SEND_SAFE_MESSAGES, fallback="邮件发送失败，请稍后重试"
                ),
            }

        logger.info(
            f"邮件发送成功: from={user_email.email_address}, to={details.get('to')}, "
            f"encryption={user_email.smtp_encryption}"
        )
        return {
            "success": True,
            "message": f"邮件已成功发送给 {details.get('to')}",
            "details": {
                "from": details.get("from"),
                "to": details.get("to"),
                "cc": details.get("cc"),
                "subject": details.get("subject"),
            },
        }

    async def _execute_read(self, user_email: UserEmail, **kwargs) -> Dict[str, Any]:
        """读取邮件（同步 IMAP 调用经 to_thread 避免阻塞事件循环）"""
        folder = kwargs.get("folder") or "INBOX"
        limit = _clamp_int(kwargs.get("limit"), default=10, low=1, high=100)
        unseen_only = kwargs.get("unseen_only", False)
        from_filter = kwargs.get("from_filter") or ""
        subject_filter = kwargs.get("subject_filter") or ""
        body_preview_len = _clamp_int(kwargs.get("body_preview_len"), default=500, low=1, high=5000)

        try:
            return await asyncio.to_thread(
                email_lib.read_emails,
                user_email,
                folder=folder,
                limit=limit,
                unseen_only=unseen_only,
                from_filter=from_filter,
                subject_filter=subject_filter,
                body_preview_len=body_preview_len,
            )
        except email_lib.EmailLibError as e:
            # 已知错误：message 为固定安全文案，可直接透传
            logger.warning(f"邮件收取失败: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.opt(exception=True).error(f"邮件收取失败: {e}")
            return {
                "success": False,
                "error": sanitize_error(
                    e, safe_messages=_READ_SAFE_MESSAGES, fallback="邮件收取失败，请稍后重试"
                ),
            }


def create_email_tools(user_email: Optional[UserEmail] = None) -> List[BaseTool]:
    """
    创建邮件工具实例（三合一后仅一个工具）

    Args:
        user_email: 用户邮箱配置（可选）

    Returns:
        邮件工具列表
    """
    return [EmailProcessTool(user_email)]
