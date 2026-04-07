"""
全筑EAS合同核对技能 - 单元测试
"""

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skills

# 动态加载 eas_contract_verify 模块（目录名含 '-'）
_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
_SCRIPT_PATH = os.path.join(
    _PROJECT_ROOT, "src", "skills", "eas-contract-verify-1.0.0", "scripts", "eas_contract_verify.py"
)

# 如果脚本文件不存在，跳过整个模块
pytestmark = pytest.mark.skipif(
    not os.path.exists(_SCRIPT_PATH),
    reason="EAS contract verify skill not installed",
)

_spec = importlib.util.spec_from_file_location("eas_contract_verify", _SCRIPT_PATH)
_eas_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_eas_mod)

_amount_equal = _eas_mod._amount_equal
_cn_amount_to_num = _eas_mod._cn_amount_to_num
_extract_contract_fields = _eas_mod._extract_contract_fields
_lcs_length = _eas_mod._lcs_length
_normalize_amount = _eas_mod._normalize_amount
_normalize_text = _eas_mod._normalize_text
_safe_filename = _eas_mod._safe_filename
_text_similar = _eas_mod._text_similar
compare_contract = _eas_mod.compare_contract
query_contract = _eas_mod.query_contract
sanitize_error_info = _eas_mod.sanitize_error_info


class TestSanitizeErrorInfo:
    """测试敏感信息过滤"""

    def test_password_filtered(self):
        result = sanitize_error_info("password=secret123")
        assert result == "password=***"

    def test_api_key_filtered(self):
        result = sanitize_error_info("api_key=abc123def")
        assert result == "api_key=***"

    def test_token_filtered(self):
        result = sanitize_error_info("token=Bearer xyz")
        assert "token=***" in result

    def test_no_sensitive_info(self):
        msg = "Connection timeout to database server"
        result = sanitize_error_info(msg)
        assert result == msg

    def test_multiple_sensitive_fields(self):
        result = sanitize_error_info("password=mypwd and api_key=mykey")
        assert "password=***" in result
        assert "api_key=***" in result


class TestSafeFilename:
    """测试安全文件名生成"""

    def test_chinese_replaced(self):
        result = _safe_filename("合同扫描件.pdf")
        assert "合" not in result
        assert "同" not in result
        assert result.endswith(".pdf")

    def test_no_chinese(self):
        result = _safe_filename("contract_001.pdf")
        assert result == "contract_001.pdf"

    def test_special_chars_removed(self):
        result = _safe_filename('file<>:"/\\|?*name.pdf')
        assert "<" not in result
        assert ">" not in result
        assert result.endswith(".pdf")

    def test_empty_result_fallback(self):
        result = _safe_filename("文档")
        assert len(result) > 0
        assert result.startswith("file_")


class TestExtractContractFields:
    """测试从 EAS 返回数据中提取合同字段"""

    def test_single_object_standard_fields(self):
        data = {
            "contractCode": "XZCG-2026-0001",
            "partyA": "全筑控股集团",
            "partyB": "某某建材公司",
            "amount": "100000.00",
            "contractName": "材料采购合同",
        }
        result = _extract_contract_fields(data)
        assert result["contract_code"] == "XZCG-2026-0001"
        assert result["party_a"] == "全筑控股集团"
        assert result["party_b"] == "某某建材公司"
        assert result["amount"] == "100000.00"
        assert result["contract_name"] == "材料采购合同"

    def test_list_format(self):
        data = [{
            "contractNo": "HT-2024-001",
            "firstParty": "甲方公司",
            "secondParty": "乙方公司",
            "contractAmount": "50000",
        }]
        result = _extract_contract_fields(data)
        assert result["contract_code"] == "HT-2024-001"
        assert result["party_a"] == "甲方公司"
        assert result["party_b"] == "乙方公司"
        assert result["amount"] == "50000"

    def test_rows_format(self):
        data = {"rows": [{"contractCode": "XZ-001", "amount": "200000"}]}
        result = _extract_contract_fields(data)
        assert result["contract_code"] == "XZ-001"
        assert result["amount"] == "200000"

    def test_empty_list(self):
        result = _extract_contract_fields([])
        assert result["contract_code"] == ""
        assert result["match_count"] == 0


