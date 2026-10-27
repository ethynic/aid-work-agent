"""web-search-pro 技能移植单测：注册发现、脚本与文档完整性、命令路径替换。"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skills

SKILL_DIR = Path(__file__).resolve().parents[2] / "src" / "skills" / "web-search-pro-1.0.0"


def _load_registry():
    from src.core.skill_registry import SkillRegistry

    registry = SkillRegistry()
    registry.load_from_directory(SKILL_DIR.parent)
    return registry


class TestWebSearchProSkill:
    def test_skill_registered(self):
        registry = _load_registry()
        skill = registry.get("web-search-pro")
        assert skill is not None
        assert skill.version == "1.0.0"

    def test_skill_in_master_content_readable(self):
        registry = _load_registry()
        content = registry.get_content("web-search-pro")
        assert content is not None
        assert "pipeline.py" in content
        assert "skill_execute" in content

    def test_scripts_present(self):
        for name in ("web_search.py", "pipeline.py", "render.py"):
            assert (SKILL_DIR / "scripts" / name).is_file()
        assert (SKILL_DIR / "references" / "retrieval-notes.md").is_file()

    def test_process_command_rewrites_script_path(self):
        """skill_execute 的 _process_command 契约：scripts/<name> 替换为技能脚本绝对路径。"""
        import tempfile

        from src.core.skill_executor import SkillExecutionContext, SkillExecutor

        registry = _load_registry()
        executor = SkillExecutor(registry, workspace=Path(tempfile.mkdtemp()))
        context = SkillExecutionContext(
            skill_name="web-search-pro",
            workdir=executor._create_workdir("web-search-pro", "test_session"),
        )
        command = "python scripts/web_search.py search '测试' --json"
        processed = executor._process_command(command, context)
        expected = (SKILL_DIR / "scripts" / "web_search.py").resolve().as_posix()
        assert processed == f"python {expected} search '测试' --json"

    def test_web_search_help_runs_offline(self):
        result = subprocess.run(
            [sys.executable, str(SKILL_DIR / "scripts" / "web_search.py"), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "search" in result.stdout

    def test_pipeline_help_runs_offline(self):
        result = subprocess.run(
            [sys.executable, str(SKILL_DIR / "scripts" / "pipeline.py"), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0

    def test_mask_merge_algorithm(self):
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        try:
            import pipeline
            pages = [
                {"url": "a", "masked_phones": [{"raw": "1391680****", "pattern": "1391680****"}]},
                {"url": "b", "masked_phones": [{"raw": "139****7580", "pattern": "139****7580"}]},
                {"url": "c", "masked_phones": [{"raw": "139******80", "pattern": "139******80"}]},
            ]
            merged = pipeline.merge_masked_phones(pages)
            assert merged and merged[0]["number"] == "13916807580"
            # 位冲突判不同号码，不误合并
            conflict = pipeline.merge_masked_phones([
                {"url": "a", "masked_phones": [{"raw": "1391680****", "pattern": "1391680****"}]},
                {"url": "b", "masked_phones": [{"raw": "138****7580", "pattern": "138****7580"}]},
            ])
            assert not conflict
            # 信息不足不下结论
            insufficient = pipeline.merge_masked_phones([
                {"url": "a", "masked_phones": [{"raw": "132******53", "pattern": "132******53"}]},
            ])
            assert not insufficient
        finally:
            sys.path.pop(0)


class TestBaiduProxyAndWarmCookies:
    """百度通道渠道级代理 + cookie 预热（2026-10-27 风控治理）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        monkeypatch.syspath_prepend(str(SKILL_DIR / "scripts"))
        import web_search
        self.ws = web_search
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(web_search, "_baidu_warm_cookies", None)
        monkeypatch.setattr(web_search, "BAIDU_PROXY", "http://u:p@127.0.0.1:18080")
        yield
        if sys.path[0] == str(SKILL_DIR / "scripts"):
            sys.path.pop(0)

    def test_channel_proxy_scope(self):
        ws = self.ws
        assert ws._channel_proxy("baidu") == ws.BAIDU_PROXY
        assert ws._channel_proxy("baidu_m") == ws.BAIDU_PROXY
        assert ws._channel_proxy("bing") is None
        assert ws._channel_proxy("sogou") is None

    def test_warm_cookies_cached_and_failure_tolerant(self):
        ws = self.ws

        class FakeClient:
            def __init__(self, *a, **k):
                self.cookies = {"BAIDUID": "abc123"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **k):
                return None

        self.monkeypatch.setattr(ws, "_client", lambda *a, **k: FakeClient())
        assert ws._warm_baidu_cookies() == {"BAIDUID": "abc123"}
        # 二次调用走缓存，不再发请求
        assert ws._warm_baidu_cookies() == {"BAIDUID": "abc123"}

        # 预热失败不抛异常，返回空 dict
        self.monkeypatch.setattr(ws, "_baidu_warm_cookies", None)

        def boom(*a, **k):
            raise RuntimeError("down")

        self.monkeypatch.setattr(ws, "_client", boom)
        self.monkeypatch.setattr(ws, "_baidu_warm_cookies", None)
        assert ws._warm_baidu_cookies() == {}

    def test_proxy_fallback_to_direct_on_proxy_error(self):
        """代理连接失败时回落直连，且 errors 如实记录。"""
        ws = self.ws
        calls = []

        class FakeResp:
            def raise_for_status(self):
                return None

            @property
            def encoding(self):
                return "utf-8"

            @property
            def text(self):
                return "<html><body>page</body></html>"

        def fake_fetch(url, verify=True, headers=None, proxy=None, cookies=None):
            calls.append({"url": url, "proxy": proxy, "cookies": cookies})
            if proxy:
                raise ws.httpx.ProxyError("tunnel down")
            return FakeResp().text

        self.monkeypatch.setattr(ws, "fetch_raw", fake_fetch)
        self.monkeypatch.setattr(ws, "_warm_baidu_cookies",
                                 lambda verify=True, proxy=None: {"BAIDUID": "t"})

        class _T:
            time = ws.time.time
            sleep = staticmethod(lambda s: None)

        self.monkeypatch.setattr(ws, "time", _T)
        res = ws.search("测试关键词", limit=3, engine="baidu")
        # 至少一次走代理（抛错）+ 一次直连
        assert any(c["proxy"] == ws.BAIDU_PROXY for c in calls)
        assert any(c["proxy"] is None for c in calls)
        # 回落有记录
        assert any("回落直连" in e for e in res.get("errors", []))
        # 百度通道请求携带预热 cookie
        assert all(c["cookies"] for c in calls)

    def test_non_baidu_channel_no_proxy_no_cookies(self):
        ws = self.ws
        calls = []

        def fake_fetch(url, verify=True, headers=None, proxy=None, cookies=None):
            calls.append({"proxy": proxy, "cookies": cookies})
            return "<html><body>x</body></html>"

        self.monkeypatch.setattr(ws, "fetch_raw", fake_fetch)

        class _T:
            time = ws.time.time
            sleep = staticmethod(lambda s: None)

        self.monkeypatch.setattr(ws, "time", _T)
        ws.search("test keyword", limit=3, engine="bing")
        assert calls and all(c["proxy"] is None and c["cookies"] is None for c in calls)
