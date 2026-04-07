"""
合同PDF OCR解析脚本

使用 PaddleOCR 解析上传的合同扫描件PDF，提取：
1. 按页输出：页码、页宽、页高、markdown文本
2. 每页骑缝章检测：图片右下角x坐标距页宽不超过20像素
3. 每页可疑图片检测：图片宽高在200-250之间
4. 将所有页面文本传给大模型解析：是否为合同、第一页是否为审批单、合同名称、甲方、乙方、总金额、盖章页

使用方式：
    python scripts/parse_contract_pdf.py --file-path "/path/to/contract.pdf"
    python scripts/parse_contract_pdf.py --file-path "/path/to/contract.pdf" --output-dir "./output"
    python scripts/parse_contract_pdf.py --file-path "/path/to/contract.pdf" --debug
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

import httpx

# 自动加载项目根目录 .env 文件中的环境变量，手动运行脚本时无需手动 export
try:
    from dotenv import load_dotenv
    _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    _env_file = os.path.join(_project_root, ".env")
    if os.path.isfile(_env_file):
        load_dotenv(_env_file, override=True)
except ImportError:
    pass

logger = logging.getLogger(__name__)

# =============================================================================
# PaddleOCR 配置
# =============================================================================

DEFAULT_TIMEOUT = 600  # seconds


def _get_paddleocr_config() -> Tuple[str, str]:
    """获取 PaddleOCR API URL 和 Token（直接从环境变量读取，不依赖 src.config.settings）"""
    api_url = os.getenv("PADDLEOCR_DOC_PARSING_API_URL", "").strip()
    token = os.getenv("PADDLEOCR_ACCESS_TOKEN", "").strip()

    if not api_url:
        raise ValueError(
            "PADDLEOCR_DOC_PARSING_API_URL 未配置，请设置环境变量 PADDLEOCR_DOC_PARSING_API_URL"
        )
    if not token:
        raise ValueError(
            "PADDLEOCR_ACCESS_TOKEN 未配置，请设置环境变量 PADDLEOCR_ACCESS_TOKEN"
        )

    if not api_url.startswith(("http://", "https://")):
        api_url = f"https://{api_url}"

    return api_url, token


def _load_file_as_base64(file_path: str) -> str:
    """加载本地文件并编码为 base64"""
    path = os.path.abspath(file_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _call_paddleocr(file_base64: str, file_type: int = 0) -> Dict[str, Any]:
    """调用 PaddleOCR 文档解析 API，返回完整原始结果"""
    api_url, token = _get_paddleocr_config()

    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "Client-Platform": "official-skill",
    }

    params: Dict[str, Any] = {
        "file": file_base64,
        "fileType": file_type,
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }

    timeout = float(os.getenv("PADDLEOCR_DOC_PARSING_TIMEOUT", str(DEFAULT_TIMEOUT)))

    try:
        with httpx.Client(timeout=timeout, verify=False) as client:
            resp = client.post(api_url, json=params, headers=headers)
            resp.raise_for_status()
            result = resp.json()
    except httpx.TimeoutException:
        raise RuntimeError(f"API 请求超时（{timeout}秒）")
    except httpx.RequestError as e:
        raise RuntimeError(f"API 请求失败: {sanitize_error_info(str(e))}")
    except json.JSONDecodeError:
        raise RuntimeError("API 返回了无效的 JSON 响应")

    if isinstance(result, dict) and result.get("errorCode", 0) != 0:
        raise RuntimeError(f"API 错误: {result.get('errorMsg', '未知错误')}")

    return result


def sanitize_error_info(error_msg: str) -> str:
    """过滤敏感信息"""
    patterns = [
        r'password["\s:=]+\S+', r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+', r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+', r'access[_-]?key["\s:=]+\S+',
    ]
    sanitized = error_msg
    for p in patterns:
        sanitized = re.sub(p, lambda m: m.group(0).split('=')[0] + '=***', sanitized, flags=re.IGNORECASE)
    return sanitized


# =============================================================================
# 页面信息提取
# =============================================================================

def _get_page_size(page_result: Dict) -> Tuple[int, int]:
    """
    从 PaddleOCR 结果中获取页面尺寸

    Returns:
        (width, height)，获取失败返回 (0, 0)
    """
    try:
        return page_result['prunedResult'].get("width", 0), page_result['prunedResult'].get("height", 0)
    except (IndexError, TypeError, AttributeError, KeyError):
        pass
    return 0, 0


def _get_page_text(page_result: Dict) -> str:
    """获取页面 markdown.text"""
    try:
        return page_result.get("markdown", {}).get("text", "")
    except (AttributeError, TypeError):
        return ""


def _get_page_images(page_result: Dict) -> Dict[str, str]:
    """
    获取页面中所有图片 {文件名: URL}

    图片文件名格式示例：
      imgs/img_in_seal_box_507_6_692_136.jpg
      imgs/img_in_image_box_1_0_132_121.jpg
    """
    try:
        return page_result.get("markdown", {}).get("images", {})
    except (AttributeError, TypeError):
        return {}


def _parse_image_coords(filename: str) -> Optional[Dict[str, int]]:
    """
    从图片文件名中解析坐标

    文件名格式：img_in_{type}_box_{x1}_{y1}_{x2}_{y2}.jpg
    示例：img_in_seal_box_507_6_692_136.jpg -> {type: "seal", x1: 507, y1: 6, x2: 692, y2: 136}

    Returns:
        坐标字典或 None
    """
    basename = os.path.basename(filename)
    match = re.match(r'img_in_(\w+)_box_(\d+)_(\d+)_(\d+)_(\d+)\.', basename)
    if match:
        return {
            "type": match.group(1),
            "x1": int(match.group(2)),
            "y1": int(match.group(3)),
            "x2": int(match.group(4)),
            "y2": int(match.group(5)),
        }
    return None


# =============================================================================
# 单页分析：骑缝章 + 可疑图片
# =============================================================================

def _analyze_page_images(
    images: Dict[str, str],
    page_width: int,
    page_height: int,
    riding_seal_tolerance: int = 50,
    suspicious_size_min: int = 200,
    suspicious_size_max: int = 250,
) -> Dict[str, Any]:
    """
    分析页面中的图片，检测骑缝章和可疑图片

    Args:
        images: 页面图片字典 {文件名: URL}
        page_width: 页面宽度（像素）
        page_height: 页面高度（像素）
        riding_seal_tolerance: 骑缝章判定阈值，图片x2距页宽不超过此像素数
        suspicious_size_min: 可疑图片最小宽/高
        suspicious_size_max: 可疑图片最大宽/高

    Returns:
        {
            "has_riding_seal": bool,
            "seal_images": [{filename, url, coords, distance_to_edge}, ...],
            "suspicious_images": [{filename, url, coords, width, height}, ...],
        }
    """
    if not images or page_width <= 0:
        return {"has_riding_seal": False, "seal_images": [], "suspicious_images": []}

    seal_images = []
    suspicious_images = []

    for filename, url in images.items():
        coords = _parse_image_coords(filename)
        if not coords:
            continue

        img_width = coords["x2"] - coords["x1"]
        img_height = coords["y2"] - coords["y1"]

        # 骑缝章判定：任意图片（不限类型）的 x2 距页宽不超过 riding_seal_tolerance 像素
        distance_to_edge = page_width - coords["x2"]
        if 0 <= distance_to_edge <= riding_seal_tolerance:
            seal_images.append({
                "filename": filename,
                "url": url,
                "coords": coords,
                "distance_to_edge": distance_to_edge,
            })

        # 可疑图片判定：宽高均在 suspicious_size_min ~ suspicious_size_max 之间
        if suspicious_size_min <= img_width <= suspicious_size_max and \
           suspicious_size_min <= img_height <= suspicious_size_max:
            suspicious_images.append({
                "filename": filename,
                "url": url,
                "coords": coords,
                "width": img_width,
                "height": img_height,
            })

    return {
        "has_riding_seal": len(seal_images) > 0,
        "seal_images": seal_images,
        "suspicious_images": suspicious_images,
    }


# =============================================================================
# LLM 调用（使用项目默认 provider）
# =============================================================================

def _get_llm_config() -> Dict[str, str]:
    """
    从环境变量读取 LLM 配置，返回 {provider, api_key, model}

    与 config.yaml 中 llm.provider 保持一致，优先使用 zhipu。
    """
    provider = os.getenv("LLM_PROVIDER", os.getenv("LLM_DEFAULT_PROVIDER", "zhipu")).strip().lower()

    if provider == "zhipu":
        api_key = os.getenv("ZHIPU_API_KEYS", "").strip().split(",")[0].strip()
        model = os.getenv("ZHIPU_MODEL", "glm-4")
        return {"provider": "zhipu", "api_key": api_key, "model": model}
    elif provider == "qwen":
        api_key = os.getenv("QWEN_API_KEYS", "").strip().split(",")[0].strip()
        model = os.getenv("QWEN_MODEL", "qwen-plus")
        return {"provider": "qwen", "api_key": api_key, "model": model}
    else:
        raise ValueError(f"不支持的 LLM 提供者: {provider}，请设置环境变量 LLM_PROVIDER 为 zhipu 或 qwen")


CONTRACT_ANALYSIS_SYSTEM_PROMPT = """你是一个专业的合同文档分析助手。请根据提供的合同PDF各页文本内容，分析并返回以下信息：

