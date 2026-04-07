"""
邮件工具

实现邮件发送和收取功能，使用用户的邮箱配置
"""

import smtplib
import imaplib
import email
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.models.user import UserEmail


class EmailSendInput(BaseModel):
    """发送邮件参数"""
    to: str = Field(..., description="收件人邮箱地址，多个地址用逗号分隔")
    subject: str = Field(..., description="邮件主题")
    body: str = Field(..., description="邮件正文内容")
    cc: Optional[str] = Field("", description="抄送人邮箱地址，多个地址用逗号分隔（可选）")


class EmailReadInput(BaseModel):
    """读取邮件参数"""
    folder: Optional[str] = Field("INBOX", description="邮件文件夹，默认INBOX")
    limit: Optional[int] = Field(10, description="收取邮件数量，默认10封")
    unseen_only: Optional[bool] = Field(False, description="是否只收取未读邮件，默认False")
    from_filter: Optional[str] = Field("", description="发件人过滤条件（可选）")
    subject_filter: Optional[str] = Field("", description="主题过滤条件（可选）")


class EmailListFoldersInput(BaseModel):
    """获取邮件夹列表参数（无参数）"""
    pass


def decode_imap_folder_name(name: str) -> str:
    """
    解码 IMAP 文件夹名称 (modified UTF-7)
    
    IMAP 使用 modified UTF-7 编码非ASCII字符
    例如: &XfJT0ZAB- 解码后为 "已发送邮件"
    """
    if not name:
        return name
    
    # 如果没有 & 符号，说明是纯ASCII
    if '&' not in name:
        return name
    
    result = []
    i = 0
    while i < len(name):
        if name[i] == '&':
            # 查找结束符 '-'
            end = name.find('-', i)
            if end == -1:
                result.append(name[i:])
                break
            
            # 提取编码部分
            encoded = name[i+1:end]
            if encoded == '':  # '&-' 表示 '&' 字符
                result.append('&')
            else:
                try:
                    # modified UTF-7: 将 ',' 替换为 '/'
                    encoded = encoded.replace(',', '/')
                    # 添加填充
                    padding = (4 - len(encoded) % 4) % 4
                    encoded += '=' * padding
                    # 解码 base64
                    decoded = base64.b64decode(encoded)
                    result.append(decoded.decode('utf-16-be'))
                except Exception:
                    result.append(name[i:end+1])
            i = end + 1
        else:
            result.append(name[i])
            i += 1
    
    return ''.join(result)


