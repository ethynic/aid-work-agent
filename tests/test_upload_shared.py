"""测试将 PDF 上传到共享文件夹"""

import os, sys, json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import importlib.util
_SCRIPT = os.path.join(PROJECT_ROOT, "src", "skills", "eas-contract-verify-1.0.0", "scripts", "eas_contract_verify.py")
_spec = importlib.util.spec_from_file_location("eas_contract_verify", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

FILE_PATH = os.path.join(PROJECT_ROOT, "uploads", "file_479706a1fa2f.pdf")
CONTRACT_CODE = "256521SG046-XMCG-007"

result = _mod.upload_contract_attachment(CONTRACT_CODE, FILE_PATH)
print(json.dumps(result, ensure_ascii=False, indent=2))