要求：
1. 严格以 JSON 格式返回，不要有任何其他文字说明
2. 所有金额必须是纯数字（如 150000），不要带货币符号或中文
3. 如果某项信息无法确定，填空字符串 ""

返回 JSON 格式：
{
  "is_contract": true/false,
  "is_approval_form_first_page": true/false,
  "contract_name": "合同名称",
  "party_a": "甲方全称",
  "party_b": "乙方全称",
  "total_amount": "纯数字金额",
  "stamp_pages": [页码列表，如 [3, 5, 8]],
  "has_party_a_stamp": true/false,
  "has_party_b_stamp": true/false,
  "contract_code": "合同编号"
}

字段说明：
- is_contract: 整个文档是否为合同（或包含合同内容）
- is_approval_form_first_page: 第一页是否为合同审批单/用印审批单
- contract_name: 合同名称/标题
- party_a: 甲方全称
- party_b: 乙方全称
- total_amount: 合同总金额，纯数字（如 150000.00），不带货币符号和中文
- stamp_pages: 合同中需要双方盖章（签字/盖章页）的页码列表（1-based）
- has_party_a_stamp: 甲方是否在合同中盖章（根据盖章页和文本内容判断）
- has_party_b_stamp: 乙方是否在合同中盖章（根据盖章页和文本内容判断）
- contract_code: 合同编号（如果文档中能识别到的话）"""


def _call_llm_analyze(pages_text: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    调用大模型分析合同内容

    Args:
        pages_text: [{"index": 1, "text": "..."}, ...] 每页的文本和页码

    Returns:
        LLM 解析结果
    """
    config = _get_llm_config()

    if not config["api_key"]:
        raise ValueError(
            f"LLM API Key 未配置，请在 .env 中设置 "
            f"{'ZHIPU_API_KEYS' if config['provider'] == 'zhipu' else 'QWEN_API_KEYS'}"
        )

    # 构造用户消息
    pages_content = ""
    for page in pages_text:
        text = page["text"][:3000] if page["text"] else "(空白页)"
        pages_content += f"\n--- 第 {page['index']} 页 ---\n{text}\n"

    user_message = f"请分析以下合同PDF的各页内容：\n{pages_content}"

    if config["provider"] == "zhipu":
        return _call_zhipu(config, user_message)
    elif config["provider"] == "qwen":
        return _call_qwen(config, user_message)
    else:
        raise ValueError(f"不支持的 provider: {config['provider']}")


