"""确定性人工需求检测器（Phase 3R）。

在导航后首个 snapshot、每步执行后以及接受 LLM ``done`` 前检查结构信号，命中
验证码 / 登录 / MFA / 扫码 / 文件选择器时强制转人工。LLM 的 ``done``、自由文本
"请人工登录" 或普通成功结果不能覆盖结构化证据。

只读取脱敏结构（element role/label/element_type、challenge iframe、page_text
关键词），不读取或记录输入值。SnapshotElement 已冻结且禁止额外字段，检测器
在 snapshot 字典之上操作，不扩展 DTO。
"""

from __future__ import annotations

from typing import Any

# 挑战 / 验证码标记，与 worker_main._CHALLENGE_IFRAME_MARKERS 对齐（含图像验证码控件）
CAPTCHA_MARKERS = (
    "captcha", "recaptcha", "hcaptcha", "turnstile", "geetest",
    "验证码", "校验码", "人机验证", "安全验证", "滑动验证", "图形验证",
)

# 密码字段标记
PASSWORD_MARKERS = ("密码", "口令", "password", "passwd", "pwd")

# MFA / 二步验证 / 扫码 标记
MFA_MARKERS = (
    "二步验证", "两步验证", "动态码", "验证器", "mfa", "otp", "totp",
    "扫码", "二维码", "qrcode", "scan",
)

# 文件选择器标记
FILE_PICKER_MARKERS = ("文件上传", "选择文件", "file", "upload", "附件", "attachment")


def _element_signature(item: dict[str, Any]) -> str:
    """合并元素脱敏字段为小写串，用于关键词匹配。不包含输入值。"""
    parts = [
        str(item.get("label") or ""),
        str(item.get("role") or ""),
        str(item.get("element_type") or ""),
        str(item.get("tag") or ""),
        str(item.get("name") or ""),
    ]
    return " ".join(parts).lower()


def detect_human_requirement(snapshot: dict[str, Any]) -> str | None:
    """根据 snapshot 结构信号返回人工需求 reason_code，无命中返回 None。

    返回值：``CAPTCHA_REQUIRED`` / ``AUTH_REQUIRED`` / ``MFA_REQUIRED`` /
    ``FILE_PICKER_REQUIRED`` / ``None``。

    优先级：CAPTCHA > FILE_PICKER > MFA > AUTH。CAPTCHA 最具体（登录页常为
    用户名+密码+图形验证码），先返回，避免被 AUTH 笼统覆盖。
    """
    if not snapshot:
        return None

    elements = snapshot.get("interactive_elements") or []
    element_sigs = [_element_signature(item) for item in elements]

    challenge_iframe = bool(snapshot.get("challenge_iframe_present"))
    page_text = str(snapshot.get("page_text") or "").lower()
    title = str(snapshot.get("title") or "").lower()
    text_blob = f"{title} {page_text}"

    has_captcha_control = any(
        any(marker in sig for marker in CAPTCHA_MARKERS) for sig in element_sigs
    ) or any(marker in text_blob for marker in CAPTCHA_MARKERS)
    has_captcha = challenge_iframe or has_captcha_control

    has_file_picker = any(
        any(marker in sig for marker in FILE_PICKER_MARKERS) for sig in element_sigs
    )

    has_mfa = any(marker in text_blob for marker in MFA_MARKERS) or any(
        any(marker in sig for marker in MFA_MARKERS) for sig in element_sigs
    )

    # CAPTCHA：挑战 iframe 或验证码控件命中即转人工（最具体，优先）
    if has_captcha:
        return "CAPTCHA_REQUIRED"

    # 文件选择器：无法自动处理，需用户本机选择
    if has_file_picker:
        return "FILE_PICKER_REQUIRED"

    # MFA / 扫码：二步验证或扫码结构
    if has_mfa:
        return "MFA_REQUIRED"

    # AUTH_REQUIRED 不在此主动产出：单独密码字段或单独"登录"链接不误报（设计
    # §7.2）。密码 + 验证码组合已由上方 CAPTCHA_REQUIRED 覆盖；纯登录表单由
    # LLM ask_user 或显式场景触发。保留 reason_code 常量供 human_control 模板。
    return None
