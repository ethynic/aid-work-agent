"""
邮件同步逻辑层（email_lib）

纯同步 IMAP/SMTP 逻辑，不含 Agent 工具壳（对齐 src/tools/excel/ 的目录模式）：
- 连接建立（统一 timeout，防网络挂起导致请求永久阻塞）
- SMTP 发送（with 语句保证连接释放）
- IMAP 读取（两段式过滤：先 HEADER.PEEK 拉头本地过滤，命中才拉全信）
- 邮件头/正文解析（header 解码、text/plain + text/html 回退去标签）
- 文件夹列举（modified UTF-7 解码）

本层函数全部为同步函数，由工具层用 asyncio.to_thread 包裹调用。
已知失败用 EmailLibError 抛出（message 为对用户安全的固定文案），
未知异常原样抛出，交由工具层 sanitize_error 脱敏。
"""

import base64
import email
import imaplib
import re
import smtplib
from contextlib import contextmanager
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

from loguru import logger

from src.models.user import UserEmail

# 连接超时（秒）：IMAP/SMTP 建连统一 30s，避免网络挂起时请求永久阻塞
CONNECT_TIMEOUT = 30


class EmailLibError(Exception):
    """邮件逻辑层已知错误（message 为对用户安全的固定文案，可直接透传）"""


def _enc_value(enc: Any) -> str:
    """归一化加密协议取值（兼容 EncryptionType 枚举与字符串）"""
    value = getattr(enc, "value", enc)
    return str(value or "").lower()


def _split_addresses(value) -> Tuple[str, List[str]]:
    """
    归一化收件人/抄送人输入

    支持字符串（逗号分隔多个）或列表，返回 (展示用字符串, 去空白后的地址列表)
    """
    if isinstance(value, list):
        recipients = [str(addr).strip() for addr in value if str(addr).strip()]
    else:
        recipients = [addr.strip() for addr in str(value or "").split(",") if addr.strip()]
    return ", ".join(recipients), recipients


# ============== SMTP 发送 ==============

def send_email(user_email: UserEmail, to, subject: str, body: str, cc="") -> Dict[str, Any]:
    """
    同步发送邮件（失败抛异常，由工具层统一脱敏）

    Args:
        user_email: 用户邮箱配置
        to: 收件人（字符串或列表）
        subject: 邮件主题
        body: 邮件正文
        cc: 抄送人（字符串或列表，可选）

    Returns:
        发送详情（from/to/cc/subject，to/cc 为归一化后的展示字符串）
    """
    msg = MIMEMultipart()
    msg["From"] = user_email.email_address
    msg["Subject"] = subject

    to_str, recipients = _split_addresses(to)
    if not recipients:
        # to 为空白/纯逗号等归一化后无有效地址：必现失败，直接抛已知错误，
        # 避免 SMTPRecipientsRefused 落入兜底文案"请稍后重试"误导重试
        raise EmailLibError("收件人地址无效，请检查收件人邮箱地址")
    msg["To"] = to_str

    cc_str = ""
    if cc:
        cc_str, cc_recipients = _split_addresses(cc)
        msg["Cc"] = cc_str
        recipients.extend(cc_recipients)

    msg.attach(MIMEText(body, "plain", "utf-8"))

    enc = _enc_value(user_email.smtp_encryption)
    if enc == "ssl":
        # SSL/TLS 加密连接（with 保证连接释放）
        with smtplib.SMTP_SSL(
            user_email.smtp_server,
            user_email.smtp_port,
            timeout=CONNECT_TIMEOUT,
        ) as server:
            server.login(user_email.smtp_user, user_email.smtp_password)
            server.sendmail(user_email.email_address, recipients, msg.as_string())
    elif enc == "tls":
        # STARTTLS 加密连接
        with smtplib.SMTP(
            user_email.smtp_server,
            user_email.smtp_port,
            timeout=CONNECT_TIMEOUT,
        ) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user_email.smtp_user, user_email.smtp_password)
            server.sendmail(user_email.email_address, recipients, msg.as_string())
    else:
        # 无加密连接（不推荐）
        with smtplib.SMTP(
            user_email.smtp_server,
            user_email.smtp_port,
            timeout=CONNECT_TIMEOUT,
        ) as server:
            server.login(user_email.smtp_user, user_email.smtp_password)
            server.sendmail(user_email.email_address, recipients, msg.as_string())

    return {
        "from": user_email.email_address,
        "to": to_str,
        "cc": cc_str,
        "subject": subject,
    }


# ============== IMAP 连接与读取 ==============

def _create_imap(user_email: UserEmail, timeout: int = CONNECT_TIMEOUT):
    """按加密协议建立 IMAP 连接（不登录）"""
    enc = _enc_value(user_email.imap_encryption)
    if enc == "ssl":
        return imaplib.IMAP4_SSL(user_email.imap_server, user_email.imap_port, timeout=timeout)
    mail = imaplib.IMAP4(user_email.imap_server, user_email.imap_port, timeout=timeout)
    if enc == "tls":
        mail.starttls()
    return mail


