"""
email_process 附件能力单测（Phase 3 / D21 / D22）

覆盖：
- download_attachments action：参数校验、单/多附件落盘、RFC2231 中文文件名解码、
  超大附件跳过（>25MB）、filenames 过滤、filedumps 可重复（不标已读走 BODY.PEEK）
- read 返回 attachments 元信息（D21）
- read 的 since 参数走 IMAP 服务器端 SINCE 条件（ASCII 可服务端过滤）
- 纯函数：format_imap_date / list_attachments / 文件名净化
"""

import email as email_module
import os
import shutil
import tempfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.email]
from src.models.user import UserEmail, EncryptionType
from src.tools.email import EmailProcessTool
from src.tools.email import email_lib

from tests.unit.tools.test_email_tool import make_imap_mock  # 复用 IMAP mock 构造

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def user_email():
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


@pytest.fixture
def storage_dl_dir():
    """工具层 download_dir 安全校验只允许项目 storage 内目录：在 storage/tmp 下开隔离临时目录"""
    base = REPO_ROOT / "storage" / "tmp" / "test_email_att_dl"
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=str(base)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def make_raw_email_with_attachments(
    subject="8月增减员报表",
    attachments=None,
):
    """构造带附件的原始邮件字节。

    attachments: [(filename, payload_bytes, rfc2231)]，rfc2231=True 用
    add_header 三元组（charset, lang, value）编码中文文件名（RFC2231 分片）。
    """
    msg = MIMEMultipart()
    msg.attach(MIMEText("8月社保增减员名单见附件", "plain", "utf-8"))
    for item in attachments or []:
        filename, payload = item[0], item[1]
        rfc2231 = item[2] if len(item) > 2 else False
        part = MIMEApplication(payload)
        if rfc2231:
            part.add_header("Content-Disposition", "attachment",
                            filename=("utf-8", "", filename))
        else:
            part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
    msg["Subject"] = subject
    msg["From"] = "hr@company.com"
    msg["To"] = "me@example.com"
    msg["Date"] = "Mon, 18 Aug 2026 10:00:00 +0800"
    return msg.as_bytes()


def make_imap_mock_with_attachments(messages):
    """附件版 IMAP mock：uid fetch 全信返回带附件原始字节"""
    mail = MagicMock()
    mail.select.return_value = ("OK", [str(len(messages)).encode()])
    mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])

    def uid_side_effect(command, *args):
        if command == "search":
            return ("OK", [b" ".join(m["uid"] for m in messages)])
        if command == "fetch":
            uid = args[0]
            msg = next((m for m in messages if m["uid"] == uid), None)
            if msg is None:
                return ("OK", [b")"])
            raw = msg["raw"]
            return ("OK", [
                (b'1 (INTERNALDATE "18-Aug-2026 10:00:00 +0800" BODY[] {'
                 + str(len(raw)).encode() + b"}", raw),
                b")",
            ])
        return ("OK", [b""])

    mail.uid.side_effect = uid_side_effect
    return mail


# ============== 纯函数 ==============


class TestAttachmentParsers:
    def test_format_imap_date(self):
        assert email_lib.format_imap_date("2026-08-01") == "01-Aug-2026"
        with pytest.raises(email_lib.EmailLibError):
            email_lib.format_imap_date("2026/08/01")

    def test_format_imap_date_english_months_locale_safe(self):
        """12 个月全映射为英文缩写（strftime %b 随进程 locale 变化，zh_CN 下会得 '8月'）"""
        expected = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        for month, en in enumerate(expected, 1):
            assert email_lib.format_imap_date(f"2026-{month:02d}-01") == \
                f"01-{en}-2026", f"month={month}"

    def test_list_attachments_meta(self):
        raw = make_raw_email_with_attachments(attachments=[
            ("report.xlsx", b"x" * 100),
        ])
        msg = email_module.message_from_bytes(raw)
        atts = email_lib.list_attachments(msg)
        assert atts == [{"filename": "report.xlsx",
                         "content_type": "application/octet-stream", "size": 100}]

    def test_list_attachments_rfc2231_chinese_filename(self):
        raw = make_raw_email_with_attachments(attachments=[
            ("202608增减人员报表.xlsx", b"y" * 10, True),
        ])
        atts = email_lib.list_attachments(email_module.message_from_bytes(raw))
        assert atts[0]["filename"] == "202608增减人员报表.xlsx"

    def test_sanitize_attachment_filename(self):
        assert email_lib._sanitize_attachment_filename("../evil.xlsx") == "evil.xlsx"
        assert email_lib._sanitize_attachment_filename("a/../../b.xlsx") == "b.xlsx"
        assert email_lib._sanitize_attachment_filename("..") == ""
        assert email_lib._sanitize_attachment_filename("正常名.xlsx") == "正常名.xlsx"


# ============== read 增强（D21） ==============


