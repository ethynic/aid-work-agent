"""
邮件工具单元测试

测试 EmailProcessTool（action: send / read 确定性分发，mock 邮件服务器），
覆盖：发送、读取、两段式过滤、HTML 正文回退、folders 字段、body_preview_len、
user_id 注入、action 分发与错误脱敏。
"""

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.email]
from unittest.mock import MagicMock, patch

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib

from src.models.user import UserEmail, EncryptionType
from src.tools.email import EmailProcessTool, create_email_tools
from src.tools.email import email_lib


@pytest.fixture
def user_email():
    """测试邮箱配置"""
    return UserEmail(
        email_address="test@example.com",
        smtp_server="smtp.example.com",
        smtp_port=465,
        smtp_user="test@example.com",
        smtp_password="test_password",
        smtp_encryption=EncryptionType.SSL,
        imap_server="imap.example.com",
        imap_port=993,
        imap_encryption=EncryptionType.SSL,
    )


# ============== 测试辅助 ==============

def make_raw_email(subject="测试邮件", from_="noreply@example.com",
                   to="me@example.com", body="正文内容", html=False):
    """构造原始邮件字节（multipart，html=True 时仅含 text/html 部分）"""
    if html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(f"<html><body><p>{body}</p></body></html>", "html", "utf-8"))
    else:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain", "utf-8"))
    msg["Subject"] = subject
    msg["From"] = from_
    msg["To"] = to
    msg["Date"] = "Mon, 18 Aug 2026 10:00:00 +0800"
    return msg.as_bytes()


def make_imap_mock(messages, fetch_log=None):
    """
    构造已登录的 IMAP 连接 mock

    Args:
        messages: [{"uid": b"1", "subject":..., "from_":..., "to":..., "raw": bytes}]，uid 升序
        fetch_log: 可选列表，记录每次 (uid, fetch_spec) 便于断言两段式行为
    """
    mail = MagicMock()
    mail.select.return_value = ("OK", [str(len(messages)).encode()])
    mail.list.return_value = ("OK", [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren) "/" "Sent Messages"',
    ])

    def uid_side_effect(command, *args):
        if command == "search":
            return ("OK", [b" ".join(m["uid"] for m in messages)])
        if command == "fetch":
            uid = args[0]
            spec = args[1] if len(args) > 1 else ""
            if fetch_log is not None:
                fetch_log.append((uid, spec))
            msg = next((m for m in messages if m["uid"] == uid), None)
            if msg is None:
                return ("OK", [b")"])
            if "HEADER.FIELDS" in spec:
                # 两段式第一段：仅返回头字段
                header = (
                    f"Subject: {msg['subject']}\r\n"
                    f"From: {msg['from_']}\r\n"
                    f"To: {msg.get('to', 'me@example.com')}\r\n"
                    f"Date: Mon, 18 Aug 2026 10:00:00 +0800\r\n\r\n"
                ).encode("utf-8")
                return ("OK", [
                    (b"1 (BODY[HEADER.FIELDS] {" + str(len(header)).encode() + b"}", header),
                    b")",
                ])
            # 全信 fetch（含 INTERNALDATE）
            raw = msg["raw"]
            return ("OK", [
                (b'1 (INTERNALDATE "18-Aug-2026 10:00:00 +0800" BODY[] {'
                 + str(len(raw)).encode() + b"}", raw),
                b")",
            ])
        return ("OK", [b""])

    mail.uid.side_effect = uid_side_effect
    return mail


# ============== send 动作 ==============

