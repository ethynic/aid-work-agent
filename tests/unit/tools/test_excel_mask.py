"""
excel_mask 脱敏钩子单测（Q3 最简实现：身份证/手机号占位符往返）

覆盖：
- mask -> unmask 往返幂等（含身份证 + 手机号混合文本）
- 同一真值稳定复用同一占位符；不同真值不同编号
- 已脱敏形式（含 * 或 XXXX 掩码）不再替换
- 词边界：长数字串中的子串不误替换；手机号与身份证直接相连各自正确切分
- unmask_record 递归还原 dict；模块级便捷函数往返
"""

import pytest

from src.tools.excel.excel_mask import MaskSession, mask, unmask

pytestmark = [pytest.mark.tools]

# 校验位正确的测试数据
ID1 = "340203199003074259"
ID2 = "310101198504172334"
ID_X = "34020319921203451X"  # 校验位为 X 的身份证
TEL1 = "13805512366"
TEL2 = "13905514578"


class TestRoundTrip:
    """mask -> unmask 往返幂等"""

    def test_mixed_text_roundtrip(self):
        """身份证 + 手机号混合文本往返还原全等"""
        session = MaskSession()
        text = (
            f"增员：张伟，身份证{ID1}，联系手机{TEL1}；"
            f"减员：郑华，身份证{ID2}，联系手机{TEL2}。"
        )
        masked = session.mask(text)
        assert ID1 not in masked and TEL1 not in masked
        assert ID2 not in masked and TEL2 not in masked
        assert session.unmask(masked) == text

    def test_x_check_digit_roundtrip(self):
        """校验位为 X 的身份证（末位绑定 X 一并脱敏/还原）"""
        session = MaskSession()
        text = f"证号{ID_X}，请核对"
        masked = session.mask(text)
        assert ID_X not in masked
        assert session.unmask(masked) == text

    def test_text_without_sensitive_untouched(self):
        """无敏感信息的文本原样返回"""
        session = MaskSession()
        text = "2026年8月社保增减表，参保地安徽省,芜湖市,镜湖区，比例5%:5%"
        assert session.mask(text) == text
        assert session.unmask(text) == text


class TestPlaceholderStability:
    """同一真值稳定复用同一占位符；不同真值不同编号"""

    def test_same_value_same_placeholder(self):
        session = MaskSession()
        masked = session.mask(f"第一次{ID1}，第二次{ID1}，再提{TEL1}和{TEL1}")
        assert masked.count("[ID_1]") == 2
        assert masked.count("[TEL_1]") == 2

    def test_different_values_different_numbers(self):
        session = MaskSession()
        masked = session.mask(f"{ID1} 与 {ID2}，{TEL1} 与 {TEL2}")
        assert "[ID_1]" in masked and "[ID_2]" in masked
        assert "[TEL_1]" in masked and "[TEL_2]" in masked
        assert masked == f"[ID_1] 与 [ID_2]，[TEL_1] 与 [TEL_2]"

    def test_placeholders_independent_between_kinds(self):
        """身份证与手机号编号各自独立计数"""
        session = MaskSession()
        masked = session.mask(f"{TEL1} 和 {ID1}")
        assert masked == "[TEL_1] 和 [ID_1]"


class TestMaskedFormPassthrough:
    """已脱敏形式（* / XXXX 掩码）不再替换（D10：照抄 + warning 是 Phase 2 的事）"""

    def test_asterisk_masked_id_untouched(self):
        session = MaskSession()
        text = "340203********1234"
        assert session.mask(text) == text

    def test_xxxx_masked_id_untouched(self):
        session = MaskSession()
        text = "3402031990XXXX1234"
        assert session.mask(text) == text


class TestWordBoundary:
    """词边界：避免长数字串中的子串误替换；相邻敏感信息各自正确切分"""

    def test_18_digit_substring_in_22_digit_run_not_replaced(self):
        """22 位数字串（如订单号）中的 18 位子串不误替换"""
        session = MaskSession()
        text = "订单号1234567890123456789012，金额100元"
        assert session.mask(text) == text

    def test_19_digit_phone_like_run_not_replaced(self):
        """1880000000012345678（手机号后紧跟数字，非完整敏感信息）不误替换"""
        session = MaskSession()
        text = "流水1880000000012345678End"
        assert session.mask(text) == text

    def test_phone_directly_before_id(self):
        """手机号紧跟身份证（无分隔符）：各自正确切分为两个占位符"""
        session = MaskSession()
        masked = session.mask(f"{TEL1}{ID1}")
        assert masked == "[TEL_1][ID_1]"
        assert session.unmask(masked) == f"{TEL1}{ID1}"

    def test_id_directly_before_phone(self):
        """身份证紧跟手机号（无分隔符）：各自正确切分"""
        session = MaskSession()
        masked = session.mask(f"{ID1}{TEL1}")
        assert masked == "[ID_1][TEL_1]"
        assert session.unmask(masked) == f"{ID1}{TEL1}"

    def test_phone_not_matched_inside_digits(self):
        """手机号前后是数字时不匹配（如卡号中段）"""
        session = MaskSession()
        text = "卡号9138055123660"
        assert session.mask(text) == text


class TestUnmaskRecord:
    """unmask_record 递归还原 dict"""

    def test_nested_dict_and_list(self):
        session = MaskSession()
        record = {
            "姓名": "张伟",
            "身份证": session.mask(f"证{ID1}"),
            "联系方式": {"手机": session.mask(TEL1), "备用": [session.mask(TEL1)]},
            "基本工资": 6500,
            "备注": None,
        }
        restored = session.unmask_record(record)
        assert restored["身份证"] == f"证{ID1}"
        assert restored["联系方式"]["手机"] == TEL1
        assert restored["联系方式"]["备用"] == [TEL1]
        # 非字符串类型原样保留
        assert restored["基本工资"] == 6500 and restored["备注"] is None

    def test_unknown_placeholder_left_as_is(self):
        """未知占位符（映射表里没有）原样保留"""
        session = MaskSession()
        assert session.unmask("[ID_99]") == "[ID_99]"


class TestModuleLevelHelpers:
    """模块级便捷函数：共用默认会话，单文本立即往返"""

    def test_module_roundtrip(self):
        raw = f"证号{ID1}电话{TEL1}"
        masked = mask(raw)
        assert ID1 not in masked and TEL1 not in masked
        assert unmask(masked) == raw