class TestNormalizeAmount:
    """测试金额标准化"""

    def test_plain_number(self):
        assert abs(_normalize_amount("100000") - 100000.0) < 0.01

    def test_with_comma(self):
        assert abs(_normalize_amount("100,000.00") - 100000.0) < 0.01

    def test_with_yuan_sign(self):
        assert abs(_normalize_amount("¥100000") - 100000.0) < 0.01

    def test_with_fullwidth_yuan(self):
        assert abs(_normalize_amount("￥100,000.00") - 100000.0) < 0.01

    def test_with_yuan_unit(self):
        assert abs(_normalize_amount("100000元") - 100000.0) < 0.01

    def test_chinese_upper(self):
        result = _normalize_amount("壹拾万元整")
        assert result is not None
        assert abs(result - 100000.0) < 0.01

    def test_empty_string(self):
        assert _normalize_amount("") is None

    def test_invalid_string(self):
        assert _normalize_amount("abc") is None


class TestAmountEqual:
    """测试金额比对"""

    def test_exact_equal(self):
        assert _amount_equal(100000.0, 100000.0)

    def test_small_difference(self):
        assert _amount_equal(100000.0, 100001.0)

    def test_large_difference(self):
        assert not _amount_equal(100000.0, 200000.0)

    def test_both_zero(self):
        assert _amount_equal(0, 0)

    def test_one_zero(self):
        assert not _amount_equal(0, 100)

    def test_none_values(self):
        assert not _amount_equal(None, 100)
        assert not _amount_equal(100, None)


class TestTextSimilar:
    """测试文本相似度比较"""

    def test_exact_match(self):
        assert _text_similar("全筑控股集团", "全筑控股集团")

    def test_contains(self):
        assert _text_similar("全筑控股集团", "全筑控股集团有限公司")

    def test_punctuation_removed(self):
        assert _text_similar("全筑控股（集团）", "全筑控股集团")

    def test_different_text(self):
        assert not _text_similar("全筑控股集团", "某某建筑材料公司")


class TestQueryContract:
    """测试 EAS 合同查询"""

    @patch.object(_eas_mod, "_call_eas_api")
    def test_query_success(self, mock_api):
        mock_api.return_value = {
            "success": True,
            "data": {
                "contractCode": "XZCG-2026-0001",
                "partA": "全筑控股集团",
                "partB": "建材公司",
                "ofTax": 100000.00,
            },
        }
        result = query_contract("XZCG-2026-0001")
        assert result["success"]
        assert result["data"]["contract_code"] == "XZCG-2026-0001"

    @patch.object(_eas_mod, "_call_eas_api")
    def test_query_api_failure(self, mock_api):
        mock_api.return_value = {"success": False, "msg": "合同编号不存在"}
        result = query_contract("INVALID-001")
        assert not result["success"]
        assert "合同编号不存在" in result["error"]

    @patch.object(_eas_mod, "_call_eas_api")
    def test_query_empty_code(self, mock_api):
        result = query_contract("")
        assert not result["success"]
        assert "不能为空" in result["error"]
        mock_api.assert_not_called()


class TestCompareContract:
    """测试合同比对"""

    @patch.object(_eas_mod, "_call_eas_api")
    def test_compare_match(self, mock_api):
        mock_api.return_value = {
            "success": True,
            "data": {
                "contractCode": "XZCG-2026-0001",
                "partA": "全筑控股集团有限公司",
                "partB": "上海某某建材有限公司",
                "ofTax": 100000.00,
            },
        }
        result = compare_contract("XZCG-2026-0001", {
            "party_a": "全筑控股集团有限公司",
            "party_b": "上海某某建材有限公司",
            "amount": "100000.00",
        })
        assert result["result"] == "yes"
        assert result["message"] == "合同信息一致"

    @patch.object(_eas_mod, "_call_eas_api")
    def test_compare_mismatch_amount(self, mock_api):
        mock_api.return_value = {
            "success": True,
            "data": {
                "contractCode": "XZCG-2026-0001",
                "partA": "全筑控股集团",
                "partB": "建材公司",
                "ofTax": 200000.00,
            },
        }
        result = compare_contract("XZCG-2026-0001", {
            "party_a": "全筑控股集团",
            "party_b": "建材公司",
            "amount": "100000.00",
        })
        assert result["result"] == "no"
        assert "金额不一致" in result["message"]

    def test_compare_missing_ocr_fields(self):
        result = compare_contract("XZCG-2026-0001", {"party_a": "甲方"})
        assert result["result"] == "no"
        assert "缺少必要字段" in result["message"]


class TestCnAmountToNum:
    """测试中文大写金额转换"""

    def test_simple(self):
        assert abs(_cn_amount_to_num("壹拾万") - 100000.0) < 0.01

    def test_with_unit(self):
        assert abs(_cn_amount_to_num("壹拾万元整") - 100000.0) < 0.01

    def test_hundred_thousand(self):
        assert abs(_cn_amount_to_num("壹佰万") - 1000000.0) < 0.01
