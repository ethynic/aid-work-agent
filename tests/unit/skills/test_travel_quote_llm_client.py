"""
travel-quote llm_client 单元测试

覆盖 qwen 分支改用 OpenAI 兼容接口（compatible-mode）的调用逻辑：
- 与主链路 QwenProvider 一致走 /chat/completions，避免 qwen3.x 系列模型
  在 DashScope 原生 Generation 端点报 400 url error
- 不透传 deepseek 格式 extra_body，用 settings.llm.enable_thinking 控制思考开关
"""
import importlib
import sys
from pathlib import Path

import pytest

# src/config/__init__.py 里 `from .settings import settings` 会遮蔽 src.config.settings
# 模块名，必须用 importlib 从 sys.modules 拿真正的模块对象
settings_module = importlib.import_module('src.config.settings')

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _make_settings(enable_thinking=False):
    class _QwenCfg:
        model = 'qwen3.7-flash'
        base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'

        def get_effective_keys(self):
            return ['test-qwen-key']

    class _LLM:
        provider = 'qwen'
        enable_thinking = False
        qwen = _QwenCfg()

    llm = _LLM()
    llm.enable_thinking = enable_thinking
    return type('FakeSettings', (), {'llm': llm})()


def _make_deepseek_settings():
    """全局 provider 仍是 qwen，验证 provider_override / env 能切到 deepseek。"""
    class _DeepSeekCfg:
        model = 'deepseek-v4-flash'
        base_url = 'https://api.deepseek.com'

        def get_effective_keys(self):
            return ['test-deepseek-key']

    class _LLM:
        provider = 'qwen'
        enable_thinking = None
        deepseek = _DeepSeekCfg()

    return type('FakeSettings', (), {'llm': _LLM()})()


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _clear_skill_llm_env(monkeypatch):
    """清空技能子进程 LLM 覆盖 env，保证用例确定性（env 优先级高于 settings）"""
    monkeypatch.delenv('SKILL_LLM_PROVIDER', raising=False)
    monkeypatch.delenv('SKILL_LLM_MODEL', raising=False)


