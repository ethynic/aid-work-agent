"""excel_template_ai._default_llm qwen 分支单元测试

覆盖 qwen 分支改用 OpenAI 兼容接口（compatible-mode）：
- 与主链路 QwenProvider 一致走 /chat/completions，避免 qwen3.x 系列在原生 Generation 端点报 400 url error
- disable_thinking 通过 enable_thinking 字段控制思考开关
"""
import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# src/config/__init__.py 里 `from .settings import settings` 会遮蔽 src.config.settings
# 模块名，必须用 importlib 从 sys.modules 拿真正的模块对象
settings_module = importlib.import_module('src.config.settings')


def _make_settings():
    class _QwenCfg:
        model = 'qwen3.7-flash'
        base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'

        def get_effective_keys(self):
            return ['test-qwen-key']

    class _LLM:
        provider = 'qwen'
        qwen = _QwenCfg()

    return type('FakeSettings', (), {'llm': _LLM})()


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class TestDefaultLlmQwen:
    def test_uses_compatible_mode_endpoint(self, monkeypatch):
        import httpx
        from src.tools.excel.excel_template_ai import _default_llm

        captured = {}

        def _fake_post(url, *, headers, json, timeout):
            captured['url'] = url
            captured['headers'] = headers
            captured['payload'] = json
            captured['timeout'] = timeout
            return _Resp({"choices": [{"message": {"content": "结构JSON"}}]})

        monkeypatch.setattr(settings_module, 'settings', _make_settings())
        monkeypatch.setattr(httpx, 'post', _fake_post)

        result = _default_llm('分析prompt')

        assert result == '结构JSON'
        assert captured['url'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
        assert captured['headers']['Authorization'] == 'Bearer test-qwen-key'
        assert captured['payload']['model'] == 'qwen3.7-flash'
        assert captured['payload']['enable_thinking'] is False
        assert captured['payload']['max_tokens'] == 4096
        assert captured['payload']['temperature'] == 0.0
        assert captured['payload']['messages'] == [{"role": "user", "content": "分析prompt"}]

    def test_disable_thinking_false_omits_param(self, monkeypatch):
        import httpx
        from src.tools.excel.excel_template_ai import _default_llm

        captured = {}

        def _fake_post(url, *, json, **kw):
            captured['payload'] = json
            return _Resp({"choices": [{"message": {"content": "ok"}}]})

        monkeypatch.setattr(settings_module, 'settings', _make_settings())
        monkeypatch.setattr(httpx, 'post', _fake_post)

        _default_llm('分析prompt', disable_thinking=False)
        assert 'enable_thinking' not in captured['payload']

    def test_missing_key_raises(self, monkeypatch):
        from src.tools.excel.excel_template_ai import _default_llm

        settings = _make_settings()
        settings.llm.qwen.get_effective_keys = lambda: []
        monkeypatch.setattr(settings_module, 'settings', settings)

        with pytest.raises(ValueError, match='QWEN API key 未配置'):
            _default_llm('分析prompt')
