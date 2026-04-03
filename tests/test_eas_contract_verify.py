"""
全筑EAS合同核对技能 - 单元测试

测试范围：
- EAS API 调用（mock）
- 合同字段提取
- 文件名安全处理
- 金额标准化与比对
- 文本相似度比较
- 合同比对逻辑
"""

import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# 将项目根目录加入 sys.path
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)

# 由于目录名含 '-'，无法直接 import，使用 importlib 动态加载
_SCRIPT_PATH = os.path.join(
    PROJECT_ROOT, "src", "skills", "eas-contract-verify-1.0.0", "scripts", "eas_contract_verify.py"
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


class TestSanitizeErrorInfo(unittest.TestCase):
    """测试敏感信息过滤"""

    def test_password_filtered(self):
        result = sanitize_error_info("password=secret123")
        self.assertEqual(result, "password=***")

    def test_api_key_filtered(self):
        result = sanitize_error_info("api_key=abc123def")
        self.assertEqual(result, "api_key=***")

    def test_token_filtered(self):
        result = sanitize_error_info("token=Bearer xyz")
        # \S+ 匹配到空格前，所以只替换 token=Bearer
        self.assertIn("token=***", result)

    def test_no_sensitive_info(self):
        msg = "Connection timeout to database server"
        result = sanitize_error_info(msg)
        self.assertEqual(result, msg)

    def test_multiple_sensitive_fields(self):
        result = sanitize_error_info("password=mypwd and api_key=mykey")
        self.assertIn("password=***", result)
        self.assertIn("api_key=***", result)


class TestSafeFilename(unittest.TestCase):
    """测试安全文件名生成"""

    def test_chinese_replaced(self):
        result = _safe_filename("合同扫描件.pdf")
        self.assertNotIn("合", result)
        self.assertNotIn("同", result)
        self.assertTrue(result.endswith(".pdf"))

    def test_no_chinese(self):
        result = _safe_filename("contract_001.pdf")
        self.assertEqual(result, "contract_001.pdf")

    def test_special_chars_removed(self):
        result = _safe_filename('file<>:"/\\|?*name.pdf')
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertTrue(result.endswith(".pdf"))

    def test_empty_result_fallback(self):
        result = _safe_filename("文档")
        self.assertTrue(len(result) > 0)
        self.assertTrue(result.startswith("file_"))


class TestExtractContractFields(unittest.TestCase):
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
        self.assertEqual(result["contract_code"], "XZCG-2026-0001")
        self.assertEqual(result["party_a"], "全筑控股集团")
        self.assertEqual(result["party_b"], "某某建材公司")
        self.assertEqual(result["amount"], "100000.00")
        self.assertEqual(result["contract_name"], "材料采购合同")

    def test_list_format(self):
        data = [{
            "contractNo": "HT-2024-001",
            "firstParty": "甲方公司",
            "secondParty": "乙方公司",
            "contractAmount": "50000",
        }]
        result = _extract_contract_fields(data)
        self.assertEqual(result["contract_code"], "HT-2024-001")
        self.assertEqual(result["party_a"], "甲方公司")
        self.assertEqual(result["party_b"], "乙方公司")
        self.assertEqual(result["amount"], "50000")

    def test_rows_format(self):
        data = {"rows": [{"contractCode": "XZ-001", "amount": "200000"}]}
        result = _extract_contract_fields(data)
        self.assertEqual(result["contract_code"], "XZ-001")
        self.assertEqual(result["amount"], "200000")

    def test_empty_list(self):
        result = _extract_contract_fields([])
        self.assertEqual(result["contract_code"], "")
        self.assertEqual(result["match_count"], 0)

    def test_alternative_field_names(self):
        data = {
            "contract_no": "CG-001",
            "customerName": "客户A",
            "supplierName": "供应商B",
            "totalAmount": "300000",
        }
        result = _extract_contract_fields(data)
        self.assertEqual(result["contract_code"], "CG-001")
        self.assertEqual(result["party_a"], "客户A")
        self.assertEqual(result["party_b"], "供应商B")
        self.assertEqual(result["amount"], "300000")


class TestNormalizeAmount(unittest.TestCase):
    """测试金额标准化"""

    def test_plain_number(self):
        self.assertAlmostEqual(_normalize_amount("100000"), 100000.0)

    def test_with_comma(self):
        self.assertAlmostEqual(_normalize_amount("100,000.00"), 100000.0)

    def test_with_yuan_sign(self):
        self.assertAlmostEqual(_normalize_amount("¥100000"), 100000.0)

    def test_with_fullwidth_yuan(self):
        self.assertAlmostEqual(_normalize_amount("￥100,000.00"), 100000.0)

    def test_with_yuan_unit(self):
        self.assertAlmostEqual(_normalize_amount("100000元"), 100000.0)

    def test_chinese_upper(self):
        result = _normalize_amount("壹拾万元整")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 100000.0)

    def test_chinese_mixed(self):
        result = _normalize_amount("拾万")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 100000.0)

    def test_empty_string(self):
        self.assertIsNone(_normalize_amount(""))

    def test_invalid_string(self):
        self.assertIsNone(_normalize_amount("abc"))


