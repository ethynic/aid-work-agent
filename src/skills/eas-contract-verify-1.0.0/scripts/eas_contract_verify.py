"""
全筑EAS合同核对脚本

功能：
1. 调用 EAS 合同归档查询 API 获取合同信息
2. 上传合同附件至共享路径
3. 比对扫描件 OCR 信息与 EAS 系统数据
4. 一键合同归档自动化审核（解析 → 校验 → 比对 → 上传）

使用方式：
    python scripts/eas_contract_verify.py query --contract-code "HT-2024-001234"
    python scripts/eas_contract_verify.py upload --contract-code "HT-2024-001234" --file-path "/path/to/file.pdf"
    python scripts/eas_contract_verify.py compare --contract-code "HT-2024-001234" --ocr-data '{"party_a":"XX公司","party_b":"YY公司","amount":"100000"}'
    python scripts/eas_contract_verify.py verify --contract-code "HT-2024-001234" --file-path "/path/to/contract.pdf"
    python scripts/eas_contract_verify.py verify --contract-code "HT-2024-001234" --file-path "/path/to/contract.pdf" --debug
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# =============================================================================
# 配置
# =============================================================================

EAS_API_URL = os.environ.get(
    "EAS_API_URL", "https://dc.trendzone.com.cn/manage/api/eas_auto_contAttach"
)

# SMB 共享目录配置（支持 Windows / Linux / Docker 跨平台）
# - Windows 开发环境：使用 UNC 路径 + net use 命令建立凭据连接
# - Linux/Docker 生产环境：宿主机预先 mount.cifs 挂载，容器通过 volumes 映射
SMB_SERVER = os.environ.get("SMB_SERVER", "192.168.200.10")
SMB_SHARE_NAME = os.environ.get("SMB_SHARE_NAME", "AIUpload")
SMB_USERNAME = os.environ.get("SMB_USERNAME", "aiupload")
SMB_PASSWORD = os.environ.get("SMB_PASSWORD", "Ai@2025")

# 根据操作系统设置共享路径
# Windows: UNC 路径 \\server\share
# Linux/Docker: 挂载点路径（通过 SMB_MOUNT_POINT 环境变量配置，默认 /mnt/smb/AIUpload）
import platform as _platform
if _platform.system() == "Windows":
    SHARED_PATH = rf"\\{SMB_SERVER}\{SMB_SHARE_NAME}"
else:
    SHARED_PATH = os.environ.get("SMB_MOUNT_POINT", f"/mnt/smb/{SMB_SHARE_NAME}")

# 兼容旧变量名
SHARED_USERNAME = SMB_USERNAME
SHARED_PASSWORD = SMB_PASSWORD

DEFAULT_TIMEOUT = 30  # 秒


# =============================================================================
# 工具函数
# =============================================================================

def sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感信息"""
    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+',
        r'auth[_-]?token["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in sensitive_patterns:
        sanitized = re.sub(
            pattern,
            lambda m: m.group(0).split('=')[0] + '=***',
            sanitized,
            flags=re.IGNORECASE
        )
    return sanitized


def _safe_filename(filename: str) -> str:
    """将文件名中的中文字符替换为拼音或移除，确保文件名非中文"""
    # 移除或替换中文字符
    cleaned = re.sub(r'[\u4e00-\u9fff]', '_', filename)
    # 移除非法文件名字符
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', cleaned)
    # 移除多余的下划线
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    if not cleaned:
        cleaned = f"file_{uuid.uuid4().hex[:8]}"
    return cleaned