def _call_zhipu(config: Dict[str, str], user_message: str) -> Dict[str, Any]:
    """调用智谱 GLM API"""
    import asyncio

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": CONTRACT_ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
    }

    async def _request():
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()

    result = asyncio.run(_request())
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _parse_llm_json_response(content)


def _call_qwen(config: Dict[str, str], user_message: str) -> Dict[str, Any]:
    """调用通义千问 API"""
    import asyncio

    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config["model"],
        "input": {
            "messages": [
                {"role": "system", "content": CONTRACT_ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ]
        },
        "parameters": {
            "temperature": 0.1,
            "max_tokens": 2048,
            "result_format": "message",
        },
    }

    async def _request():
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()

    result = asyncio.run(_request())
    content = result.get("output", {}).get("choices", [{}])[0].get("message", {}).get("content", "")
    return _parse_llm_json_response(content)


def _parse_llm_json_response(content: str) -> Dict[str, Any]:
    """解析 LLM 返回的 JSON 内容"""
    if not content:
        return {"parse_error": "LLM 返回为空"}

    # 尝试提取 JSON 块（LLM 可能用 ```json 包裹）
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
    if json_match:
        content = json_match.group(1)

    # 尝试找到 JSON 对象
    brace_match = re.search(r'\{[\s\S]*\}', content)
    if brace_match:
        content = brace_match.group(0)

    try:
        parsed = json.loads(content)
        # 规范化字段
        return {
            "is_contract": bool(parsed.get("is_contract", False)),
            "is_approval_form_first_page": bool(parsed.get("is_approval_form_first_page", False)),
            "contract_name": str(parsed.get("contract_name", "")),
            "party_a": str(parsed.get("party_a", "")),
            "party_b": str(parsed.get("party_b", "")),
            "total_amount": str(parsed.get("total_amount", "")),
            "stamp_pages": [int(p) for p in parsed.get("stamp_pages", []) if isinstance(p, (int, float, str))],
            "has_party_a_stamp": bool(parsed.get("has_party_a_stamp", False)),
            "has_party_b_stamp": bool(parsed.get("has_party_b_stamp", False)),
            "contract_code": str(parsed.get("contract_code", "")),
        }
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return {"parse_error": f"LLM 返回 JSON 解析失败: {e}", "raw_response": content}


