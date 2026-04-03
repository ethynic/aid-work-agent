"""
EAS 合同查询测试脚本

直接调用 EAS API 查询合同编号 256521SG046-XMCG-007 的信息
"""

import sys
import os

# 将项目根目录加入 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import importlib.util

_SCRIPT_PATH = os.path.join(
    PROJECT_ROOT, "src", "skills", "eas-contract-verify-1.0.0", "scripts", "eas_contract_verify.py"
)
_spec = importlib.util.spec_from_file_location("eas_contract_verify", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def main():
    contract_code = "256521SG046-XMCG-007"

    print(f"查询合同编号: {contract_code}")
    print("=" * 60)

    # 1. 查询合同列表
    print("\n--- 查询合同列表 (doType=list) ---")
    list_result = _mod.query_contract(contract_code)
    print(f"成功: {list_result.get('success')}")
    if list_result.get("success"):
        import json
        print(json.dumps(list_result.get("data", {}), ensure_ascii=False, indent=2))
    else:
        print(f"错误: {list_result.get('error')}")
        print(f"调试: {list_result.get('debug')}")

    # 2. 查询合同单据
    print(f"\n--- 查询合同单据 (doType=bill) ---")
    bill_result = _mod.query_contract_bill(contract_code)
    print(f"成功: {bill_result.get('success')}")
    if bill_result.get("success"):
        import json
        print(json.dumps(bill_result.get("data", {}), ensure_ascii=False, indent=2))
    else:
        print(f"错误: {bill_result.get('error')}")
        print(f"调试: {bill_result.get('debug')}")


if __name__ == "__main__":
    main()