def _ensure_smb_connection() -> bool:
    """
    确保共享目录可访问（跨平台）

    - Windows: 使用 net use 命令建立 SMB 凭据连接
    - Linux/Docker: 期望宿主机已通过 mount.cifs 挂载，容器通过 volumes 映射；
      仅检查路径是否存在，不做运行时挂载（容器内通常无权限）

    Returns:
        True 表示连接成功或路径已就绪，False 表示失败
    """
    import subprocess as _sp

    if _platform.system() == "Windows":
        # Windows: 通过 net use 建立 SMB 凭据连接
        try:
            _net_use = _sp.run(
                f'net use "{SHARED_PATH}" /user:"{SMB_USERNAME}" "{SMB_PASSWORD}"',
                shell=True, capture_output=True, text=True
            )
            # 错误 1219 表示已有连接，忽略
            if _net_use.returncode != 0 and "1219" not in (_net_use.stderr + _net_use.stdout):
                logger.warning(f"后端日志：net use 返回: {_net_use.stderr.strip()}")
                return False
            return True
        except Exception as e:
            logger.error(f"后端日志：net use 执行失败: {e}")
            return False
    else:
        # Linux/Docker: 检查挂载点路径是否可访问
        if os.path.isdir(SHARED_PATH):
            # 尝试列出目录内容，验证读写权限
            try:
                os.listdir(SHARED_PATH)
                return True
            except PermissionError:
                logger.error(f"后端日志：共享路径无访问权限: {SHARED_PATH}")
                return False
        else:
            logger.error(
                f"后端日志：共享路径不存在: {SHARED_PATH}，"
                f"请在宿主机执行 mount.cifs 挂载，或在 docker-compose 中配置 volumes 映射"
            )
            return False


def _make_result(success: bool, data: Any = None, error: str = "", debug: str = "") -> Dict:
    """构造统一的结果字典"""
    result: Dict[str, Any] = {"success": success}
    if data is not None:
        result["data"] = data
    if error:
        result["error"] = error
    if debug:
        result["debug"] = debug
    if not success and not error:
        result["error"] = "未知错误"
    return result


# =============================================================================
# EAS API 调用日志（模块级，verify 流程收集后写入报告）
# =============================================================================

_eas_api_logs: list = []