# =============================================================================
# 主解析函数
# =============================================================================

def parse_contract_pdf(file_path: str, debug: bool = False) -> Dict[str, Any]:
    """
    解析合同PDF文件

    步骤：
    1. 调用 PaddleOCR 解析 PDF
    2. 按页提取信息（页码、宽高、文本、骑缝章、可疑图片）
    3. 汇总全局骑缝章检测结果（任一页有骑缝章即判定为有）
    4. 将所有页面文本传给 LLM 分析合同信息
    5. 合并 PaddleOCR 和 LLM 结果返回

    Args:
        file_path: PDF文件路径
        debug: 是否输出原始OCR结果（仅在调试时启用，输出较大）

    Returns:
        {success: True, data: {pages: [...], has_riding_seal: bool, llm_analysis: {...}, ...}}
    """
    if not file_path or not os.path.isfile(file_path):
        return {"success": False, "error": f"文件不存在: {file_path}"}

    # 1. 调用 PaddleOCR 解析
    try:
        file_b64 = _load_file_as_base64(file_path)
        ocr_result = _call_paddleocr(file_b64, file_type=0)
    except (FileNotFoundError, RuntimeError) as e:
        logger.error(f"后端日志：OCR解析失败: {e}", exc_info=True)
        return {"success": False, "error": "OCR解析失败", "debug": sanitize_error_info(str(e))}

    # 2. 提取页面结果
    try:
        raw_result = ocr_result.get("result", ocr_result)
        pages_raw = raw_result.get("layoutParsingResults", [])
    except (AttributeError, TypeError):
        return {"success": False, "error": "OCR返回数据格式异常"}

    if not pages_raw:
        return {"success": False, "error": "OCR未解析到任何页面内容"}

    # 3. 按页分析
    pages: List[Dict[str, Any]] = []
    pages_text_for_llm: List[Dict[str, str]] = []

    for i, page_result in enumerate(pages_raw):
        page_index = i + 1  # 1-based
        page_width, page_height = _get_page_size(page_result)
        text = _get_page_text(page_result)
        images = _get_page_images(page_result)

        image_analysis = _analyze_page_images(images, page_width, page_height)

        page_data: Dict[str, Any] = {
            "page_index": page_index,
            "page_width": page_width,
            "page_height": page_height,
            "text": text,
            "has_riding_seal": image_analysis["has_riding_seal"],
            "seal_images": image_analysis["seal_images"],
            "suspicious_images": image_analysis["suspicious_images"],
        }

        pages.append(page_data)
        pages_text_for_llm.append({"index": page_index, "text": text})

    # 4. 调用 LLM 分析
    llm_analysis = {}
    try:
        llm_analysis = _call_llm_analyze(pages_text_for_llm)
        logger.info(f"后端日志：LLM合同分析完成: {json.dumps(llm_analysis, ensure_ascii=False)[:500]}")
    except Exception as e:
        logger.error(f"后端日志：LLM分析失败: {e}", exc_info=True)
        llm_analysis = {"parse_error": f"LLM调用失败: {sanitize_error_info(str(e))}"}

    # 5. 汇总全局骑缝章检测：任一页有骑缝章即为有
    global_has_riding_seal = any(p.get("has_riding_seal", False) for p in pages)

    # 6. 合并结果
    result_data: Dict[str, Any] = {
        "file_name": os.path.basename(file_path),
        "total_pages": len(pages),
        "has_riding_seal": global_has_riding_seal,
        "pages": pages,
        "llm_analysis": llm_analysis,
    }

    # 仅在 debug 模式下包含原始 OCR 结果（数据量较大）
    if debug:
        result_data["raw_ocr_result"] = ocr_result

    return {"success": True, "data": result_data}


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="合同PDF OCR解析")
    parser.add_argument("--file-path", required=True, help="PDF文件路径")
    parser.add_argument("--output-dir", default=None, help="JSON输出目录，默认与PDF同目录")
    parser.add_argument("--debug", action="store_true", help="输出原始OCR结果（数据量较大）")
    args = parser.parse_args()

    result = parse_contract_pdf(args.file_path, debug=args.debug)

    if not result.get("success"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.file_path))
    os.makedirs(output_dir, exist_ok=True)
    output_name = os.path.splitext(os.path.basename(args.file_path))[0] + "_parsed.json"
    output_path = os.path.join(output_dir, output_name)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps({"success": True, "output_file": output_path}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