class TestReadAttachmentsMeta:
    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_returns_attachments_meta(self, mock_imap_ssl, user_email):
        raw = make_raw_email_with_attachments(attachments=[
            ("report.xlsx", b"x" * 50),
            ("名单.pdf", b"z" * 30),
        ])
        mock_imap_ssl.return_value = make_imap_mock_with_attachments([
            {"uid": b"7", "raw": raw},
        ])
        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=1)
        assert result["success"] is True
        atts = result["emails"][0]["attachments"]
        assert [a["filename"] for a in atts] == ["report.xlsx", "名单.pdf"]
        assert atts[0]["size"] == 50 and atts[1]["content_type"] == "application/octet-stream"

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_since_uses_server_side_criteria(self, mock_imap_ssl, user_email):
        mail = make_imap_mock([
            {"uid": b"1", "subject": "a", "from_": "a@example.com",
             "raw": make_raw_email_with_attachments()},
        ])
        mock_imap_ssl.return_value = mail
        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=1, since="2026-08-01")
        assert result["success"] is True
        search_args = [c.args for c in mail.uid.call_args_list if c.args[0] == "search"]
        assert search_args and search_args[0][-1] == "SINCE 01-Aug-2026"

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_since_combined_with_unseen(self, mock_imap_ssl, user_email):
        mail = make_imap_mock([
            {"uid": b"1", "subject": "a", "from_": "a@example.com",
             "raw": make_raw_email_with_attachments()},
        ])
        mock_imap_ssl.return_value = mail
        tool = EmailProcessTool(user_email)
        await tool.execute(action="read", limit=1, unseen_only=True, since="2026-08-01")
        search_args = [c.args for c in mail.uid.call_args_list if c.args[0] == "search"]
        assert search_args[0][-1] == "UNSEEN SINCE 01-Aug-2026"

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_read_invalid_since_safe_error(self, mock_imap_ssl, user_email):
        mock_imap_ssl.return_value = make_imap_mock([])
        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="read", limit=1, since="08/2026")
        assert result["success"] is False
        assert "since 参数格式错误" in result["error"]


# ============== download_attachments action（D22） ==============


