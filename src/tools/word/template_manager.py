"""
Word 模板管理器

从 word_lib.py 迁移模板加载/列表功能，新增变量占位符替换能力。
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from docx import Document

from src.tools.word.word_lib import replace_text_cross_run


TEMPLATES_DIR = Path(__file__).parent / "assets" / "templates"


def get_template(template_name: str) -> Dict[str, Any]:
    """加载命名模板。"""
    template_path = TEMPLATES_DIR / f"{template_name}.json"
    if not template_path.exists():
        available = [p.stem for p in sorted(TEMPLATES_DIR.glob("*.json"))] if TEMPLATES_DIR.exists() else []
        raise ValueError(f"未知模板 '{template_name}'。可用模板: {available}")
    return json.loads(template_path.read_text(encoding="utf-8"))


def list_templates() -> List[Dict[str, str]]:
    """列出所有可用模板的名称和描述。"""
    if not TEMPLATES_DIR.exists():
        return []
    result = []
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append({
                "name": data.get("name", p.stem),
                "display_name": data.get("display_name", p.stem),
                "description": data.get("description", ""),
            })
        except (json.JSONDecodeError, KeyError):
            result.append({"name": p.stem, "display_name": p.stem, "description": ""})
    return result


def fill_template(doc: Document, variables: Dict[str, str]) -> int:
    """
    替换文档中所有匹配的变量占位符。

    搜索范围：正文段落、表格单元格、页眉、页脚
    支持格式：{{变量名}} 和 [变量名]

    Returns:
        替换的变量数量
    """
    count = 0
    for var_name, var_value in variables.items():
        for placeholder in [f"{{{{{var_name}}}}}", f"[{var_name}]"]:
            count += _replace_in_doc(doc, placeholder, var_value)
    return count


def _replace_in_doc(doc: Document, target: str, replacement: str) -> int:
    """在文档全文（正文、表格、页眉页脚）中替换文本"""
    count = 0

    # 正文段落
    for para in doc.paragraphs:
        if target in para.text:
            count += replace_text_cross_run(para.runs, target, replacement)

    # 表格
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    if target in para.text:
                        count += replace_text_cross_run(para.runs, target, replacement)

    # 页眉页脚
    for section in doc.sections:
        for hf in [section.header, section.footer]:
            for para in hf.paragraphs:
                if target in para.text:
                    count += replace_text_cross_run(para.runs, target, replacement)

    return count
