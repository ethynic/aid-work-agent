"""
EAS 合同查询接口集成测试

使用真实合同编号 212321SG073-XMCG-004 调用 EAS API，
验证接口连通性、返回数据结构和字段提取逻辑。

标记为 integration + tools，需网络连通 EAS API。
跳过条件：无网络或 EAS API 不可达时自动 skip。
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.tools]

# 将技能脚本目录加入 sys.path 以便直接导入
SKILL_SCRIPTS_DIR = str(
    Path(__file__).parent.parent.parent
    / "src" / "skills" / "eas-contract-verify-1.0.0" / "scripts"
)
if SKILL_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SKILL_SCRIPTS_DIR)

from eas_contract_verify import (
    EAS_API_URL,
    _call_eas_api,
    _extract_contract_fields,
    query_contract,
    query_contract_bill,
)

# 测试用合同编号
TEST_CONTRACT_CODE = "212321SG021-XMQT-003  "


# ---------------------------------------------------------------------------
# 辅助：检测 EAS API 是否可达
# ---------------------------------------------------------------------------

def _eas_api_reachable() -> bool:
    """检查 EAS API 是否可访问"""
    try:
        with httpx.Client(timeout=10, verify=False) as client:
            resp = client.post(EAS_API_URL, data={"doType": "bill", "contract_code": "ping"})
            return resp.status_code < 500
    except (httpx.RequestError, Exception):
        return False


# 自动 skip 装饰器
eas_required = pytest.mark.skipif(
    not _eas_api_reachable(),
    reason="EAS API 不可达，跳过集成测试"
)


# ===========================================================================
# 测试类
# ===========================================================================


class TestEASContractQuery:
    """EAS 合同查询 — 真实 API 调用"""

    @eas_required
    def test_query_contract_bill_with_real_code(self):
        """测试用真实合同编号查询单据详情（doType=bill）"""
        result = query_contract_bill(TEST_CONTRACT_CODE)

        # 基本结构断言
        assert isinstance(result, dict)
        assert "success" in result

        # 打印完整结果便于调试
        print(f"\n=== query_contract_bill('{TEST_CONTRACT_CODE}') ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if result["success"]:
            data = result["data"]
            assert isinstance(data, dict)
            # 验证提取了合同关键字段
            assert "contract_code" in data
            assert "party_a" in data
            assert "party_b" in data
            assert "amount" in data
            assert "contract_name" in data
            # 合同编号应与查询一致
            assert data["contract_code"] == TEST_CONTRACT_CODE

    @eas_required
    def test_query_contract_with_real_code(self):
        """测试 query_contract 完整查询流程（bill fallback 到 list）"""
        result = query_contract(TEST_CONTRACT_CODE)

        assert isinstance(result, dict)
        assert "success" in result

        print(f"\n=== query_contract('{TEST_CONTRACT_CODE}') ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if result["success"]:
            data = result["data"]
            assert isinstance(data, dict)
            assert data.get("contract_code") == TEST_CONTRACT_CODE
            # 至少应有甲乙方或金额之一
            has_content = any([
                data.get("party_a"),
                data.get("party_b"),
                data.get("amount"),
                data.get("contract_name"),
            ])
            assert has_content, "合同数据中甲乙方、金额、名称全部为空"

    @eas_required
    def test_query_nonexistent_contract(self):
        """测试查询不存在的合同编号，应返回失败"""
        fake_code = "NOTEXIST-999-FAKE-000"
        result = query_contract(fake_code)

        print(f"\n=== query_contract('{fake_code}') ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # 不存在的合同，success 可能为 False，或 success=True 但 data 为空
        if not result["success"]:
            assert "error" in result
        else:
            data = result.get("data", {})
            # 空数据或不匹配的编号
            assert not data.get("contract_code") or data["contract_code"] != TEST_CONTRACT_CODE

    def test_query_empty_contract_code(self):
        """测试空合同编号，应返回错误（不需要网络）"""
        result = query_contract("")
        assert result["success"] is False
        assert "合同编号" in result.get("error", "")

        result2 = query_contract("   ")
        assert result2["success"] is False

    def test_query_bill_empty_contract_code(self):
        """测试空合同编号查询单据详情，应返回错误（不需要网络）"""
        result = query_contract_bill("")
        assert result["success"] is False
        assert "合同编号" in result.get("error", "")


class TestExtractContractFields:
    """EAS 返回数据字段提取 — 纯逻辑，不需要网络"""

    def test_extract_from_list_format(self):
        """测试从列表格式提取字段"""
        data = [{"contractCode": "HT-001", "partA": "甲方公司", "partB": "乙方公司", "ofTax": "100000"}]
        result = _extract_contract_fields(data)
        assert result["contract_code"] == "HT-001"
        assert result["party_a"] == "甲方公司"
        assert result["party_b"] == "乙方公司"
        assert result["amount"] == "100000"

    def test_extract_from_dict_with_rows(self):
        """测试从 rows 嵌套格式提取字段"""
        data = {"rows": [{"contractCode": "HT-002", "customerName": "A公司", "supplierName": "B公司"}]}
        result = _extract_contract_fields(data)
        assert result["contract_code"] == "HT-002"
        assert result["party_a"] == "A公司"
        assert result["party_b"] == "B公司"

    def test_extract_from_single_object(self):
        """测试从单对象格式提取字段"""
        data = {"contractCode": "HT-003", "partA": "测试甲方", "amount": 50000}
        result = _extract_contract_fields(data)
        assert result["contract_code"] == "HT-003"
        assert result["party_a"] == "测试甲方"

    def test_extract_empty_data(self):
        """测试空数据返回空字段"""
        result = _extract_contract_fields([])
        assert result["contract_code"] == ""
        assert result["match_count"] == 0

        result2 = _extract_contract_fields(None)
        assert result2["contract_code"] == ""

    @eas_required
    def test_extract_from_real_api_response(self):
        """用真实 API 返回数据验证字段提取"""
        # 先获取原始 API 响应
        raw = _call_eas_api({"doType": "bill", "contract_code": TEST_CONTRACT_CODE})
        assert isinstance(raw, dict)

        print(f"\n=== 原始 API 响应 ===")
        print(json.dumps(raw, ensure_ascii=False, indent=2)[:3000])

        # 提取字段
        data = raw.get("data", raw)
        if isinstance(data, str):
            data = json.loads(data)

        result = _extract_contract_fields(data)
        print(f"\n=== 提取结果 ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        assert result["contract_code"] == TEST_CONTRACT_CODE