class TestEmailProcessSend:
    """邮件发送测试"""

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.smtplib.SMTP_SSL")
    async def test_send_simple_email(self, mock_smtp_ssl, user_email):
        server = MagicMock()
        mock_smtp_ssl.return_value.__enter__.return_value = server
        mock_smtp_ssl.return_value.__exit__.return_value = False

        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="send",
            to="recipient@example.com",
            subject="Test Subject",
            body="Test body content",
        )
        assert result["success"] is True
        # 连接必须带 timeout（防挂起）
        mock_smtp_ssl.assert_called_once_with("smtp.example.com", 465, timeout=30)
        server.login.assert_called_once_with("test@example.com", "test_password")
        server.sendmail.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.smtplib.SMTP_SSL")
    async def test_send_to_list(self, mock_smtp_ssl, user_email):
        server = MagicMock()
        mock_smtp_ssl.return_value.__enter__.return_value = server
        mock_smtp_ssl.return_value.__exit__.return_value = False

        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="send",
            to=["a@example.com", "b@example.com"],
            subject="多收件人",
            body="列表收件人",
        )
        assert result["success"] is True
        # 展示消息使用归一化后的逗号连接
        assert "a@example.com, b@example.com" in result["message"]

    @pytest.mark.asyncio
    async def test_send_missing_to_fails(self, user_email):
        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="send", to="", subject="Test", body="Test body")
        assert result["success"] is False
        assert result["error"] == "收件人和邮件主题为必填项"

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.smtplib.SMTP_SSL")
    async def test_send_error_sanitized(self, mock_smtp_ssl, user_email):
        # 认证失败：白名单命中固定文案，不透传异常原文
        mock_smtp_ssl.side_effect = smtplib.SMTPAuthenticationError(535, b"secret detail")
        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="send", to="x@example.com", subject="s", body="b"
        )
        assert result["success"] is False
        assert result["error"] == "邮箱账号或密码（授权码）错误，请检查邮箱配置"
        assert "535" not in result["error"]
        assert "secret" not in result["error"]

    @pytest.mark.asyncio
    async def test_send_blank_to_rejected(self, user_email):
        """归一化后无有效收件人（空白/纯逗号）必须报明确错误，不得落入'请稍后重试'兜底"""
        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="send", to="  ,,", subject="s", body="b")
        assert result["success"] is False
        assert result["error"] == "收件人地址无效，请检查收件人邮箱地址"
        assert "稍后重试" not in result["error"]

    @pytest.mark.asyncio
    async def test_unbound_email(self):
        tool = EmailProcessTool()
        result = await tool.execute(action="send", to="x@example.com", subject="s", body="b")
        assert result["success"] is False
        assert result["error"] == "未绑定邮箱，请先去设置中绑定邮箱"


# ============== read 动作 ==============

