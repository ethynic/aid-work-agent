"""
邮件工具

实现邮件发送和读取功能
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.base import BaseTool
from src.config.settings import settings


class EmailSendTool(BaseTool):
    """邮件发送工具"""
    
    name = "email_send"
    description = "发送邮件给指定收件人"
    category = "email"
    parameters_schema = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "收件人邮箱地址",
            },
            "subject": {
                "type": "string",
                "description": "邮件主题",
            },
            "body": {
                "type": "string",
                "description": "邮件正文内容",
            },
            "cc": {
                "type": "string",
                "description": "抄送人邮箱地址（可选）",
            },
        },
        "required": ["to", "subject"],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行邮件发送
        
        Args:
            to: 收件人
            subject: 主题
            body: 正文
            cc: 抄送人
        
        Returns:
            执行结果
        """
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        cc = kwargs.get("cc", "")
        
        # 获取邮件配置
        config = settings.tools.email
        
        if not config.smtp_server or not config.smtp_user:
            return {
                "success": False,
                "error": "邮件服务未配置，请联系管理员",
            }
        
        try:
            # 创建邮件
            msg = MIMEMultipart()
            msg["From"] = config.smtp_user
            msg["To"] = to
            msg["Subject"] = subject
            
            if cc:
                msg["Cc"] = cc
            
            msg.attach(MIMEText(body, "plain", "utf-8"))
            
            # 发送邮件
            with smtplib.SMTP_SSL(config.smtp_server, config.smtp_port) as server:
                server.login(config.smtp_user, config.smtp_password)
                recipients = [to]
                if cc:
                    recipients.append(cc)
                server.sendmail(config.smtp_user, recipients, msg.as_string())
            
            logger.info(f"邮件发送成功: {to}")
            
            return {
                "success": True,
                "message": f"邮件已成功发送给 {to}",
                "details": {
                    "to": to,
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
    """邮件读取工具"""
    
    name = "email_read"
    description = "读取收件箱中的邮件列表"
    category = "email"
    parameters_schema = {
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "邮件文件夹，默认为INBOX",
            },
            "limit": {
                "type": "integer",
                "description": "读取邮件数量，默认10封",
            },
            "unseen_only": {
                "type": "boolean",
                "description": "是否只读取未读邮件",
            },
        },
        "required": [],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行邮件读取
        
        Args:
            folder: 文件夹
            limit: 数量限制
            unseen_only: 是否只读未读
        
        Returns:
            执行结果
        """
        folder = kwargs.get("folder", "INBOX")
        limit = kwargs.get("limit", 10)
        unseen_only = kwargs.get("unseen_only", False)
        
        # 获取邮件配置
        config = settings.tools.email
        
        if not config.imap_server or not config.smtp_user:
            return {
                "success": False,
                "error": "邮件服务未配置，请联系管理员",
            }
        
        try:
            import imaplib
            import email
            from email.header import decode_header
            
            # 连接IMAP服务器
            mail = imaplib.IMAP4_SSL(config.imap_server, config.imap_port)
            mail.login(config.smtp_user, config.smtp_password)
            mail.select(folder)
            
            # 搜索邮件
            if unseen_only:
                status, messages = mail.search(None, "UNSEEN")
            else:
                status, messages = mail.search(None, "ALL")
            
            email_ids = messages[0].split()
            email_ids = email_ids[-limit:]  # 获取最新的N封
            
            emails = []
            for email_id in reversed(email_ids):
                status, msg_data = mail.fetch(email_id, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        # 解码主题
                        subject, encoding = decode_header(msg["Subject"])[0]
                        if isinstance(subject, bytes):
                            subject = subject.decode(encoding or "utf-8")
                        
                        # 获取发件人
                        from_ = msg.get("From", "")
                        
                        emails.append({
                            "id": email_id.decode(),
                            "subject": subject,
                            "from": from_,
                            "date": msg.get("Date", ""),
                        })
            
            mail.close()
            mail.logout()
            
            logger.info(f"读取邮件成功: {len(emails)}封")
            
            return {
                "success": True,
                "emails": emails,
                "count": len(emails),
                "message": f"成功读取{len(emails)}封邮件",
            }
        
        except Exception as e:
            logger.error(f"邮件读取失败: {e}")
            return {
                "success": False,
                "error": f"邮件读取失败: {str(e)}",
            }