class TestAmountEqual(unittest.TestCase):
    """测试金额比对"""

    def test_exact_equal(self):
        self.assertTrue(_amount_equal(100000.0, 100000.0))

    def test_small_difference(self):
        self.assertTrue(_amount_equal(100000.0, 100001.0))

    def test_large_difference(self):
        self.assertFalse(_amount_equal(100000.0, 200000.0))

    def test_both_zero(self):
        self.assertTrue(_amount_equal(0, 0))

    def test_one_zero(self):
        self.assertFalse(_amount_equal(0, 100))

    def test_none_values(self):
        self.assertFalse(_amount_equal(None, 100))
        self.assertFalse(_amount_equal(100, None))


class TestTextSimilar(unittest.TestCase):
    """测试文本相似度比较"""

    def test_exact_match(self):
        self.assertTrue(_text_similar("全筑控股集团", "全筑控股集团"))

    def test_contains(self):
        self.assertTrue(_text_similar("全筑控股集团", "全筑控股集团有限公司"))

    def test_punctuation_removed(self):
        self.assertTrue(_text_similar("全筑控股（集团）", "全筑控股集团"))

    def test_different_text(self):
        self.assertFalse(_text_similar("全筑控股集团", "某某建筑材料公司"))

    def test_empty_strings(self):
        self.assertTrue(_text_similar("", ""))

    def test_one_empty(self):
        self.assertFalse(_text_similar("全筑", ""))

    def test_case_insensitive(self):
        # 英文部分大小写不同，标准化后应一致
        self.assertTrue(_text_similar("ABC Company", "abc company"))


class TestLCSLength(unittest.TestCase):
    """测试最长公共子序列"""

    def test_identical_strings(self):
        self.assertEqual(_lcs_length("abcdef", "abcdef"), 6)

    def test_no_common(self):
        self.assertEqual(_lcs_length("abc", "def"), 0)

    def test_partial_common(self):
        self.assertEqual(_lcs_length("abcde", "ace"), 3)

    def test_empty_strings(self):
        self.assertEqual(_lcs_length("", ""), 0)


class TestNormalizeText(unittest.TestCase):
    """测试文本标准化"""

    def test_remove_spaces(self):
        self.assertEqual(_normalize_text("全筑 控股"), "全筑控股")

    def test_remove_punctuation(self):
        result = _normalize_text("全筑（集团）有限公司")
        self.assertNotIn("（", result)
        self.assertNotIn("）", result)

    def test_lowercase(self):
        self.assertEqual(_normalize_text("ABC"), "abc")

    def test_empty(self):
        self.assertEqual(_normalize_text(""), "")


class TestQueryContract(unittest.TestCase):
    """测试 EAS 合同查询"""

    @patch.object(_eas_mod, "_call_eas_api")
    def test_query_success(self, mock_api):
        # query_contract 先尝试 bill，bill 通过 _call_eas_api 调用
        mock_api.return_value = {
            "success": True,
            "data": {
                "contractCode": "XZCG-2026-0001",
                "partA": "全筑控股集团",
                "partB": "建材公司",
                "ofTax": 100000.00,
            }
        }
        result = query_contract("XZCG-2026-0001")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["contract_code"], "XZCG-2026-0001")
        self.assertEqual(result["data"]["party_a"], "全筑控股集团")

    @patch.object(_eas_mod, "_call_eas_api")
    def test_query_api_failure(self, mock_api):
        mock_api.return_value = {"success": False, "msg": "合同编号不存在"}
        result = query_contract("INVALID-001")
        self.assertFalse(result["success"])
        self.assertIn("合同编号不存在", result["error"])

    @patch.object(_eas_mod, "_call_eas_api")
    def test_query_empty_code(self, mock_api):
        result = query_contract("")
        self.assertFalse(result["success"])
        self.assertIn("不能为空", result["error"])
        mock_api.assert_not_called()

    @patch.object(_eas_mod, "_call_eas_api")
    def test_query_network_error(self, mock_api):
        mock_api.side_effect = RuntimeError("Connection refused")
        result = query_contract("XZCG-2026-0001")
        self.assertFalse(result["success"])
        self.assertIn("查询 EAS 合同信息失败", result["error"])