class TestEmailProcessRead:
    """邮件收取测试"""

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_returns_emails_folders_count(self, mock_imap_ssl, user_email):
        mail = make_imap_mock([
            {"uid": b"1", "subject": "第一封", "from_": "a@example.com",
             "raw": make_raw_email(subject="第一封", from_="a@example.com", body="内容一")},
            {"uid": b"2", "subject": "第二封", "from_": "b@example.com",
             "raw": make_raw_email(subject="第二封", from_="b@example.com", body="内容二")},
        ])
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=10)

        # 连接必须带 timeout
        mock_imap_ssl.assert_called_once_with("imap.example.com", 993, timeout=30)
        assert result["success"] is True
        assert result["count"] == 2

        # emails 结构保持既有字段（新→旧）
        assert [e["uid"] for e in result["emails"]] == ["2", "1"]
        first = result["emails"][0]
        assert first["subject"] == "第二封"
        assert first["from"] == "b@example.com"
        assert "to" in first
        # date 优先取 INTERNALDATE（mock 中为 18-Aug-2026，归一化为 yyyy-mm-dd），
        # 而非 Date 头原文 "Mon, 18 Aug 2026 10:00:00 +0800"
        assert first["date"] == "2026-08-18 10:00:00"
        assert first["body_preview"] == "内容二"

        # folders 字段（原 email_list_folders 工具能力并入 read）
        folder_names = [f["name"] for f in result["folders"]]
        assert "INBOX" in folder_names
        assert "Sent Messages" in folder_names

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_limit_returns_newest(self, mock_imap_ssl, user_email):
        mail = make_imap_mock([
            {"uid": b"1", "subject": "旧邮件", "from_": "a@example.com",
             "raw": make_raw_email(subject="旧邮件", from_="a@example.com")},
            {"uid": b"2", "subject": "新邮件", "from_": "b@example.com",
             "raw": make_raw_email(subject="新邮件", from_="b@example.com")},
        ])
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=1)
        assert result["count"] == 1
        assert result["emails"][0]["uid"] == "2"

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_body_preview_len(self, mock_imap_ssl, user_email):
        long_body = "字" * 1000
        mail = make_imap_mock([
            {"uid": b"1", "subject": "长正文", "from_": "a@example.com",
             "raw": make_raw_email(subject="长正文", from_="a@example.com", body=long_body)},
        ])
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        # 参数化截断
        result = await tool.execute(action="read", limit=1, body_preview_len=50)
        assert len(result["emails"][0]["body_preview"]) == 50

        # 默认 500
        result = await tool.execute(action="read", limit=1)
        assert len(result["emails"][0]["body_preview"]) == 500

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_html_body_fallback(self, mock_imap_ssl, user_email):
        """回归：multipart 仅含 text/html 时正文非空且无标签"""
        mail = make_imap_mock([
            {"uid": b"1", "subject": "HTML邮件", "from_": "a@example.com",
             "raw": make_raw_email(subject="HTML邮件", from_="a@example.com",
                                   body="这是HTML正文", html=True)},
        ])
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=1)
        preview = result["emails"][0]["body_preview"]
        assert preview != ""
        assert "<" not in preview and ">" not in preview
        assert "这是HTML正文" in preview

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_two_stage_filter(self, mock_imap_ssl, user_email):
        """回归：带 from_filter 时未命中邮件只发生 HEADER fetch，无全信 fetch"""
        fetch_log = []
        mail = make_imap_mock([
            {"uid": b"1", "subject": "不相关", "from_": "noise@example.com",
             "raw": make_raw_email(subject="不相关", from_="noise@example.com", body="垃圾内容")},
            {"uid": b"2", "subject": "目标邮件", "from_": "boss@company.com",
             "raw": make_raw_email(subject="目标邮件", from_="boss@company.com", body="命中内容")},
        ], fetch_log=fetch_log)
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=5, from_filter="boss@company.com")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["emails"][0]["uid"] == "2"

        # 未命中 UID（1）：只允许 HEADER fetch，不允许全信 fetch
        uid1_specs = [spec for uid, spec in fetch_log if uid == b"1"]
        assert len(uid1_specs) == 1
        assert "HEADER.FIELDS" in uid1_specs[0]
        assert "BODY.PEEK[]" not in uid1_specs[0]

        # 命中 UID（2）：先 HEADER 再全信
        uid2_specs = [spec for uid, spec in fetch_log if uid == b"2"]
        assert len(uid2_specs) == 2
        assert "HEADER.FIELDS" in uid2_specs[0]
        assert "BODY.PEEK[]" in uid2_specs[1]

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_no_filter_single_fetch(self, mock_imap_ssl, user_email):
        """无过滤时直接按 limit 拉全信，每封只 fetch 一次且不带 HEADER-only"""
        fetch_log = []
        mail = make_imap_mock([
            {"uid": b"1", "subject": "a", "from_": "a@example.com",
             "raw": make_raw_email(subject="a", from_="a@example.com")},
            {"uid": b"2", "subject": "b", "from_": "b@example.com",
             "raw": make_raw_email(subject="b", from_="b@example.com")},
        ], fetch_log=fetch_log)
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=10)
        assert result["count"] == 2
        assert len(fetch_log) == 2
        for _uid, spec in fetch_log:
            assert "BODY.PEEK[]" in spec

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_unseen_only_uses_unseen_criteria(self, mock_imap_ssl, user_email):
        mail = make_imap_mock([
            {"uid": b"1", "subject": "a", "from_": "a@example.com",
             "raw": make_raw_email(subject="a", from_="a@example.com")},
        ])
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=1, unseen_only=True)
        assert result["success"] is True
        # 服务器端搜索条件为 UNSEEN
        search_args = [c.args for c in mail.uid.call_args_list if c.args[0] == "search"]
        assert search_args and search_args[0][-1] == "UNSEEN"

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_closes_connection_on_error(self, mock_imap_ssl, user_email):
        """回归：异常路径也必须 close/logout（连接泄漏修复）"""
        mail = MagicMock()
        mail.select.return_value = ("OK", [b"1"])
        mail.list.return_value = ("OK", [])
        mail.uid.side_effect = RuntimeError("boom with secret_token")
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=1)

        assert result["success"] is False
        # 未知异常走兜底文案，不泄漏 str(e)
        assert result["error"] == "邮件收取失败，请稍后重试"
        assert "secret_token" not in result["error"]
        mail.close.assert_called_once()
        mail.logout.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_login_error_sanitized(self, mock_imap_ssl, user_email):
        mail = MagicMock()
        mail.login.side_effect = __import__("imaplib").IMAP4.error("AUTHENTICATIONFAILED (raw)")
        mock_imap_ssl.return_value = mail

        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=1)
        assert result["success"] is False
        assert result["error"] == "邮箱登录或读取失败，请检查邮箱配置"
        assert "AUTHENTICATIONFAILED" not in result["error"]