class EmailSendTool(BaseTool):
    """邮件发送工具"""

    name = "email_send"
    description = "发送邮件给指定收件人"
    display_name = "发送邮件"
    category = "email"
    InputModel = EmailSendInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示收件人"""
        if tool_args:
            to = tool_args.get("to", "")
            if to:
                return f"发送邮件至「{to}」"
        return self.display_name

    def __init__(self, user_email: UserEmail):
        """
        初始化邮件发送工具
        
        Args:
            user_email: 用户邮箱配置
        """
        self.user_email = user_email

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行邮件发送

        Args:
            to: 收件人邮箱地址
            subject: 邮件主题
            body: 邮件正文
            cc: 抄送人邮箱地址

        Returns:
            执行结果
        """
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        cc = kwargs.get("cc", "")

        if not to or not subject:
            return {
                "success": False,
                "error": "收件人和邮件主题为必填项",
            }

        try:
            # 创建邮件
            msg = MIMEMultipart()
            msg["From"] = self.user_email.email_address
            msg["Subject"] = subject

            # 处理收件人（支持字符串或列表）
            if isinstance(to, list):
                to_str = ", ".join(to)
                recipients = [addr.strip() for addr in to]
            else:
                to_str = to
                recipients = [addr.strip() for addr in to.split(",")]
            msg["To"] = to_str

            # 处理抄送人（支持字符串或列表）
            if cc:
                if isinstance(cc, list):
                    cc_str = ", ".join(cc)
                    recipients.extend([addr.strip() for addr in cc])
                else:
                    cc_str = cc
                    recipients.extend([addr.strip() for addr in cc.split(",")])
                msg["Cc"] = cc_str if isinstance(cc, list) else cc

            msg.attach(MIMEText(body, "plain", "utf-8"))

            # 根据加密协议发送邮件
            if self.user_email.smtp_encryption == "ssl":
                # SSL/TLS 加密连接
                with smtplib.SMTP_SSL(
                    self.user_email.smtp_server,
                    self.user_email.smtp_port
                ) as server:
                    server.login(
                        self.user_email.smtp_user,
                        self.user_email.smtp_password
                    )
                    server.sendmail(
                        self.user_email.email_address,
                        recipients,
                        msg.as_string()
                    )
            elif self.user_email.smtp_encryption == "tls":
                # STARTTLS 加密连接
                with smtplib.SMTP(
                    self.user_email.smtp_server,
                    self.user_email.smtp_port
                ) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(
                        self.user_email.smtp_user,
                        self.user_email.smtp_password
                    )
                    server.sendmail(
                        self.user_email.email_address,
                        recipients,
                        msg.as_string()
                    )
            else:
                # 无加密连接（不推荐）
                with smtplib.SMTP(
                    self.user_email.smtp_server,
                    self.user_email.smtp_port
                ) as server:
                    server.login(
                        self.user_email.smtp_user,
                        self.user_email.smtp_password
                    )
                    server.sendmail(
                        self.user_email.email_address,
                        recipients,
                        msg.as_string()
                    )

            logger.info(f"邮件发送成功: from={self.user_email.email_address}, to={to}, encryption={self.user_email.smtp_encryption}")

            return {
                "success": True,
                "message": f"邮件已成功发送给 {to}",
                "details": {
                    "from": self.user_email.email_address,
                    "to": to,
                    "cc": cc,
                    "subject": subject,
                },
            }

        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return {
                "success": False,
                "error": f"邮件发送失败: {str(e)}",
            }


