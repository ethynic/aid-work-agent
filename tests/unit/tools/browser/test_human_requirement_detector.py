"""确定性人工需求检测器红灯契约测试（Phase 3R）。

验证含用户名/密码/图形验证码的登录页必须产生 CAPTCHA_REQUIRED，LLM done
或文字"请人工登录"不能覆盖结构化证据。普通内容页不得误报。
"""

import pytest

from src.tools.browser.human_requirement_detector import detect_human_requirement


def _elem(label="", role="", element_type="", tag="", name=""):
    return {"label": label, "role": role, "element_type": element_type, "tag": tag, "name": name}


def test_captcha_login_page_triggers_assistance():
    """真实带图形验证码的登录页必须检测为 CAPTCHA_REQUIRED。"""
    snapshot = {
        "url": "https://example.com/login",
        "title": "用户登录",
        "interactive_elements": [
            _elem(label="用户名", role="textbox", element_type="text"),
            _elem(label="密码", role="textbox", element_type="password"),
            _elem(label="图形验证码", role="textbox", element_type="text"),
        ],
        "challenge_iframe_present": False,
        "page_text": "请输入用户名、密码和图形验证码登录",
    }
    assert detect_human_requirement(snapshot) == "CAPTCHA_REQUIRED"


def test_challenge_iframe_triggers_captcha():
    """reCAPTCHA/turnstile iframe 存在即转人工。"""
    snapshot = {
        "interactive_elements": [],
        "challenge_iframe_present": True,
        "page_text": "",
    }
    assert detect_human_requirement(snapshot) == "CAPTCHA_REQUIRED"


def test_file_picker_triggers_assistance():
    snapshot = {
        "interactive_elements": [_elem(label="文件上传", element_type="file")],
        "challenge_iframe_present": False,
        "page_text": "",
    }
    assert detect_human_requirement(snapshot) == "FILE_PICKER_REQUIRED"


def test_mfa_triggers_assistance():
    snapshot = {
        "interactive_elements": [],
        "challenge_iframe_present": False,
        "page_text": "请打开验证器 App 扫码确认，完成两步验证",
        "title": "二步验证",
    }
    assert detect_human_requirement(snapshot) == "MFA_REQUIRED"


def test_normal_content_page_does_not_trigger():
    """普通内容/搜索结果页不得误报。"""
    snapshot = {
        "interactive_elements": [_elem(label="搜索", role="searchbox", element_type="search")],
        "challenge_iframe_present": False,
        "page_text": "搜索结果列表，共 10 条",
        "title": "搜索结果",
    }
    assert detect_human_requirement(snapshot) is None


def test_plain_login_link_does_not_falsely_trigger():
    """单独"登录"链接或普通密码字段不误报（设计 §7.2）。"""
    snapshot = {
        "interactive_elements": [_elem(label="登录", role="link")],
        "challenge_iframe_present": False,
        "page_text": "欢迎访问，点击登录进入",
    }
    assert detect_human_requirement(snapshot) is None


def test_llm_done_cannot_override_structural_captcha():
    """LLM 文本声称完成不能覆盖结构化证据：检测器只看结构，不看 LLM 输出。

    这对应 orchestrator 在 action=='done' 时先跑检测器、命中则覆盖为 ask_user
    的契约。本测试断言检测器对验证码页始终返回 CAPTCHA_REQUIRED，与是否
    "声称完成"无关。
    """
    snapshot = {
        "interactive_elements": [
            _elem(label="密码", element_type="password"),
            _elem(label="验证码", element_type="text"),
        ],
        "challenge_iframe_present": False,
        "page_text": "请输入验证码",
    }
    # 即使 LLM 返回 done，结构化信号仍在
    assert detect_human_requirement(snapshot) == "CAPTCHA_REQUIRED"


def test_empty_or_none_snapshot_returns_none():
    assert detect_human_requirement({}) is None
    assert detect_human_requirement(None) is None  # type: ignore[arg-type]


def test_captcha_takes_priority_over_file_picker():
    """CAPTCHA 最具体，优先于其它信号。"""
    snapshot = {
        "interactive_elements": [
            _elem(label="图形验证码"),
            _elem(label="文件上传", element_type="file"),
        ],
        "challenge_iframe_present": False,
        "page_text": "",
    }
    assert detect_human_requirement(snapshot) == "CAPTCHA_REQUIRED"