def _call_eas_api(params: Dict[str, str], timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    调用 EAS 合同归档 API

    每次调用会自动将入参和响应记录到 _eas_api_logs 中，
    供 verify_and_archive_contract 保存到审核报告。

    Args:
        params: API 参数字典
        timeout: 超时时间（秒）

    Returns:
        API 返回的 JSON 数据
    """
    from datetime import datetime

    log_entry: Dict[str, Any] = {
        "url": EAS_API_URL,
        "params": {k: v for k, v in params.items()},
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        with httpx.Client(timeout=timeout, verify=False) as client:
            resp = client.post(EAS_API_URL, data=params)
            resp.raise_for_status()
            result = resp.json()

        log_entry["response"] = result
        _eas_api_logs.append(log_entry)
        return result
    except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, json.JSONDecodeError) as e:
        log_entry["error"] = sanitize_error_info(str(e))
        _eas_api_logs.append(log_entry)
        raise
    except RuntimeError:
        log_entry["error"] = "RuntimeError"
        _eas_api_logs.append(log_entry)
        raise


def query_contract(contract_code: str) -> Dict[str, Any]:
    """
    查询 EAS 合同归档信息

    优先使用 doType=bill 查询单据详情（包含 partA/partB/ofTax 等字段），
    如果 bill 未返回数据则 fallback 到 doType=list。

    Args:
        contract_code: 合同编号

    Returns:
        查询结果字典
    """
    if not contract_code or not contract_code.strip():
        return _make_result(False, error="合同编号不能为空")

    contract_code = contract_code.strip()

    # 优先尝试 bill 查询单据详情
    bill_result = query_contract_bill(contract_code)
    if bill_result.get("success"):
        data = bill_result.get("data", {})
        if data.get("contract_code"):
            return bill_result

    # fallback 到 list 查询
    try:
        result = _call_eas_api({
            "doType": "list",
            "contract_code": contract_code,
        })
    except RuntimeError as e:
        return _make_result(False, error="查询 EAS 合同信息失败", debug=sanitize_error_info(str(e)))

    # 解析 API 响应
    if not isinstance(result, dict):
        return _make_result(False, error="EAS API 返回数据格式异常", debug=f"返回类型: {type(result).__name__}")

    if not result.get("success"):
        msg = result.get("msg", "未知错误")
        return _make_result(False, error=f"EAS 查询失败: {msg}", debug=json.dumps(result, ensure_ascii=False))

    # 提取合同信息
    data = result.get("data", result)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return _make_result(True, data={"raw_data": data, "contract_code": contract_code})

    # 从 EAS 返回数据中提取关键字段
    contract_info = _extract_contract_fields(data)

    return _make_result(True, data=contract_info)


def query_contract_bill(contract_code: str) -> Dict[str, Any]:
    """
    查询 EAS 合同单据详情

    Args:
        contract_code: 合同编号

    Returns:
        查询结果字典
    """
    if not contract_code or not contract_code.strip():
        return _make_result(False, error="合同编号不能为空")

    contract_code = contract_code.strip()

    try:
        result = _call_eas_api({
            "doType": "bill",
            "contract_code": contract_code,
        })
    except RuntimeError as e:
        return _make_result(False, error="查询 EAS 合同单据失败", debug=sanitize_error_info(str(e)))

    if not isinstance(result, dict):
        return _make_result(False, error="EAS API 返回数据格式异常", debug=f"返回类型: {type(result).__name__}")

    if not result.get("success"):
        msg = result.get("msg", "未知错误")
        return _make_result(False, error=f"EAS 查询失败: {msg}", debug=json.dumps(result, ensure_ascii=False))

    data = result.get("data", result)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return _make_result(True, data={"raw_data": data, "contract_code": contract_code})

    contract_info = _extract_contract_fields(data)
    return _make_result(True, data=contract_info)


def _extract_contract_fields(data: Any) -> Dict[str, Any]:
    """
    从 EAS 返回数据中提取合同关键字段

    支持多种返回格式：
    - 列表格式：[{"contractCode": "...", ...}, ...]
    - 单对象格式：{"contractCode": "...", ...}
    - 嵌套格式：{"rows": [...], ...}

    Args:
        data: EAS API 返回的 data 部分

    Returns:
        标准化的合同信息字典
    """
    # 如果是列表，取第一个
    items = data
    if isinstance(data, dict):
        # 尝试从常见字段中获取列表
        for key in ("rows", "data", "records", "list", "items"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        else:
            # 单个对象
            items = [data]

    if not isinstance(items, list) or len(items) == 0:
        return {
            "contract_code": "",
            "party_a": "",
            "party_b": "",
            "amount": "",
            "contract_name": "",
            "raw_data": data,
            "match_count": 0,
        }

    # 取第一条记录
    item = items[0]
    if not isinstance(item, dict):
        return {
            "contract_code": "",
            "party_a": "",
            "party_b": "",
            "amount": "",
            "contract_name": "",
            "raw_data": data,
            "match_count": 0,
        }

    # 尝试多种字段名匹配
    contract_code = (
        item.get("contractCode") or item.get("contract_code") or
        item.get("contractNo") or item.get("contract_no") or
        item.get("code") or item.get("number") or ""
    )

    party_a = (
        item.get("partA") or
        item.get("partyA") or item.get("party_a") or
        item.get("firstParty") or item.get("first_party") or
        item.get("甲方") or item.get("customerName") or item.get("customer_name") or
        item.get("companyA") or item.get("company_a") or
        item.get("buyer") or item.get("buyUnit") or
        item.get("purchaseUnit") or item.get("sellUnit") or ""
    )

    party_b = (
        item.get("partB") or
        item.get("partyB") or item.get("party_b") or
        item.get("secondParty") or item.get("second_party") or
        item.get("乙方") or item.get("supplierName") or item.get("supplier_name") or
        item.get("companyB") or item.get("company_b") or
        item.get("seller") or item.get("supplyUnit") or ""
    )

    # 金额字段：0 和 0.0 也是合法值，不能用 or 链（Python 中 0.0 is falsy）
    amount = ""
    for _key in ("ofTax", "amount", "contractAmount", "contract_amount",
                 "totalAmount", "total_amount", "money", "合同金额"):
        _val = item.get(_key)
        if _val is not None and _val != "":
            amount = _val
            break

    contract_name = (
        item.get("contractName") or item.get("contract_name") or
        item.get("name") or item.get("title") or
        item.get("合同名称") or ""
    )

    return {
        "contract_code": str(contract_code).strip(),
        "party_a": str(party_a).strip(),
        "party_b": str(party_b).strip(),
        "amount": str(amount).strip(),
        "contract_name": str(contract_name).strip(),
        "raw_data": data,
        "match_count": len(items),
    }


# =============================================================================
# 文件上传
# =============================================================================

def upload_contract_attachment(contract_code: str, file_path: str, file_desc: str = "") -> Dict[str, Any]:
    """
    上传合同附件至共享路径并调用 EAS API 创建归档

    Args:
        contract_code: 合同编号
        file_path: 本地文件路径
        file_desc: 文件说明（可选，允许中文）

    Returns:
        操作结果字典
    """
    if not contract_code or not contract_code.strip():
        return _make_result(False, error="合同编号不能为空")

    if not file_path or not os.path.isfile(file_path):
        return _make_result(False, error=f"文件不存在: {file_path}")

    contract_code = contract_code.strip()
    original_filename = os.path.basename(file_path)

    # 1. 生成安全的文件名
    safe_filename = _safe_filename(original_filename)
    if not file_desc:
        file_desc = original_filename

    # 2. 复制文件到共享路径
    try:
        # 确保共享目录可访问（跨平台：Windows 用 net use，Linux 用预挂载路径）
        if not _ensure_smb_connection():
            return _make_result(
                False,
                error="文件上传失败：无法连接共享目录",
                debug=f"共享路径: {SHARED_PATH}，系统: {_platform.system()}，"
                      f"Windows 请检查 net use 命令，Linux/Docker 请检查宿主机 mount.cifs 挂载"
            )

        target_dir = os.path.join(SHARED_PATH, contract_code)
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, safe_filename)
        shutil.copy2(file_path, target_path)
    except PermissionError:
        return _make_result(
            False,
            error="文件上传失败：没有共享目录的写入权限",
            debug=f"目标路径: {target_dir}，请确认共享路径可访问且账户有写入权限"
        )
    except FileNotFoundError:
        return _make_result(
            False,
            error="文件上传失败：共享目录不存在",
            debug=f"共享路径: {SHARED_PATH}，请确认网络连接和共享路径配置正确"
        )
    except Exception as e:
        return _make_result(
            False,
            error="文件上传失败",
            debug=sanitize_error_info(str(e))
        )

    # 3. 调用 EAS API 创建归档
    try:
        result = _call_eas_api({
            "doType": "create",
            "contract_code": contract_code,
            "file_name": safe_filename,
            "file_desc": file_desc,
        })
    except RuntimeError as e:
        return _make_result(False, error="EAS 归档创建失败", debug=sanitize_error_info(str(e)))

    if not isinstance(result, dict):
        return _make_result(False, error="EAS API 返回数据格式异常", debug=f"返回类型: {type(result).__name__}")

    if not result.get("success"):
        msg = result.get("msg", "未知错误")
        return _make_result(False, error=f"EAS 归档创建失败: {msg}", debug=json.dumps(result, ensure_ascii=False))

    return _make_result(True, data={
        "contract_code": contract_code,
        "file_name": safe_filename,
        "original_filename": original_filename,
        "file_desc": file_desc,
        "shared_path": os.path.join(target_dir, safe_filename),
        "eas_response": result,
    })


# =============================================================================
# 合同比对
# =============================================================================

def compare_contract(contract_code: str, ocr_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    比对 OCR 解析的合同信息与 EAS 系统数据

    Args:
        contract_code: 合同编号
        ocr_data: OCR 解析出的合同信息字典，包含：
            - party_a: 甲方名称
            - party_b: 乙方名称
            - amount: 合同金额
            - stamp_info: 盖章信息（可选）

    Returns:
        比对结果字典，格式：
        {
            "result": "yes" | "no",
            "message": "匹配说明或不一致原因"
        }
    """
    # 验证 OCR 数据
    required_fields = ["party_a", "party_b", "amount"]
    missing = [f for f in required_fields if not ocr_data.get(f)]
    if missing:
        return {
            "result": "no",
            "message": f"OCR 数据缺少必要字段: {', '.join(missing)}"
        }

    # 查询 EAS 数据
    query_result = query_contract(contract_code)
    if not query_result.get("success"):
        error_msg = query_result.get("error", "EAS 查询失败")
        return {
            "result": "no",
            "message": f"EAS 查询失败: {error_msg}"
        }

    eas_data = query_result.get("data", {})
    discrepancies = []

    # 比对甲方
    ocr_party_a = _normalize_text(ocr_data.get("party_a", ""))
    eas_party_a = _normalize_text(eas_data.get("party_a", ""))
    if ocr_party_a and eas_party_a:
        if not _text_similar(ocr_party_a, eas_party_a):
            discrepancies.append(f"甲方不一致: 扫描件为\"{ocr_data['party_a']}\", EAS为\"{eas_data['party_a']}\"")
    elif ocr_party_a and not eas_party_a:
        discrepancies.append(f"EAS中未找到甲方信息(扫描件: \"{ocr_data['party_a']}\")")
    elif not ocr_party_a and eas_party_a:
        discrepancies.append(f"扫描件中未识别到甲方信息(EAS: \"{eas_data['party_a']}\")")

    # 比对乙方
    ocr_party_b = _normalize_text(ocr_data.get("party_b", ""))
    eas_party_b = _normalize_text(eas_data.get("party_b", ""))
    if ocr_party_b and eas_party_b:
        if not _text_similar(ocr_party_b, eas_party_b):
            discrepancies.append(f"乙方不一致: 扫描件为\"{ocr_data['party_b']}\", EAS为\"{eas_data['party_b']}\"")
    elif ocr_party_b and not eas_party_b:
        discrepancies.append(f"EAS中未找到乙方信息(扫描件: \"{ocr_data['party_b']}\")")
    elif not ocr_party_b and eas_party_b:
        discrepancies.append(f"扫描件中未识别到乙方信息(EAS: \"{eas_data['party_b']}\")")

    # 比对金额
    ocr_amount = _normalize_amount(ocr_data.get("amount", ""))
    eas_amount = _normalize_amount(eas_data.get("amount", ""))
    if ocr_amount is not None and eas_amount is not None:
        if not _amount_equal(ocr_amount, eas_amount):
            discrepancies.append(
                f"合同金额不一致: 扫描件为\"{ocr_data['amount']}\", EAS为\"{eas_data['amount']}\""
            )
    elif ocr_amount is not None and eas_amount is None:
        discrepancies.append(f"EAS中未找到合同金额(扫描件: \"{ocr_data['amount']}\")")
    elif ocr_amount is None and eas_amount is not None:
        discrepancies.append(f"扫描件中未识别到合同金额(EAS: \"{eas_data['amount']}\")")

    # 构造结果
    if discrepancies:
        return {
            "result": "no",
            "message": "; ".join(discrepancies),
            "details": {
                "ocr_data": ocr_data,
                "eas_data": {
                    "contract_code": eas_data.get("contract_code", ""),
                    "party_a": eas_data.get("party_a", ""),
                    "party_b": eas_data.get("party_b", ""),
                    "amount": eas_data.get("amount", ""),
                    "contract_name": eas_data.get("contract_name", ""),
                },
                "discrepancies": discrepancies,
            }
        }
    else:
        return {
            "result": "yes",
            "message": "合同信息一致",
            "details": {
                "ocr_data": ocr_data,
                "eas_data": {
                    "contract_code": eas_data.get("contract_code", ""),
                    "party_a": eas_data.get("party_a", ""),
                    "party_b": eas_data.get("party_b", ""),
                    "amount": eas_data.get("amount", ""),
                    "contract_name": eas_data.get("contract_name", ""),
                },
            }
        }


# =============================================================================
# 一键合同归档验证
# =============================================================================

def _save_verify_report(result: Dict[str, Any], file_path: str, contract_code: str) -> str:
    """
    将审核报告保存到 JSON 文件。

    文件保存在与原始 PDF 相同的目录下，命名为 {合同编号}_verify_report.json。
    无论审核成功或失败都会保存，记录已获取的数据和不通过原因。

    Args:
        result: verify_and_archive_contract 的返回值
        file_path: 原始 PDF 文件路径
        contract_code: 合同编号

    Returns:
        保存的报告文件路径
    """
    from datetime import datetime

    try:
        now = datetime.now()
        status = "成功" if result.get("result") == "yes" else "失败"
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        report_filename = f"{contract_code}_{timestamp}_{status}.json"
        pdf_dir = os.path.dirname(os.path.abspath(file_path))
        report_path = os.path.join(pdf_dir, report_filename)

        report = {
            "contract_code": contract_code,
            "source_file": os.path.basename(file_path),
            "verify_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "result": result.get("result"),
            "message": result.get("message", ""),
        }

        details = result.get("details", {})

        # 记录 OCR 解析获取到的数据
        llm = details.get("llm_analysis", {})
        if llm:
            report["ocr_data"] = {
                "is_contract": llm.get("is_contract"),
                "is_approval_form_first_page": llm.get("is_approval_form_first_page"),
                "contract_name": llm.get("contract_name", ""),
                "contract_code_from_ocr": llm.get("contract_code", ""),
                "party_a": llm.get("party_a", ""),
                "party_b": llm.get("party_b", ""),
                "total_amount": llm.get("total_amount", ""),
                "has_party_a_stamp": llm.get("has_party_a_stamp"),
                "has_party_b_stamp": llm.get("has_party_b_stamp"),
            }

        # 记录骑缝章和页数
        report["basic_checks"] = {
            "total_pages": details.get("total_pages", 0),
            "has_riding_seal": details.get("has_riding_seal"),
            "is_contract": details.get("is_contract"),
            "has_approval_form": details.get("has_approval_form"),
            "has_party_a_stamp": details.get("has_party_a_stamp"),
            "has_party_b_stamp": details.get("has_party_b_stamp"),
        }

        # 记录 EAS 比对数据（不论成功失败）
        comparison = details.get("comparison")
        if comparison:
            report["eas_comparison"] = {
                "result": comparison.get("result"),
                "message": comparison.get("message", ""),
                "details": comparison.get("details"),
            }

        # 记录上传结果
        upload = details.get("upload")
        if upload:
            report["upload"] = {
                "success": upload.get("success"),
                "error": upload.get("error", ""),
            }
            if upload.get("success") and upload.get("data"):
                report["upload"]["shared_path"] = upload["data"].get("shared_path", "")

        # 完整 details 作为 debug 信息（可选）
        if result.get("result") == "no":
            report["full_details"] = details

        # 记录所有 EAS API 调用的入参和响应
        if _eas_api_logs:
            report["eas_api_logs"] = list(_eas_api_logs)

        os.makedirs(pdf_dir, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 将报告路径附加到 result 中
        result["report_file"] = report_path
        logger.info(f"后端日志：审核报告已保存: {report_path}")
        return report_path

    except Exception as e:
        logger.warning(f"后端日志：保存审核报告失败: {e}")
        return ""

def verify_and_archive_contract(
    file_path: str,
    contract_code: str,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    合同归档自动化审核主流程

    完整步骤：
    1. 解析合同PDF（OCR + LLM分析）
    2. 基础校验：是否为合同、是否有审批单、是否有骑缝章、是否有甲乙双方盖章
    3. 通过EAS API比对甲方、乙方、金额
    4. 比对一致则上传附件到EAS

    无论成功或失败，都会将审核报告保存到与 PDF 同目录的 JSON 文件中。

    Args:
        file_path: 合同PDF文件路径
        contract_code: EAS合同编号
        debug: 是否输出调试信息

    Returns:
        {
            "result": "yes" | "no",
            "message": "...",
            "details": {...},
            "report_file": "保存的报告文件路径"
        }
    """
    from parse_contract_pdf import parse_contract_pdf

    details: Dict[str, Any] = {"contract_code": contract_code, "file_path": file_path}

    # 清空 EAS API 调用日志，开始收集本次审核的所有调用
    global _eas_api_logs
    _eas_api_logs = []

    # ------------------------------------------------------------------
    # 步骤 1：解析合同 PDF
    # ------------------------------------------------------------------
    parse_result = parse_contract_pdf(file_path, debug=debug)
    if not parse_result.get("success"):
        result = {
            "result": "no",
            "message": f"合同PDF解析失败: {parse_result.get('error', '未知错误')}",
            "details": details,
        }
        _save_verify_report(result, file_path, contract_code)
        return result

    data = parse_result["data"]
    llm = data.get("llm_analysis", {})
    details["total_pages"] = data.get("total_pages", 0)
    details["has_riding_seal"] = data.get("has_riding_seal", False)
    details["llm_analysis"] = llm

    # ------------------------------------------------------------------
    # 步骤 2：基础校验
    # ------------------------------------------------------------------
    check_failures = []

    # 2a. 是否为合同
    if not llm.get("is_contract"):
        check_failures.append("文档不是合同或未识别为合同")
        details["is_contract"] = False
    else:
        details["is_contract"] = True

    # 2b. 是否有审批单
    if not llm.get("is_approval_form_first_page"):
        check_failures.append("未检测到合同审批单（第一页非审批单）")
        details["has_approval_form"] = False
    else:
        details["has_approval_form"] = True

    # 2c. 是否有骑缝章（全局汇总）— 仅记录，不作为不通过条件
    details["has_riding_seal"] = data.get("has_riding_seal", False)

    # 2d. 是否有甲乙双方盖章
    if not llm.get("has_party_a_stamp"):
        check_failures.append("未检测到甲方盖章")
        details["has_party_a_stamp"] = False
    else:
        details["has_party_a_stamp"] = True

    if not llm.get("has_party_b_stamp"):
        check_failures.append("未检测到乙方盖章")
        details["has_party_b_stamp"] = False
    else:
        details["has_party_b_stamp"] = True

    if check_failures:
        result = {
            "result": "no",
            "message": "合同基础校验不通过: " + "; ".join(check_failures),
            "details": details,
        }
        _save_verify_report(result, file_path, contract_code)
        return result

    # ------------------------------------------------------------------
    # 步骤 3：与 EAS 系统数据比对
    # ------------------------------------------------------------------
    ocr_data = {
        "party_a": llm.get("party_a", ""),
        "party_b": llm.get("party_b", ""),
        "amount": llm.get("total_amount", ""),
    }
    comparison = compare_contract(contract_code, ocr_data)
    details["comparison"] = comparison

    if comparison.get("result") != "yes":
        result = {
            "result": "no",
            "message": f"合同与EAS系统数据不一致: {comparison.get('message', '')}",
            "details": details,
        }
        _save_verify_report(result, file_path, contract_code)
        return result

    # ------------------------------------------------------------------
    # 步骤 4：比对一致 → 上传附件
    # ------------------------------------------------------------------
    upload_result = upload_contract_attachment(
        contract_code,
        file_path,
        file_desc=os.path.basename(file_path),
    )
    details["upload"] = upload_result

    if not upload_result.get("success"):
        result = {
            "result": "no",
            "message": f"附件上传失败: {upload_result.get('error', '未知错误')}",
            "details": details,
        }
        _save_verify_report(result, file_path, contract_code)
        return result

    result = {
        "result": "yes",
        "message": "合同归档自动化审核通过，附件已上传至EAS系统。下一步将通过OA审批流程完成审批。",
        "details": details,
    }
    _save_verify_report(result, file_path, contract_code)
    return result


def _normalize_text(text: str) -> str:
    """标准化文本：去除空白、标点，统一为小写"""
    if not text:
        return ""
    # 去除常见括号和标点
    text = re.sub(r'[（）()\[\]【】《》<>""\'\'\-,，。、；;：:]', '', text)
    # 去除多余空白
    text = re.sub(r'\s+', '', text)
    return text.lower()


def _normalize_amount(amount_str: str) -> Optional[float]:
    """
    标准化金额字符串为数值

    支持格式：
    - 100000
    - 100,000.00
    - ¥100000
    - ￥100,000.00
    - 100000元
    - 壹拾万元整（大写数字暂不支持，需先转换）

    Returns:
        数值或 None（无法解析时）
    """
    if not amount_str:
        return None

    # 去除货币符号和单位
    amount_str = re.sub(r'[¥￥元圆]', '', amount_str).strip()

    # 处理大写数字（基础转换）
    cn_num_map = {
        '零': 0, '〇': 0, '一': 1, '壹': 1, '二': 2, '贰': 2, '两': 2,
        '三': 3, '叁': 3, '四': 4, '肆': 4, '五': 5, '伍': 5,
        '六': 6, '陆': 6, '七': 7, '柒': 7, '八': 8, '捌': 8,
        '九': 9, '玖': 9, '十': 10, '拾': 10, '百': 100, '佰': 100,
        '千': 1000, '仟': 1000, '万': 10000, '亿': 100000000,
    }

    # 尝试大写数字转换
    if re.search(r'[壹贰叁肆伍陆柒捌玖拾佰仟万亿]', amount_str):
        try:
            return _cn_amount_to_num(amount_str)
        except (ValueError, IndexError):
            pass

    # 去除逗号
    amount_str = amount_str.replace(',', '')

    try:
        return float(amount_str)
    except ValueError:
        return None


def _cn_amount_to_num(cn_amount: str) -> float:
    """将中文大写金额转换为数字"""
    cn_num_map = {
        '零': 0, '〇': 0, '一': 1, '壹': 1, '二': 2, '贰': 2, '两': 2,
        '三': 3, '叁': 3, '四': 4, '肆': 4, '五': 5, '伍': 5,
        '六': 6, '陆': 6, '七': 7, '柒': 7, '八': 8, '捌': 8,
        '九': 9, '玖': 9, '十': 10, '拾': 10, '百': 100, '佰': 100,
        '千': 1000, '仟': 1000, '万': 10000, '亿': 100000000,
    }

    # 去除"元整"等后缀
    cn_amount = re.sub(r'[元圆整正角分]*$', '', cn_amount).strip()

    result = 0
    temp = 0
    for char in cn_amount:
        if char in cn_num_map:
            val = cn_num_map[char]
            if val >= 10000:
                result += temp * val
                temp = 0
            elif val >= 10:
                if temp == 0:
                    temp = 1
                temp *= val
            else:
                temp = val
        elif char == '点' or char == '.':
            break

    result += temp
    return float(result)


def _text_similar(text1: str, text2: str, threshold: float = 0.8) -> bool:
    """
    判断两个文本是否相似

    使用最长公共子序列比例作为相似度。
    自动进行文本标准化（去除标点、空格、统一大小写）后再比较。

    Args:
        text1: 文本1
        text2: 文本2
        threshold: 相似度阈值，默认 0.8

    Returns:
        是否相似
    """
    if not text1 or not text2:
        return text1 == text2

    # 标准化后再比较
    text1 = _normalize_text(text1)
    text2 = _normalize_text(text2)

    if text1 == text2:
        return True

    # 完全包含
    if text1 in text2 or text2 in text1:
        return True

    # LCS 相似度
    lcs_len = _lcs_length(text1, text2)
    max_len = max(len(text1), len(text2))
    similarity = lcs_len / max_len if max_len > 0 else 0

    return similarity >= threshold


def _lcs_length(s1: str, s2: str) -> int:
    """计算两个字符串的最长公共子序列长度"""
    m, n = len(s1), len(s2)
    # 优化空间复杂度
    if m < n:
        s1, s2 = s2, s1
        m, n = n, m

    prev = [0] * (n + 1)
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (n + 1)

    return prev[n]


def _amount_equal(amount1: float, amount2: float, tolerance: float = 0.01) -> bool:
    """判断两个金额是否相等（允许微小误差）"""
    if amount1 is None or amount2 is None:
        return False
    if amount1 == 0 and amount2 == 0:
        return True
    if amount1 == 0 or amount2 == 0:
        return False
    # 相对误差
    rel_diff = abs(amount1 - amount2) / max(abs(amount1), abs(amount2))
    return rel_diff < tolerance


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="全筑EAS合同核对工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # query 命令
    query_parser = subparsers.add_parser("query", help="查询 EAS 合同信息")
    query_parser.add_argument("--contract-code", required=True, help="合同编号")

    # upload 命令
    upload_parser = subparsers.add_parser("upload", help="上传合同附件并创建归档")
    upload_parser.add_argument("--contract-code", required=True, help="合同编号")
    upload_parser.add_argument("--file-path", required=True, help="本地文件路径")
    upload_parser.add_argument("--file-desc", default="", help="文件说明（可选，允许中文）")

    # compare 命令
    compare_parser = subparsers.add_parser("compare", help="比对 OCR 数据与 EAS 数据")
    compare_parser.add_argument("--contract-code", required=True, help="合同编号")
    compare_parser.add_argument("--ocr-data", required=True, help="OCR 解析的 JSON 数据")

    # verify 命令（一键合同归档审核）
    verify_parser = subparsers.add_parser("verify", help="一键合同归档自动化审核（解析+校验+比对+上传）")
    verify_parser.add_argument("--contract-code", required=True, help="EAS合同编号")
    verify_parser.add_argument("--file-path", required=True, help="合同PDF文件路径")
    verify_parser.add_argument("--debug", action="store_true", help="输出调试信息")

    args = parser.parse_args()

    if args.command == "query":
        result = query_contract(args.contract_code)
    elif args.command == "upload":
        result = upload_contract_attachment(
            args.contract_code,
            args.file_path,
            args.file_desc,
        )
    elif args.command == "compare":
        try:
            ocr_data = json.loads(args.ocr_data)
        except json.JSONDecodeError as e:
            result = _make_result(False, error=f"OCR 数据 JSON 解析失败: {e}")
        else:
            result = compare_contract(args.contract_code, ocr_data)
    elif args.command == "verify":
        result = verify_and_archive_contract(
            args.file_path,
            args.contract_code,
            debug=args.debug,
        )
    else:
        parser.print_help()
        sys.exit(1)

    # 输出 JSON 结果
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