class EmailReadTool(BaseTool):
    """邮件收取工具"""

    name = "email_read"
    description = "收取用户邮箱中的邮件"
    display_name = "读取邮件"
    category = "email"
    InputModel = EmailReadInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示文件夹和数量"""
        if tool_args:
            folder = tool_args.get("folder", "INBOX")
            limit = tool_args.get("limit", 10)
            return f"读取邮件（{folder}，{limit}封）"
        return self.display_name

    def __init__(self, user_email: UserEmail):
        """
        初始化邮件收取工具
        
        Args:
            user_email: 用户邮箱配置
        """
        self.user_email = user_email

    def _list_folders(self, mail) -> List[Dict[str, Any]]:
        """列出所有邮件文件夹"""
        try:
            status, folders = mail.list()
            if status != "OK":
                return []
            
            result = []
            for folder in folders:
                if folder:
                    # 解析文件夹信息
                    folder_str = folder.decode() if isinstance(folder, bytes) else folder
                    parts = folder_str.split('"')
                    if len(parts) >= 3:
                        folder_name = parts[-2] if parts[-2] else parts[-1].strip()
                        result.append({"name": folder_name, "raw": folder_str})
            return result
        except Exception as e:
            logger.error(f"列出文件夹失败: {e}")
            return []

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行邮件收取

        Args:
            limit: 收取邮件数量
            folder: 邮件文件夹
            unseen_only: 是否只收取未读邮件
            from_filter: 发件人过滤条件
            subject_filter: 主题过滤条件

        Returns:
            执行结果
        """
        limit = kwargs.get("limit", 10)
        folder = kwargs.get("folder", "INBOX")
        unseen_only = kwargs.get("unseen_only", False)
        from_filter = kwargs.get("from_filter", "")
        subject_filter = kwargs.get("subject_filter", "")

        try:
            # 根据加密协议连接IMAP服务器
            if self.user_email.imap_encryption == "ssl":
                # SSL/TLS 加密连接
                mail = imaplib.IMAP4_SSL(
                    self.user_email.imap_server,
                    self.user_email.imap_port
                )
            elif self.user_email.imap_encryption == "tls":
                # STARTTLS 加密连接
                mail = imaplib.IMAP4(
                    self.user_email.imap_server,
                    self.user_email.imap_port
                )
                mail.starttls()
            else:
                # 无加密连接（不推荐）
                mail = imaplib.IMAP4(
                    self.user_email.imap_server,
                    self.user_email.imap_port
                )

            imap_user, imap_password = self.user_email.get_imap_credentials()
            mail.login(imap_user, imap_password)
            mail.select(folder)

            # 构建服务器端搜索条件（仅支持ASCII的条件）
            server_criteria = []
            local_from_filter = ""
            local_subject_filter = ""
            
            if unseen_only:
                server_criteria.append("UNSEEN")
            
            # 发件人和主题过滤在本地进行（避免编码问题）
            if from_filter:
                local_from_filter = from_filter
            if subject_filter:
                local_subject_filter = subject_filter

            # 执行服务器端搜索（使用UID模式）
            if server_criteria:
                search_query = " ".join(server_criteria)
                status, messages = mail.uid("search", None, search_query)
            else:
                status, messages = mail.uid("search", None, "ALL")

            if status != "OK":
                return {
                    "success": False,
                    "error": "搜索邮件失败",
                }

            logger.info(f"IMAP搜索返回原始数据: {messages}")
            
            # 尝试使用普通搜索模式作为对比
            if not server_criteria:
                status2, messages2 = mail.search(None, "ALL")
                logger.info(f"普通搜索返回数据: {messages2}")
                if status2 == "OK" and messages2[0]:
                    normal_count = len(messages2[0].split())
                    logger.info(f"普通搜索找到 {normal_count} 封邮件")

            email_uids = messages[0].split() if messages[0] else []
            total_found = len(email_uids)
            logger.info(f"服务器搜索到 {total_found} 封邮件 (使用UID模式)")
            
            # 如果需要本地过滤，获取更多邮件
            if local_from_filter or local_subject_filter:
                fetch_limit = limit * 3
            else:
                # 无本地过滤时，按limit限制
                fetch_limit = min(limit, total_found)
            
            email_uids = email_uids[-fetch_limit:]
            logger.info(f"准备获取 {len(email_uids)} 封邮件")

            emails = []
            fetched_count = 0
            for uid in reversed(email_uids):
                # 使用 UID 和 BODY.PEEK[] 避免自动标记为已读
                status, msg_data = mail.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    logger.warning(f"获取邮件 UID:{uid} 失败: {status}")
                    continue
                
                fetched_count += 1
                    
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])

                        # 解码主题
                        subject = self._decode_header_value(msg["Subject"])
                        
                        # 获取发件人
                        from_ = self._decode_header_value(msg.get("From", ""))
                        
                        # 本地过滤
                        if local_from_filter and local_from_filter.lower() not in from_.lower():
                            continue
                        if local_subject_filter and local_subject_filter.lower() not in subject.lower():
                            continue
                        
                        # 获取正文
                        body = self._get_email_body(msg)

                        emails.append({
                            "uid": uid.decode(),
                            "subject": subject,
                            "from": from_,
                            "to": msg.get("To", ""),
                            "date": msg.get("Date", ""),
                            "body_preview": body[:200] if body else "",
                        })
                        
                        # 达到限制数量后停止
                        if len(emails) >= limit:
                            break
                
                if len(emails) >= limit:
                    break

            logger.info(f"成功获取 {fetched_count} 封邮件内容，返回 {len(emails)} 封")

            mail.close()
            mail.logout()

            logger.info(f"收取邮件成功: {len(emails)}封")

            return {
                "success": True,
                "emails": emails,
                "count": len(emails),
                "message": f"成功收取{len(emails)}封邮件",
                "folder": folder,
            }

        except Exception as e:
            logger.error(f"邮件收取失败: {e}")
            return {
                "success": False,
                "error": f"邮件收取失败: {str(e)}",
            }

    def _decode_header_value(self, value: Optional[str]) -> str:
        """解码邮件头部值"""
        if not value:
            return ""
        
        try:
            decoded_parts = decode_header(value)
            result = []
            for part, encoding in decoded_parts:
                if isinstance(part, bytes):
                    result.append(part.decode(encoding or "utf-8", errors="ignore"))
                else:
                    result.append(part)
            return "".join(result)
        except Exception:
            return value

    def _get_email_body(self, msg) -> str:
        """获取邮件正文"""
        body = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                
                # 跳过附件
                if "attachment" in content_disposition:
                    continue
                
                if content_type == "text/plain":
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="ignore")
                        break
                    except Exception:
                        continue
        else:
            try:
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="ignore")
            except Exception:
                pass
        
        return body