@contextmanager
def imap_session(user_email: UserEmail, timeout: int = CONNECT_TIMEOUT):
    """
    IMAP 会话上下文：建立连接并登录，退出时保证 close/logout（防连接泄漏）

    无论正常返回还是异常，都会尝试 close + logout 释放服务器连接。
    """
    mail = _create_imap(user_email, timeout)
    try:
        imap_user, imap_password = user_email.get_imap_credentials()
        mail.login(imap_user, imap_password)
        yield mail
    finally:
        # 连接泄漏修复：close/logout 必须放 finally，异常路径也要释放
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass


def list_folders(mail) -> List[Dict[str, Any]]:
    """
    列出所有邮件文件夹（仅名称，不逐文件夹 select/search 统计，避免拖慢读取热路径）

    Returns:
        [{"name": 解码后名称, "original_name": 服务器原始名称}]，失败返回 []
    """
    try:
        status, folders = mail.list()
    except Exception as e:
        logger.warning(f"列出邮件文件夹失败: {e}")
        return []

    if status != "OK":
        return []

    result = []
    for raw in folders or []:
        if not raw:
            continue
        folder_str = raw.decode() if isinstance(raw, bytes) else str(raw)
        # LIST 响应形如: (\HasNoChildren) "/" "INBOX"，按引号切分取名称段
        parts = folder_str.split('"')
        if len(parts) < 3:
            continue
        folder_name = parts[-2] if parts[-2] else parts[-1].strip()
        if not folder_name:
            continue
        result.append({
            "name": decode_imap_folder_name(folder_name),
            "original_name": folder_name,
        })
    return result


def search_uids(mail, unseen_only: bool = False) -> List[bytes]:
    """
    服务器端 UID 搜索（仅 ASCII 安全条件）

    Args:
        mail: 已登录并 select 过文件夹的 IMAP 连接
        unseen_only: 是否只搜未读

    Returns:
        UID 列表（bytes，升序，旧→新）
    """
    criteria = "UNSEEN" if unseen_only else "ALL"
    status, messages = mail.uid("search", None, criteria)
    if status != "OK":
        raise EmailLibError("搜索邮件失败")

    # 部分服务器空结果时返回 [None]，需同时防 messages 为空与首元素为 None
    data = messages[0] if messages and messages[0] else b""
    return data.split() if data else []


def _parse_message_bytes(msg_data) -> Optional[email.message.Message]:
    """从 fetch 响应中提取原始邮件字节并解析为 Message"""
    for part in msg_data or []:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
            return email.message_from_bytes(part[1])
    return None


def fetch_header_fields(mail, uid: bytes) -> Optional[email.message.Message]:
    """
    两段式过滤第一段：只拉取指定头字段（SUBJECT/FROM/TO/DATE），用于本地过滤

    BODY.PEEK 不会将邮件标记为已读。
    """
    status, msg_data = mail.uid(
        "fetch", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])"
    )
    if status != "OK":
        logger.warning(f"获取邮件头 UID:{uid} 失败: {status}")
        return None
    return _parse_message_bytes(msg_data)


def _parse_internaldate(msg_data) -> str:
    """从 fetch 响应中提取 INTERNALDATE（服务器收到时间）

    INTERNALDATE 可能出现在 tuple[0]（元数据先于 BODY 字面量的主流服务器布局），
    也可能出现在响应尾部的 bytes 段（INTERNALDATE 排在 BODY 之后），两处都要扫。
    """
    for part in msg_data or []:
        chunks = part if isinstance(part, tuple) else (part,)
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                continue
            m = re.search(rb'INTERNALDATE\s+"([^"]+)"', chunk)
            if m:
                try:
                    dt = parsedate_to_datetime(m.group(1).decode(errors="ignore"))
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    return m.group(1).decode(errors="ignore")
    return ""


def fetch_full_message(mail, uid: bytes) -> Tuple[Optional[email.message.Message], str]:
    """
    拉取全信（BODY.PEEK[] + INTERNALDATE）

    Returns:
        (解析后的 Message, INTERNALDATE 字符串)，失败返回 (None, "")
    """
    status, msg_data = mail.uid("fetch", uid, "(BODY.PEEK[] INTERNALDATE)")
    if status != "OK":
        logger.warning(f"获取邮件 UID:{uid} 失败: {status}")
        return None, ""
    return _parse_message_bytes(msg_data), _parse_internaldate(msg_data)