# ============== action 分发 ==============

class TestActionDispatch:
    """action 确定性分发（send/read 各走对分支，不走 LLM 路由）"""

    @pytest.mark.asyncio
    async def test_dispatch_send(self, user_email):
        with patch.object(email_lib, "send_email", return_value={
            "from": "test@example.com", "to": "x@example.com", "cc": "", "subject": "s"
        }) as mock_send, patch.object(email_lib, "read_emails") as mock_read:
            tool = EmailProcessTool(user_email)
            result = await tool.execute(
                action="send", to="x@example.com", subject="s", body="b"
            )
        assert result["success"] is True
        mock_send.assert_called_once()
        mock_read.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_read(self, user_email):
        with patch.object(email_lib, "read_emails", return_value={
            "success": True, "emails": [], "folders": [], "count": 0
        }) as mock_read, patch.object(email_lib, "send_email") as mock_send:
            tool = EmailProcessTool(user_email)
            result = await tool.execute(
                action="read", folder="INBOX", limit=3, unseen_only=True
            )
        assert result["success"] is True
        mock_read.assert_called_once()
        mock_send.assert_not_called()
        # 参数透传到库层
        kwargs = mock_read.call_args.kwargs
        assert kwargs["folder"] == "INBOX"
        assert kwargs["limit"] == 3
        assert kwargs["unseen_only"] is True

    @pytest.mark.asyncio
    async def test_dispatch_invalid_action(self, user_email):
        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="delete_all", to="x@example.com")
        assert result["success"] is False
        assert "不支持的操作类型" in result["error"]

    @pytest.mark.asyncio
    async def test_read_limit_defensive_clamp(self, user_email):
        """limit 防御：主执行链不做 InputModel 校验，工具层须归一非法值防 TypeError/资源放大"""
        cases = [
            (0, 1),            # 非法小值 → 夹到 1
            (-5, 1),
            ("abc", 10),       # 不可转换 → 默认 10
            (None, 10),
            (100000, 100),     # 超大 → 上限 100
            (3, 3),            # 合法值原样透传
        ]
        for raw, expected in cases:
            with patch.object(email_lib, "read_emails", return_value={
                "success": True, "emails": [], "folders": [], "count": 0
            }) as mock_read:
                tool = EmailProcessTool(user_email)
                await tool.execute(action="read", limit=raw)
            assert mock_read.call_args.kwargs["limit"] == expected, f"limit={raw!r}"

    @pytest.mark.asyncio
    async def test_read_body_preview_len_defensive_clamp(self, user_email):
        """body_preview_len 防御：负数会反向切片，非法值归一"""
        cases = [(-100, 1), ("x", 500), (999999, 5000), (30, 30)]
        for raw, expected in cases:
            with patch.object(email_lib, "read_emails", return_value={
                "success": True, "emails": [], "folders": [], "count": 0
            }) as mock_read:
                tool = EmailProcessTool(user_email)
                await tool.execute(action="read", limit=1, body_preview_len=raw)
            assert mock_read.call_args.kwargs["body_preview_len"] == expected, \
                f"body_preview_len={raw!r}"


# ============== user_id 运行时注入 ==============