class TestCallLlmQwen:
    def test_uses_compatible_mode_endpoint(self, monkeypatch):
        import llm_client
        import httpx

        captured = {}

        def _fake_post(url, *, headers, json, timeout):
            captured['url'] = url
            captured['headers'] = headers
            captured['payload'] = json
            captured['timeout'] = timeout
            return _Resp({"choices": [{"message": {"content": "解析结果"}}]})

        monkeypatch.setattr(settings_module, 'settings', _make_settings())
        monkeypatch.setattr(httpx, 'post', _fake_post)

        result = llm_client.call_llm('测试prompt', max_tokens=1024, task='test')

        assert result == '解析结果'
        assert captured['url'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
        assert captured['headers']['Authorization'] == 'Bearer test-qwen-key'
        assert captured['payload']['model'] == 'qwen3.7-flash'
        assert captured['payload']['enable_thinking'] is False
        assert captured['payload']['max_tokens'] == 1024
        assert captured['payload']['messages'] == [{"role": "user", "content": "测试prompt"}]

    def test_base_url_none_falls_back_to_default(self, monkeypatch):
        import llm_client
        import httpx

        captured = {}

        def _fake_post(url, **kw):
            captured['url'] = url
            return _Resp({"choices": [{"message": {"content": "ok"}}]})

        settings = _make_settings()
        settings.llm.qwen.base_url = None
        monkeypatch.setattr(settings_module, 'settings', settings)
        monkeypatch.setattr(httpx, 'post', _fake_post)

        llm_client.call_llm('p')
        assert captured['url'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'

    def test_missing_key_raises(self, monkeypatch):
        import llm_client

        settings = _make_settings()
        settings.llm.qwen.get_effective_keys = lambda: []
        monkeypatch.setattr(settings_module, 'settings', settings)

        with pytest.raises(ValueError, match='QWEN API key 未配置'):
            llm_client.call_llm('p')

    def test_enable_thinking_none_omits_param(self, monkeypatch):
        import llm_client
        import httpx

        captured = {}

        def _fake_post(url, *, json, **kw):
            captured['payload'] = json
            return _Resp({"choices": [{"message": {"content": "ok"}}]})

        monkeypatch.setattr(settings_module, 'settings', _make_settings(enable_thinking=None))
        monkeypatch.setattr(httpx, 'post', _fake_post)

        llm_client.call_llm('p')
        assert 'enable_thinking' not in captured['payload']


class TestCallLlmProviderOverride:
    def test_provider_override_selects_deepseek(self, monkeypatch):
        import llm_client
        import httpx

        captured = {}

        def _fake_post(url, *, headers, json, timeout):
            captured['url'] = url
            captured['headers'] = headers
            captured['payload'] = json
            return _Resp({"choices": [{"message": {"content": "解析结果"}}]})

        monkeypatch.setattr(settings_module, 'settings', _make_deepseek_settings())
        monkeypatch.setattr(httpx, 'post', _fake_post)

        result = llm_client.call_llm(
            '测试prompt',
            provider_override='deepseek',
            model_override='deepseek-v4-flash',
            task='test',
        )

        assert result == '解析结果'
        assert captured['url'] == 'https://api.deepseek.com/chat/completions'
        assert captured['headers']['Authorization'] == 'Bearer test-deepseek-key'
        assert captured['payload']['model'] == 'deepseek-v4-flash'

    def test_env_provider_and_model_fallback(self, monkeypatch):
        import llm_client
        import httpx

        monkeypatch.setenv('SKILL_LLM_PROVIDER', 'deepseek')
        monkeypatch.setenv('SKILL_LLM_MODEL', 'deepseek-v4-flash')
        monkeypatch.setattr(settings_module, 'settings', _make_deepseek_settings())

        captured = {}

        def _fake_post(url, *, headers, json, **kw):
            captured['url'] = url
            captured['headers'] = headers
            captured['payload'] = json
            return _Resp({"choices": [{"message": {"content": "解析结果"}}]})

        monkeypatch.setattr(httpx, 'post', _fake_post)

        llm_client.call_llm('测试prompt', task='test')

        assert captured['url'] == 'https://api.deepseek.com/chat/completions'
        assert captured['headers']['Authorization'] == 'Bearer test-deepseek-key'
        assert captured['payload']['model'] == 'deepseek-v4-flash'

    def test_explicit_param_beats_env(self, monkeypatch):
        import llm_client
        import httpx

        monkeypatch.setenv('SKILL_LLM_PROVIDER', 'deepseek')
        monkeypatch.setenv('SKILL_LLM_MODEL', 'deepseek-v4-flash')
        monkeypatch.setattr(settings_module, 'settings', _make_settings())

        captured = {}

        def _fake_post(url, *, headers, json, **kw):
            captured['payload'] = json
            return _Resp({"choices": [{"message": {"content": "ok"}}]})

        monkeypatch.setattr(httpx, 'post', _fake_post)

        # 显式 provider_override=qwen 应覆盖 env 里的 deepseek
        llm_client.call_llm('p', provider_override='qwen', task='test')
        assert captured['payload']['model'] == 'qwen3.7-flash'


class TestGetEffectiveProvider:
    def test_global_default(self, monkeypatch):
        import llm_client
        monkeypatch.setattr(settings_module, 'settings', _make_settings())
        assert llm_client.get_effective_provider() == 'qwen'

    def test_env_overrides_global(self, monkeypatch):
        import llm_client
        monkeypatch.setenv('SKILL_LLM_PROVIDER', 'deepseek')
        monkeypatch.setattr(settings_module, 'settings', _make_settings())
        assert llm_client.get_effective_provider() == 'deepseek'

    def test_explicit_param_beats_env(self, monkeypatch):
        import llm_client
        monkeypatch.setenv('SKILL_LLM_PROVIDER', 'deepseek')
        monkeypatch.setattr(settings_module, 'settings', _make_settings())
        assert llm_client.get_effective_provider('qwen') == 'qwen'


class TestCallLlmEmptyContent:
    """LLM 返回空/纯空白 content 时必须抛带上下文的清晰错误，而不是让下游
    json.loads 报晦涩的 "Expecting value: line 1 column 1 (char 0)"。"""

    def test_empty_content_raises_clear_error(self, monkeypatch):
        import llm_client
        import httpx

        def _fake_post(url, **kw):
            return _Resp({"choices": [{"message": {"content": ""}}]})

        monkeypatch.setattr(settings_module, 'settings', _make_settings())
        monkeypatch.setattr(httpx, 'post', _fake_post)

        with pytest.raises(ValueError, match=r'LLM 返回空内容.*task=test.*provider=qwen.*model=qwen3.7-flash'):
            llm_client.call_llm('p', task='test')

    def test_whitespace_content_raises(self, monkeypatch):
        import llm_client
        import httpx

        def _fake_post(url, **kw):
            return _Resp({"choices": [{"message": {"content": "\n   \n"}}]})

        monkeypatch.setattr(settings_module, 'settings', _make_settings())
        monkeypatch.setattr(httpx, 'post', _fake_post)

        with pytest.raises(ValueError, match='LLM 返回空内容'):
            llm_client.call_llm('p')

    def test_deepseek_empty_content_raises(self, monkeypatch):
        import llm_client
        import httpx

        def _fake_post(url, **kw):
            return _Resp({"choices": [{"message": {"content": "  "}}]})

        monkeypatch.setenv('SKILL_LLM_PROVIDER', 'deepseek')
        monkeypatch.setenv('SKILL_LLM_MODEL', 'deepseek-v4-flash')
        monkeypatch.setattr(settings_module, 'settings', _make_deepseek_settings())
        monkeypatch.setattr(httpx, 'post', _fake_post)

        with pytest.raises(ValueError, match='provider=deepseek.*model=deepseek-v4-flash'):
            llm_client.call_llm('p')
