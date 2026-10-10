"""纯文本转义测试：调用真实 render，只 mock 浏览器截图边界。"""
from unittest.mock import AsyncMock

import pytest

from src.services.x_to_image.models import InputType, XToImageInput
from src.services.x_to_image.renderers.text_renderer import TextRenderer

pytestmark = pytest.mark.unit


async def test_render_escapes_text_before_screenshot(tmp_path, monkeypatch):
    renderer = TextRenderer()
    output = str(tmp_path / "text.png")
    shoot = AsyncMock(return_value=output)
    monkeypatch.setattr(renderer, "_shoot_full_page", shoot)
    inp = XToImageInput(source='<b>not bold</b> & "quoted"', content_type=InputType.TEXT, width=400)

    assert await renderer.render(inp, tmp_path) == [output]

    shoot.assert_awaited_once()
    doc, actual_input, work_dir = shoot.call_args.args
    assert actual_input is inp
    assert work_dir == tmp_path
    assert '<pre>&lt;b&gt;not bold&lt;/b&gt; &amp; &quot;quoted&quot;</pre>' in doc
    assert '<b>not bold</b>' not in doc
    assert 'width: 400px' in doc
