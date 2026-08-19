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


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


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