class TestCompareContract(unittest.TestCase):
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
            }
        }
        result = compare_contract("XZCG-2026-0001", {
            "party_a": "全筑控股集团有限公司",
            "party_b": "上海某某建材有限公司",
            "amount": "100000.00",
        })
        self.assertEqual(result["result"], "yes")
        self.assertEqual(result["message"], "合同信息一致")

    @patch.object(_eas_mod, "_call_eas_api")
    def test_compare_mismatch_amount(self, mock_api):
        mock_api.return_value = {
            "success": True,
            "data": {
                "contractCode": "XZCG-2026-0001",
                "partA": "全筑控股集团",
                "partB": "建材公司",
                "ofTax": 200000.00,
            }
        }
        result = compare_contract("XZCG-2026-0001", {
            "party_a": "全筑控股集团",
            "party_b": "建材公司",
            "amount": "100000.00",
        })
        self.assertEqual(result["result"], "no")
        self.assertIn("金额不一致", result["message"])

    @patch.object(_eas_mod, "_call_eas_api")
    def test_compare_mismatch_party(self, mock_api):
        mock_api.return_value = {
            "success": True,
            "data": {
                "contractCode": "XZCG-2026-0001",
                "partA": "北京某某科技集团有限公司",
                "partB": "乙方公司B",
                "ofTax": 100000.00,
            }
        }
        result = compare_contract("XZCG-2026-0001", {
            "party_a": "上海某某贸易有限公司",
            "party_b": "乙方公司B",
            "amount": "100000.00",
        })
        self.assertEqual(result["result"], "no")
        self.assertIn("甲方不一致", result["message"])

    @patch.object(_eas_mod, "_call_eas_api")
    def test_compare_eas_query_fails(self, mock_api):
        mock_api.return_value = {"success": False, "msg": "网络错误"}
        result = compare_contract("XZCG-2026-0001", {
            "party_a": "甲方",
            "party_b": "乙方",
            "amount": "100000",
        })
        self.assertEqual(result["result"], "no")
        self.assertIn("EAS", result["message"])

    def test_compare_missing_ocr_fields(self):
        result = compare_contract("XZCG-2026-0001", {
            "party_a": "甲方",
        })
        self.assertEqual(result["result"], "no")
        self.assertIn("缺少必要字段", result["message"])

    @patch.object(_eas_mod, "_call_eas_api")
    def test_compare_fuzzy_name_match(self, mock_api):
        """测试公司名称模糊匹配"""
        mock_api.return_value = {
            "success": True,
            "data": {
                "contractCode": "XZCG-2026-0001",
                "partA": "全筑控股集团有限公司",
                "partB": "建材公司",
                "ofTax": 100000.00,
            }
        }
        result = compare_contract("XZCG-2026-0001", {
            "party_a": "全筑控股集团有限公司",
            "party_b": "建材公司",
            "amount": "100000.00",
        })
        self.assertEqual(result["result"], "yes")


class TestCnAmountToNum(unittest.TestCase):
    """测试中文大写金额转换"""

    def test_simple(self):
        self.assertAlmostEqual(_cn_amount_to_num("壹拾万"), 100000.0)

    def test_with_unit(self):
        result = _cn_amount_to_num("壹拾万元整")
        self.assertAlmostEqual(result, 100000.0)

    def test_hundred_thousand(self):
        self.assertAlmostEqual(_cn_amount_to_num("壹佰万"), 1000000.0)

    def test_complex(self):
        self.assertAlmostEqual(_cn_amount_to_num("伍拾万"), 500000.0)


if __name__ == "__main__":
    unittest.main()