class TestDownloadAttachments:
    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_attachments_basic(self, mock_imap_ssl, user_email, storage_dl_dir):
        raw = make_raw_email_with_attachments(attachments=[
            ("report.xlsx", b"A" * 100),
            ("202608增减人员报表.xlsx", b"B" * 200, True),  # RFC2231 中文名
        ])
        mock_imap_ssl.return_value = make_imap_mock_with_attachments([
            {"uid": b"7", "raw": raw},
        ])
        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="download_attachments", uid="7", download_dir=str(storage_dl_dir)
        )
        assert result["success"] is True
        assert result["uid"] == "7"
        assert len(result["files"]) == 2
        # RFC2231 中文文件名解码后落盘
        names = {f["filename"] for f in result["files"]}
        assert names == {"report.xlsx", "202608增减人员报表.xlsx"}
        for f in result["files"]:
            assert os.path.exists(f["path"])
            assert os.path.getsize(f["path"]) == f["size"]
        assert result["skipped"] == []

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_oversize_skipped(self, mock_imap_ssl, user_email, tmp_path):
        """单附件 >25MB 跳过并标注（用小上限参数化验证同一分支，25MB 默认值见常量）"""
        assert email_lib.MAX_ATTACHMENT_SIZE == 25 * 1024 * 1024
        raw = make_raw_email_with_attachments(attachments=[
            ("小文件.xlsx", b"A" * 100),
            ("超大文件.xlsx", b"B" * 400),
        ])
        mock_imap_ssl.return_value = make_imap_mock_with_attachments([
            {"uid": b"9", "raw": raw},
        ])
        with patch.object(email_lib, "MAX_ATTACHMENT_SIZE", 200):
            # 默认上限走模块常量；这里直接调库层用小 max_size 验证同一逻辑
            result = email_lib.download_attachments(
                user_email, "9", str(tmp_path), max_size=200
            )
        assert result["success"] is True
        assert [f["filename"] for f in result["files"]] == ["小文件.xlsx"]
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["filename"] == "超大文件.xlsx"
        assert "超过" in result["skipped"][0]["reason"]
        assert not (tmp_path / "超大文件.xlsx").exists()

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_full_25mb_boundary(self, mock_imap_ssl, user_email, storage_dl_dir):
        """真实 25MB 边界：恰好 25MB 放行、25MB+1 跳过（默认上限路径）"""
        raw = make_raw_email_with_attachments(attachments=[
            ("恰25MB.xlsx", b"A" * (25 * 1024 * 1024)),
            ("超1字节.xlsx", b"B" * (25 * 1024 * 1024 + 1)),
        ])
        mock_imap_ssl.return_value = make_imap_mock_with_attachments([
            {"uid": b"10", "raw": raw},
        ])
        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="download_attachments", uid="10", download_dir=str(storage_dl_dir)
        )
        assert result["success"] is True
        assert [f["filename"] for f in result["files"]] == ["恰25MB.xlsx"]
        assert [s["filename"] for s in result["skipped"]] == ["超1字节.xlsx"]

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_filenames_filter(self, mock_imap_ssl, user_email, storage_dl_dir):
        raw = make_raw_email_with_attachments(attachments=[
            ("report.xlsx", b"A" * 10),
            ("名单.pdf", b"B" * 10),
            ("增减表.xlsx", b"C" * 10),
        ])
        mock_imap_ssl.return_value = make_imap_mock_with_attachments([
            {"uid": b"11", "raw": raw},
        ])
        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="download_attachments", uid="11", download_dir=str(storage_dl_dir),
            filenames=["report.xlsx", "增减表.xlsx"],
        )
        assert result["success"] is True
        assert sorted(f["filename"] for f in result["files"]) == ["report.xlsx", "增减表.xlsx"]

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_uses_body_peek(self, mock_imap_ssl, user_email, storage_dl_dir):
        """D24：下载走 BODY.PEEK，不把邮件标已读"""
        raw = make_raw_email_with_attachments(attachments=[("r.xlsx", b"A" * 10)])
        mail = make_imap_mock_with_attachments([{"uid": b"12", "raw": raw}])
        mock_imap_ssl.return_value = mail
        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="download_attachments", uid="12", download_dir=str(storage_dl_dir)
        )
        assert result["success"] is True
        fetch_args = [c.args for c in mail.uid.call_args_list if c.args[0] == "fetch"]
        assert fetch_args and all("BODY.PEEK[]" in a[2] for a in fetch_args)
        # 无 STORE/SEEN 操作
        assert not [c for c in mail.uid.call_args_list if c.args[0] == "store"]

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_missing_params(self, mock_imap_ssl, user_email):
        tool = EmailProcessTool(user_email)
        result = await tool.execute(action="download_attachments", uid="1")
        assert result["success"] is False
        assert "必填" in result["error"]

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_dir_outside_storage_rejected(
        self, mock_imap_ssl, user_email, tmp_path
    ):
        """安全（LLM 传参不可信）：download_dir 指向 storage 外（系统目录/tmp）直接拒绝"""
        from src.tools.email import email_tool

        tool = EmailProcessTool(user_email)
        for bad in ("/etc", "/tmp/evil", str(tmp_path), "../../etc"):
            result = await tool.execute(
                action="download_attachments", uid="1", download_dir=bad
            )
            assert result["success"] is False, bad
            assert "超出允许范围" in result["error"], bad
        # 校验失败不触发任何 IMAP 连接
        mock_imap_ssl.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_dir_relative_resolved_under_storage(
        self, mock_imap_ssl, user_email
    ):
        """相对路径挂到项目 storage 下解析（与会话工作目录用法一致）"""
        from src.tools.email import email_tool

        resolved = email_tool._resolve_download_dir("test_att/x")
        assert resolved.startswith(str((REPO_ROOT / "storage").resolve()))
        assert ".." not in Path(resolved).parts

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_uid_not_found(self, mock_imap_ssl, user_email, storage_dl_dir):
        mock_imap_ssl.return_value = make_imap_mock_with_attachments([])
        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="download_attachments", uid="999", download_dir=str(storage_dl_dir)
        )
        assert result["success"] is False
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    @patch("src.tools.email.email_lib.imaplib.IMAP4_SSL")
    async def test_download_same_name_dedup(self, mock_imap_ssl, user_email, storage_dl_dir):
        """同名附件不互相覆盖：追加数字后缀"""
        raw = make_raw_email_with_attachments(attachments=[
            ("same.xlsx", b"A" * 10),
            ("same.xlsx", b"B" * 20),
        ])
        mock_imap_ssl.return_value = make_imap_mock_with_attachments([
            {"uid": b"13", "raw": raw},
        ])
        tool = EmailProcessTool(user_email)
        result = await tool.execute(
            action="download_attachments", uid="13", download_dir=str(storage_dl_dir)
        )
        assert result["success"] is True
        saved = sorted(os.listdir(storage_dl_dir))
        assert saved == ["same(1).xlsx", "same.xlsx"]

    @pytest.mark.asyncio
    async def test_download_error_sanitized(self, user_email, storage_dl_dir):
        """未知异常走兜底文案，不泄漏 str(e)"""
        with patch.object(email_lib, "download_attachments",
                          side_effect=RuntimeError("boom with secret_token")):
            tool = EmailProcessTool(user_email)
            result = await tool.execute(
                action="download_attachments", uid="1", download_dir=str(storage_dl_dir)
            )
        assert result["success"] is False
        assert result["error"] == "附件下载失败，请稍后重试"
        assert "secret_token" not in result["error"]

    def test_display_name_download(self, user_email):
        tool = EmailProcessTool(user_email)
        assert tool.get_display_name({"action": "download_attachments", "uid": "7"}) == \
            "下载邮件附件（UID 7）"
        assert tool.get_display_name({"action": "download_attachments"}) == "下载邮件附件"
