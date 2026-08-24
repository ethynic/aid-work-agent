"""wenxin_collect.py（文心联网采集子脚本）IO 协议与提问模板单测。

collect_one 涉及 Playwright/CDP DOM，由端到端验证（真实浏览器），
本文件只测纯逻辑：QUERY_TMPL 拼装、main() 的 stdin/stdout JSON 协议、
失败时 stderr 只输出 `wenxin_collect_failed:{Type}`（不泄漏原文/证据）。
"""

import importlib.util
import io
import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

WENXIN_PATH = (
    Path(__file__).resolve().parents[3]
    / "clients"
    / "association-client-cli"
    / "scripts"
    / "wenxin_collect.py"
)


def _load_wenxin():
    spec = importlib.util.spec_from_file_location("wenxin_collect", WENXIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_query_template_includes_all_required_fields():
    """QUERY_TMPL 必须覆盖 search_profile 需要的全部基础信息字段。"""
    wenxin = _load_wenxin()
    query = wenxin.QUERY_TMPL.format(name="中国黄金协会")
    assert "中国黄金协会" in query
    # 与 search_profile 的 search_fields 对应（官网直接给网址，禁止超链接）
    for keyword in (
        "地址", "邮箱", "官网", "主管单位", "单位等级",
        "会员数量", "分支机构", "公众号", "品牌会议",
        "秘书长", "会员服务", "办公室",
    ):
        assert keyword in query, f"QUERY_TMPL 缺少字段关键词：{keyword}"


def test_main_emits_single_line_json_on_success(monkeypatch):
    """成功时 stdout 仅输出一行压缩 JSON（{"ok","answer","note"}）。"""
    wenxin = _load_wenxin()

    async def fake_collect(name):
        assert name == "测试协会"
        return {"ok": True, "answer": "地址：北京市\n官网网址：http://x", "note": ""}

    monkeypatch.setattr(wenxin, "collect_one", fake_collect)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        json.dumps({"association_name": "测试协会"})
    ))
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    rc = wenxin.main()

    assert rc == 0
    lines = captured.getvalue().splitlines()
    assert len(lines) == 1, "stdout 必须只有一行 JSON"
    payload = json.loads(lines[0])
    assert payload["ok"] is True
    assert payload["answer"] == "地址：北京市\n官网网址：http://x"


def test_main_emits_failed_type_on_exception(monkeypatch):
    """collect_one 抛异常时，stderr 只输出异常类型，不携带原文/证据。"""
    wenxin = _load_wenxin()

    async def fake_collect(name):
        raise RuntimeError("boom 含敏感原文")

    monkeypatch.setattr(wenxin, "collect_one", fake_collect)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        json.dumps({"association_name": "测试协会"})
    ))
    err = io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    monkeypatch.setattr("sys.stdout", io.StringIO())

    rc = wenxin.main()

    assert rc == 2
    assert err.getvalue().strip() == "wenxin_collect_failed:RuntimeError"
    assert "boom" not in err.getvalue()  # 不泄漏异常原文


def test_main_rejects_empty_association_name(monkeypatch):
    """空协会名应 fail-loud（ValueError），不让文心发起空提问。"""
    wenxin = _load_wenxin()
    monkeypatch.setattr("sys.stdin", io.StringIO(
        json.dumps({"association_name": "   "})
    ))
    err = io.StringIO()
    monkeypatch.setattr("sys.stderr", err)
    monkeypatch.setattr("sys.stdout", io.StringIO())

    rc = wenxin.main()

    assert rc == 2
    assert err.getvalue().strip() == "wenxin_collect_failed:ValueError"


def test_collect_one_delegates_to_collect_query(monkeypatch):
    """collect_one 委托 collect_query(QUERY_TMPL.format(name=...))。

    collect_one/collect_query 定义在 runtime.wenxin_collector；本测试 patch
    collector_mod.collect_query 验证委托关系与 query 模板拼接。DOM 逻辑
    （CDP/textarea/稳定判断）继续由端到端验证。
    """
    import asyncio
    import sys

    wenxin = _load_wenxin()  # 触发 runtime.wenxin_collector 加载进 sys.modules
    collector_mod = sys.modules["runtime.wenxin_collector"]
    captured = {}

    async def fake_collect_query(query):
        captured["query"] = query
        return {"ok": True, "answer": "stub", "note": ""}

    monkeypatch.setattr(collector_mod, "collect_query", fake_collect_query)

    result = asyncio.run(wenxin.collect_one("中国黄金协会"))

    assert captured["query"] == wenxin.QUERY_TMPL.format(name="中国黄金协会")
    assert result == {"ok": True, "answer": "stub", "note": ""}
