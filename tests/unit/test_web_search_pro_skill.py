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
        assert skill.version == "1.1.0"

    def test_skill_in_master_content_readable(self):
        registry = _load_registry()
        content = registry.get_content("web-search-pro")
        assert content is not None
        assert "pipeline.py" in content
        assert "skill_execute" in content

    def test_scripts_present(self):
        for name in ("web_search.py", "pipeline.py", "render.py", "efficiency.py"):
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
    def _setup(self, monkeypatch, tmp_path):
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        monkeypatch.syspath_prepend(str(SKILL_DIR / "scripts"))
        import web_search
        import efficiency
        self.ws = web_search
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(web_search, "_baidu_warm_cookies", {})
        monkeypatch.setattr(web_search, "BAIDU_PROXIES", ["http://u:p@127.0.0.1:18080"])
        monkeypatch.setattr(web_search, "_proxy_failures", {})
        monkeypatch.setattr(web_search, "_proxy_cursor", 0)
        # efficiency 节流/冷却状态落盘（throttle.json）：隔离到 tmp，
        # 防止污染仓库目录与用例间互扰（缓存映射保留在进程内即可）
        import tempfile
        cache_dir = Path(tempfile.mkdtemp())
        monkeypatch.setattr(efficiency.throttle, "path", cache_dir / "throttle.json")
        monkeypatch.setattr(efficiency.throttle, "_mem",
                            {"last": {}, "cooldown_until": {}})
        monkeypatch.setattr(efficiency, "CACHE_DIR", cache_dir)
        monkeypatch.setattr(efficiency, "LLM_CACHE_DIR", cache_dir / "llm")
        yield
        if sys.path[0] == str(SKILL_DIR / "scripts"):
            sys.path.pop(0)

    def test_channel_proxy_scope(self):
        ws = self.ws
        assert ws._channel_proxy("baidu") == ws.BAIDU_PROXIES[0]
        assert ws._channel_proxy("baidu_m") == ws.BAIDU_PROXIES[0]
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
        self.monkeypatch.setattr(ws, "_baidu_warm_cookies", {})

        def boom(*a, **k):
            raise RuntimeError("down")

        self.monkeypatch.setattr(ws, "_client", boom)
        self.monkeypatch.setattr(ws, "_baidu_warm_cookies", {})
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
        assert any(c["proxy"] == ws.BAIDU_PROXIES[0] for c in calls)
        assert any(c["proxy"] is None for c in calls)
        # 回落有记录，且只记 host:port、不带 BasicAuth 凭据
        assert any("回落直连" in e for e in res.get("errors", []))
        assert any("127.0.0.1:18080" in e for e in res.get("errors", []))
        assert all("u:p" not in e for e in res.get("errors", []))
        # 失败出口进入进程级熔断
        assert ws._proxy_failures.get(ws.BAIDU_PROXIES[0], 0) > ws.time.time()
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

    def test_multi_proxy_rotation_round_robin(self):
        """多出口逐次轮换：顺序取、循环往复。"""
        ws = self.ws
        proxies = ["http://u:p@a:1", "http://u:p@b:2"]
        self.monkeypatch.setattr(ws, "BAIDU_PROXIES", proxies)
        picked = [ws._next_baidu_proxy() for _ in range(4)]
        assert picked == [proxies[0], proxies[1], proxies[0], proxies[1]]
        # 无配置回直连
        self.monkeypatch.setattr(ws, "BAIDU_PROXIES", [])
        assert ws._next_baidu_proxy() is None

    def test_failed_proxy_circuit_breaks(self):
        """熔断中的出口被轮换避开；全部熔断中回直连。"""
        ws = self.ws
        proxies = ["http://u:p@a:1", "http://u:p@b:2"]
        self.monkeypatch.setattr(ws, "BAIDU_PROXIES", proxies)
        self.monkeypatch.setattr(ws, "_proxy_failures", {proxies[0]: ws.time.time() + 600})
        assert ws._next_baidu_proxy() == proxies[1]
        assert ws._next_baidu_proxy() == proxies[1]
        self.monkeypatch.setattr(ws, "_proxy_failures",
                                 {p: ws.time.time() + 600 for p in proxies})
        assert ws._next_baidu_proxy() is None
        # 熔断过期后恢复可用
        self.monkeypatch.setattr(ws, "_proxy_failures",
                                 {p: ws.time.time() - 1 for p in proxies})
        assert ws._next_baidu_proxy() in proxies

    def test_proxy_label_strips_credentials(self):
        ws = self.ws
        assert ws._proxy_label("http://u:p@1.2.3.4:18080") == "1.2.3.4:18080"
        assert ws._proxy_label("not-a-url") == "proxy"

    def test_warm_cookies_isolated_per_proxy(self):
        """预热缓存按出口隔离：不同出口各预热一次，同出口走缓存。"""
        ws = self.ws
        client_args = []

        class FakeClient:
            def __init__(self, *a, **k):
                client_args.append(k.get("proxy"))
                self.cookies = {"BAIDUID": f"id-{len(client_args)}"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **k):
                return None

        self.monkeypatch.setattr(ws, "_client", FakeClient)
        assert ws._warm_baidu_cookies(proxy="http://u:p@a:1") == {"BAIDUID": "id-1"}
        assert ws._warm_baidu_cookies(proxy="http://u:p@a:1") == {"BAIDUID": "id-1"}
        assert ws._warm_baidu_cookies(proxy="http://u:p@b:2") == {"BAIDUID": "id-2"}
        assert client_args == ["http://u:p@a:1", "http://u:p@b:2"]
