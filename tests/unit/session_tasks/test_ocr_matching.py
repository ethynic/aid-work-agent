import pytest
from src.session_tasks.ocr_matching import ocr_text_matches

@pytest.mark.parametrize("a,b,expected", [
    ('“你好，世界！”', '你好 世界', True),
    ('ＡＢＣ', 'abc', True),
    ('这是一条用于测试文字匹配的消息', '这是一条用于测试文宇匹配的消息', True),
    ('好', '不好', False), ('!', '?', False),
    ('余额-100元', '余额100元', False), ('数量1.2公斤', '数量12公斤', False),
    ('这是一条不能自动发送的重要消息', '这是一条能自动发送的重要消息', False),
    ('这是今天已经安排好的会议😀', '这是今天已经安排好的会议😢', False),
])
def test_matching(a,b,expected):
    assert ocr_text_matches(a,b) is expected
    assert ocr_text_matches(b,a) is expected

def test_long_inputs():
    assert ocr_text_matches('甲'*19999+'乙', '甲'*19999+'丙')
    assert not ocr_text_matches('甲'*20001, '甲'*20001)
    assert not ocr_text_matches('\uFDFA'*19999+'甲', '\uFDFA'*19999+'乙')