class TestUserIdInjection:
    """user_id 注入后从数据库读取邮箱配置（子智能体/多用户场景）"""

    @pytest.mark.asyncio
    async def test_resolve_via_user_id(self, user_email):
        """set_user_id 后 _resolve_user_email 从 DB 读取配置"""
        tool = EmailProcessTool()
        tool.set_user_id("user_123")
        with patch("src.db.email_credential.EmailCredentialDB.get_user_email_model",
                   return_value=user_email) as mock_get:
            assert tool._resolve_user_email() is user_email
            mock_get.assert_called_once_with("user_123")

    @pytest.mark.asyncio
    async def test_unbound_when_no_user_id(self):
        tool = EmailProcessTool()
        assert tool._resolve_user_email() is None

    @pytest.mark.asyncio
    async def test_read_via_user_id_injection(self, user_email):
        """set_user_id 注入后 read 走 DB 解析出的配置"""
        with patch("src.db.email_credential.EmailCredentialDB.get_user_email_model",
                   return_value=user_email), \
             patch.object(email_lib, "read_emails", return_value={
                 "success": True, "emails": [], "folders": [], "count": 0
             }) as mock_read:
            tool = EmailProcessTool()
            tool.set_user_id("user_456")
            result = await tool.execute(action="read", limit=1)
        assert result["success"] is True
        # 库层收到的是 DB 解析出的 UserEmail
        assert mock_read.call_args.args[0] is user_email


# ============== 工具定义与工厂 ==============

class TestToolDefinition:
    """工具定义、动态显示名与工厂函数"""

    def test_tool_definition(self, user_email):
        tool = EmailProcessTool(user_email)
        defn = tool.to_tool_definition()
        assert defn["name"] == "email_process"
        assert "action" in defn["input_schema"]["properties"]

    def test_display_name_send(self, user_email):
        tool = EmailProcessTool(user_email)
        assert tool.get_display_name({"action": "send", "to": "a@b.com"}) == "发送邮件至「a@b.com」"
        assert tool.get_display_name({"action": "send", "to": ["a@b.com", "c@d.com"]}) == \
            "发送邮件至「a@b.com, c@d.com」"

    def test_display_name_read(self, user_email):
        tool = EmailProcessTool(user_email)
        assert tool.get_display_name({"action": "read", "folder": "INBOX", "limit": 10}) == \
            "读取邮件（INBOX，10封）"
        assert tool.get_display_name({}) == "邮件处理"

    def test_create_email_tools_returns_single_tool(self, user_email):
        """三合一后工厂只返回一个 email_process 工具"""
        tools = create_email_tools(user_email)
        assert len(tools) == 1
        assert tools[0].name == "email_process"
        defn = tools[0].to_tool_definition()
        assert "name" in defn and "input_schema" in defn


# ============== 纯函数解析 ==============

class TestEmailLibParsers:
    """email_lib 纯函数：HTML 去标签、文件夹名解码、头部解码"""

    def test_html_to_text(self):
        html = "<div>第一行 <b>加粗</b></div><p>第二行<br/>第三行</p>&amp;符号"
        text = email_lib.html_to_text(html)
        assert "<" not in text and ">" not in text
        assert "第一行 加粗" in text
        assert "第二行" in text and "第三行" in text
        assert "&符号" in text

    def test_html_to_text_strips_script(self):
        html = "<p>正文</p><script>alert('x')</script><style>.a{}</style>"
        text = email_lib.html_to_text(html)
        assert "alert" not in text
        assert "正文" in text

    def test_html_to_text_empty(self):
        assert email_lib.html_to_text("") == ""

    def test_get_email_body_prefers_plain(self):
        import email as email_module
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("<p>HTML内容</p>", "html", "utf-8"))
        msg.attach(MIMEText("纯文本内容", "plain", "utf-8"))
        assert email_lib.get_email_body(email_module.message_from_bytes(msg.as_bytes())) == "纯文本内容"

    def test_get_email_body_html_only(self):
        import email as email_module
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("<p>仅HTML</p>", "html", "utf-8"))
        body = email_lib.get_email_body(email_module.message_from_bytes(msg.as_bytes()))
        assert body == "仅HTML"

    def test_decode_imap_folder_name(self):
        assert email_lib.decode_imap_folder_name("INBOX") == "INBOX"
        assert email_lib.decode_imap_folder_name("&-") == "&"
        # modified UTF-7 编码的中文文件夹名
        assert email_lib.decode_imap_folder_name("&XfJT0ZAB-") == "已发送"

    def test_decode_header_value_encoded(self):
        assert email_lib.decode_header_value("=?utf-8?B?6L+Z5piv5rWL6K+V?=") == "这是测试"
        assert email_lib.decode_header_value("") == ""