class EmailListFoldersTool(BaseTool):
    """列出邮件文件夹工具"""

    name = "email_list_folders"
    description = "列出邮箱中的所有文件夹及其邮件统计"
    display_name = "获取邮件夹列表"
    category = "email"
    InputModel = EmailListFoldersInput

    def __init__(self, user_email: UserEmail):
        self.user_email = user_email

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """列出所有文件夹并统计邮件数量"""
        try:
            # 连接IMAP服务器
            if self.user_email.imap_encryption == "ssl":
                mail = imaplib.IMAP4_SSL(
                    self.user_email.imap_server,
                    self.user_email.imap_port
                )
            elif self.user_email.imap_encryption == "tls":
                mail = imaplib.IMAP4(
                    self.user_email.imap_server,
                    self.user_email.imap_port
                )
                mail.starttls()
            else:
                mail = imaplib.IMAP4(
                    self.user_email.imap_server,
                    self.user_email.imap_port
                )

            imap_user, imap_password = self.user_email.get_imap_credentials()
            mail.login(imap_user, imap_password)

            # 获取所有文件夹
            status, folders = mail.list()
            if status != "OK":
                return {"success": False, "error": "无法获取文件夹列表"}

            result = []
            for folder in folders:
                if folder:
                    folder_str = folder.decode() if isinstance(folder, bytes) else folder
                    # 解析文件夹名称
                    parts = folder_str.split('"')
                    folder_name = parts[-2].strip() if len(parts) >= 3 else folder_str
                    
                    # 解码文件夹名称 (modified UTF-7)
                    decoded_name = decode_imap_folder_name(folder_name)
                    
                    try:
                        # 选择文件夹并统计邮件
                        status, data = mail.select(folder_name, readonly=True)
                        if status == "OK":
                            total = int(data[0])
                            # 获取未读邮件数
                            status, unseen = mail.search(None, "UNSEEN")
                            unseen_count = len(unseen[0].split()) if unseen[0] else 0
                            
                            result.append({
                                "name": decoded_name,
                                "original_name": folder_name,
                                "total": total,
                                "unseen": unseen_count,
                            })
                    except Exception as e:
                        logger.warning(f"无法访问文件夹 {decoded_name}: {e}")

            mail.logout()

            return {
                "success": True,
                "folders": result,
                "total_folders": len(result),
                "message": f"找到 {len(result)} 个文件夹",
            }

        except Exception as e:
            logger.error(f"列出文件夹失败: {e}")
            return {"success": False, "error": f"列出文件夹失败: {str(e)}"}


def create_email_tools(user_email: UserEmail) -> List[BaseTool]:
    """
    创建邮件工具实例
    
    Args:
        user_email: 用户邮箱配置
    
    Returns:
        邮件工具列表
    """
    return [
        EmailSendTool(user_email),
        EmailReadTool(user_email),
        EmailListFoldersTool(user_email),
    ]