def read_emails(
    user_email: UserEmail,
    folder: str = "INBOX",
    limit: int = 10,
    unseen_only: bool = False,
    from_filter: str = "",
    subject_filter: str = "",
    body_preview_len: int = 500,
    timeout: int = CONNECT_TIMEOUT,
) -> Dict[str, Any]:
    """
    同步读取邮件（含两段式过滤与文件夹列表）

    带 from_filter/subject_filter 时：先对候选 UID 逐个只拉头字段（本地过滤），
    命中的才拉全信，避免对未命中邮件拉取整封原始字节（含附件）。
    无过滤时：直接按 limit 拉全信。

    Returns:
        {"success": True, "emails": [...], "folders": [...], "count": n, "message", "folder"}
        或 {"success": False, "error": 固定安全文案}
    """
    with imap_session(user_email, timeout) as mail:
        status, _data = mail.select(folder)
        if status != "OK":
            return {"success": False, "error": f"无法打开邮件文件夹: {folder}"}

        # 文件夹列表顺手返回（原 email_list_folders 工具能力并入 read）
        folders = list_folders(mail)

        uids = search_uids(mail, unseen_only=unseen_only)
        total_found = len(uids)

        has_filter = bool(from_filter or subject_filter)
        if has_filter:
            # 需要本地过滤：多拉候选（limit*3），过滤后取前 limit 封
            fetch_limit = limit * 3
        else:
            fetch_limit = min(limit, total_found)

        candidates = uids[-fetch_limit:] if fetch_limit > 0 else []
        logger.info(
            f"邮件读取: folder={folder}, 服务器共 {total_found} 封, "
            f"候选 {len(candidates)} 封, 过滤={'是' if has_filter else '否'}"
        )

        emails = []
        for uid in reversed(candidates):  # 新→旧
            if len(emails) >= limit:
                break

            if has_filter:
                # 两段式第一段：只拉头字段本地过滤
                header_msg = fetch_header_fields(mail, uid)
                if header_msg is None:
                    continue
                h_subject = decode_header_value(header_msg.get("Subject", ""))
                h_from = decode_header_value(header_msg.get("From", ""))
                if from_filter and from_filter.lower() not in h_from.lower():
                    continue
                if subject_filter and subject_filter.lower() not in h_subject.lower():
                    continue
                # 命中过滤 → 两段式第二段：拉全信
                full_msg, internal_date = fetch_full_message(mail, uid)
            else:
                full_msg, internal_date = fetch_full_message(mail, uid)
                h_subject = ""
                h_from = ""

            if full_msg is None:
                continue
            if not has_filter:
                h_subject = decode_header_value(full_msg.get("Subject", ""))
                h_from = decode_header_value(full_msg.get("From", ""))

            body = get_email_body(full_msg)
            # 优先使用 INTERNALDATE（服务器收到时间），其次用 Date 头
            date_str = internal_date or decode_header_value(full_msg.get("Date", ""))

            emails.append({
                "uid": uid.decode(),
                "subject": h_subject,
                "from": h_from,
                "to": decode_header_value(full_msg.get("To", "")),
                "date": date_str,
                "body_preview": body[:body_preview_len] if body else "",
            })

        logger.info(f"邮件读取完成: 返回 {len(emails)} 封 (folder={folder})")

        return {
            "success": True,
            "emails": emails,
            "folders": folders,
            "count": len(emails),
            "message": f"成功收取{len(emails)}封邮件",
            "folder": folder,
        }


# ============== 邮件解析 ==============

def decode_imap_folder_name(name: str) -> str:
    """
    解码 IMAP 文件夹名称 (modified UTF-7)

    IMAP 使用 modified UTF-7 编码非ASCII字符
    例如: &XfJT0ZAB- 解码后为 "已发送"
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


def decode_header_value(value) -> str:
    """解码邮件头部值（=?charset?B/Q?...?= 编码）"""
    if not value:
        return ""

    # 确保输入是字符串（Header 对象需要先转 str）
    if not isinstance(value, str):
        value = str(value)

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


def _decode_part(part) -> str:
    """解码单个邮件部分的 payload（按 part 自身 charset，缺省 utf-8）"""
    try:
        payload = part.get_payload(decode=True)
        if not payload:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="ignore")
    except Exception:
        return ""


# 去除 script/style 块（内容与标签一起去掉）
_HTML_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
# 块级/换行标签转为换行符，保留文本结构
_HTML_BREAK_RE = re.compile(r"(?i)<(br|/p|/div|/tr|/li|/h[1-6])[^>]*>")
# 其余标签直接删除
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    """
    HTML 正文转纯文本（正则去标签简版，不引入新依赖）

    Returns:
        去标签、解码 HTML 实体、压缩空白后的纯文本
    """
    if not html:
        return ""
    text = _HTML_SCRIPT_STYLE_RE.sub("", html)
    text = _HTML_BREAK_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub("", text)
    text = unescape(text)
    # 压缩行内空白与 3 连以上换行
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_email_body(msg) -> str:
    """
    获取邮件正文：优先 text/plain，无则回退 text/html（去标签转纯文本）

    跳过附件（Content-Disposition 含 attachment）。
    """
    plain = ""
    html = ""

    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        content_disposition = str(part.get("Content-Disposition", ""))
        if "attachment" in content_disposition:
            continue

        content_type = part.get_content_type()
        if content_type == "text/plain" and not plain:
            plain = _decode_part(part)
        elif content_type == "text/html" and not html:
            html = _decode_part(part)

    if plain:
        return plain
    if html:
        return html_to_text(html)
    return ""
