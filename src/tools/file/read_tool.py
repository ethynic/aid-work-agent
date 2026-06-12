"""
文件读取工具（read）

读取文本文件内容，返回 cat -n 格式（每行带行号）。支持三种读取模式：
整文件、行号范围（offset+limit）、标记定位（section_start/section_end）。
Word/Excel/PPT 文档自动走专用解析器。
"""

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools.file.word_reader import WordReader, is_word_document
from src.tools.file.excel_reader import ExcelReader, is_excel_document
from src.tools.file.ppt_reader import PPTReader, is_ppt_document

# 整文件读取上限
MAX_LINES = 2000


class ReadInput(BaseModel):
    """读取文件参数"""
    file_path: str = Field(
        ...,
        description="文件路径，绝对路径或相对于项目根目录的相对路径。"
    )
    offset: Optional[int] = Field(
        None,
        description="起始行偏移（0-based）。第 0 行 = 文件第 1 行。"
        "配合 limit 实现分页读取。",
    )
    limit: Optional[int] = Field(
        None,
        description="最多读取多少行。不指定时读到文件末尾或 2000 行上限。",
    )
    section_start: Optional[str] = Field(
        None,
        description="按标记定位：起始标记文本。"
        "工具在文件中查找包含此文本的行，从该行开始读取（含该行）。"
        "标记文本应尽量具体以避免歧义，如 '<style type=\"text/css\">' 而非 '<style'。",
    )
    section_end: Optional[str] = Field(
        None,
        description="按标记定位：结束标记文本。"
        "工具在文件中查找包含此文本的行（必须在 section_start 之后），读到该行为止（含该行）。"
        "不指定时从 section_start 读到文件末尾或 limit 行。",
    )


class ReadTool(BaseTool):
    """文件读取工具"""

    name = "read"
    description = """读取文本文件内容，返回 cat -n 格式（每行带行号）。三种读取模式：

1. 整文件：read(file_path="...")
   -> 读全部（上限 2000 行），适合小文件
2. 行号范围：read(file_path="...", offset=100, limit=50)
   -> offset 是 0-based（第 0 行 = 文件第 1 行），返回行号 1-based
   -> 配合 limit 分页读取
3. 标记定位：read(file_path="...", section_start="<style>", section_end="</style>")
   -> 读两个标记文本之间的行（含标记行），适合读 HTML 区域、配置段
   -> section_end 不传时从 section_start 读到文件末尾（受 limit 约束）

任何需要查看文件内容的场景都用本工具：查看用户上传的文档、读取已生成的输出文件、
查看模板内容、查看配置等。大文件（>500 行）先用 offset/limit 或 section_start/section_end
缩小范围，避免一次性读取过多内容。

Word/Excel/PPT 文档自动走专用解析器，不需要单独的工具。

注：skill 加载时，SKILL.md 中的 <SKILL_ROOT> 占位符已被替换为 skill 目录绝对路径，
因此 LLM 调用本工具时直接传 use_skill 返回的绝对路径即可，工具本身不需要识别占位符。"""
    display_name = "读取文件"
    category = "file"
    InputModel = ReadInput

    def __init__(self):
        """初始化文件读取工具"""
        self.common_encodings = [
            'utf-8',
            'gbk',
            'gb2312',
            'gb18030',
            'utf-16',
            'utf-16-le',
            'utf-16-be',
            'ascii',
            'latin-1',
            'cp1252',
        ]
        self.word_reader = WordReader()
        self.excel_reader = ExcelReader()
        self.ppt_reader = PPTReader()

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示文件名"""
        base = self.display_name
        if tool_args:
            path = tool_args.get("file_path", "")
            if path:
                filename = os.path.basename(path)
                return f"{base}「{filename}」"
        return base

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行文件读取。

        成功返回 dict（含 content / total_lines / read_lines，截断时含 next_hint）。
        失败返回字符串错误。
        """
        file_path = kwargs.get("file_path")
        offset = kwargs.get("offset")
        limit = kwargs.get("limit")
        section_start = kwargs.get("section_start")
        section_end = kwargs.get("section_end")

        # 负数 offset/limit 视为 None
        if offset is not None and offset < 0:
            offset = None
        if limit is not None and limit < 0:
            limit = None

        if not file_path:
            return "文件路径不能为空"

        try:
            path = self._resolve_path(file_path)

            # Word/Excel/PPT 路由
            if is_word_document(str(path)):
                return self._read_word_document(path)
            if is_excel_document(str(path)):
                return self._read_excel_document(path)
            if is_ppt_document(str(path)):
                return self._read_ppt_document(path)

            # 文本文件：自动检测编码读取
            content = self._read_file_content(path)
            lines = content.splitlines()
            total = len(lines)

            if section_start:
                return self._read_section(lines, section_start, section_end, limit, total)
            return self._read_range(lines, offset, limit, total)

        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            return f"读取文件失败: {e}"

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    def _resolve_path(self, file_path: str) -> Path:
        """
        解析文件路径：相对路径基于项目根目录，绝对路径直接使用。

        安全校验：解析后的路径必须在项目根目录或系统临时目录内，
        防止读取项目外的系统文件。

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 路径不合法
        """
        path = Path(file_path)

        # 相对路径：基于项目根目录解析
        if not path.is_absolute():
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            path = project_root / file_path

        path = path.resolve()

        # 安全校验：路径必须在项目根目录或临时目录内
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        tmp_dir = Path(tempfile.gettempdir()).resolve()
        try:
            path.relative_to(project_root)
        except ValueError:
            # 不在项目根目录内，检查是否在临时目录内
            try:
                path.relative_to(tmp_dir)
            except ValueError:
                raise ValueError(
                    f"文件路径超出允许范围（必须在项目根目录或临时目录内）: {file_path}"
                )

        # 检查文件是否存在
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        if not path.is_file():
            raise ValueError(f"路径不是文件: {file_path}")

        return path

    # ------------------------------------------------------------------
    # 编码检测与文件读取
    # ------------------------------------------------------------------

    def _read_file_content(self, path: Path) -> str:
        """自动检测编码并读取文件内容"""
        content, _ = self._read_with_auto_detection(path)
        return content

    def _read_with_encoding(self, path: Path, encoding: str) -> str:
        """使用指定编码读取文件"""
        with open(path, 'r', encoding=encoding, errors='replace') as f:
            return f.read()

    def _read_with_auto_detection(self, path: Path) -> tuple:
        """自动检测编码并读取文件，返回 (content, encoding)"""
        # 先读取前几KB用于检测
        with open(path, 'rb') as f:
            sample = f.read(8192)

        detected_encoding = self._detect_encoding(sample)

        if detected_encoding:
            try:
                content = self._read_with_encoding(path, detected_encoding)
                return content, detected_encoding
            except Exception:
                logger.warning(f"检测到的编码 {detected_encoding} 读取失败，尝试其他编码")

        # 遍历常用编码列表
        for enc in self.common_encodings:
            try:
                content = self._read_with_encoding(path, enc)
                logger.info(f"使用编码 {enc} 成功读取文件")
                return content, enc
            except Exception:
                continue

        # 兜底：二进制 + utf-8 替换
        logger.warning("所有编码尝试失败，使用二进制模式读取")
        with open(path, 'rb') as f:
            content = f.read().decode('utf-8', errors='replace')
        return content, 'utf-8'

    def _detect_encoding(self, sample: bytes) -> Optional[str]:
        """检测字节序列的编码"""
        # BOM 标记
        if sample.startswith(b'\xef\xbb\xbf'):
            return 'utf-8'
        elif sample.startswith(b'\xff\xfe'):
            return 'utf-16-le'
        elif sample.startswith(b'\xfe\xff'):
            return 'utf-16-be'

        # chardet 库（如果可用）
        try:
            import chardet
            result = chardet.detect(sample)
            if result and result['confidence'] > 0.7:
                encoding = result['encoding']
                if encoding:
                    return self._normalize_encoding(encoding)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"chardet 检测失败: {e}")

        # 启发式：UTF-8
        try:
            sample.decode('utf-8')
            return 'utf-8'
        except UnicodeDecodeError:
            pass

        # 启发式：GBK
        try:
            decoded = sample.decode('gbk')
            if any('一' <= char <= '鿿' for char in decoded):
                return 'gbk'
        except UnicodeDecodeError:
            pass

        return None

    def _normalize_encoding(self, encoding: str) -> str:
        """规范化编码名称"""
        encoding_map = {
            'GB2312': 'gb2312',
            'GB18030': 'gb18030',
            'ISO-8859-1': 'latin-1',
            'ASCII': 'ascii',
            'UTF-8': 'utf-8',
            'UTF-16': 'utf-16',
        }
        upper = encoding.upper()
        if upper in encoding_map:
            return encoding_map[upper]
        if upper in ['GBK', 'CP936', 'MS936']:
            return 'gbk'
        return encoding.lower()

    # ------------------------------------------------------------------
    # 行号范围读取
    # ------------------------------------------------------------------

    def _read_range(
        self,
        lines: list[str],
        offset: Optional[int],
        limit: Optional[int],
        total: int,
    ) -> Dict[str, Any]:
        """
        按 offset（0-based）+ limit 读取行范围。

        返回 cat -n 格式，行号 1-based。
        """
        start_0 = offset if offset is not None else 0
        if start_0 >= total:
            return {
                "content": "",
                "total_lines": total,
                "read_lines": 0,
            }

        # 计算读取范围
        end_0: int
        if limit is not None:
            end_0 = min(start_0 + limit, total)
        else:
            end_0 = min(start_0 + MAX_LINES, total)

        selected = lines[start_0:end_0]
        content = self._format_with_line_numbers(selected, start_0 + 1)
        read_count = len(selected)

        result: Dict[str, Any] = {
            "content": content,
            "total_lines": total,
            "read_lines": read_count,
        }

        # 截断时附 next_hint
        if end_0 < total:
            result["next_hint"] = (
                f"文件共 {total} 行，已读 {start_0 + 1}-{end_0} 行。"
                f"继续: offset={end_0}"
            )

        return result

    # ------------------------------------------------------------------
    # 标记定位读取
    # ------------------------------------------------------------------

    def _read_section(
        self,
        lines: list[str],
        section_start: str,
        section_end: Optional[str],
        limit: Optional[int],
        total: int,
    ) -> Dict[str, Any]:
        """
        按 section_start / section_end 标记定位读取。

        标记行包含在内。section_end 不传时读到末尾或 limit 行。
        """
        # 查找起始标记行（0-based index）
        start_idx = None
        for i, line in enumerate(lines):
            if section_start in line:
                start_idx = i
                break

        if start_idx is None:
            return f"未找到起始标记: {section_start}"

        # 查找结束标记行
        end_idx: Optional[int] = None
        if section_end:
            for i in range(start_idx + 1, total):
                if section_end in lines[i]:
                    end_idx = i
                    break
            if end_idx is None:
                return f"未找到结束标记: {section_end}"

        # 确定实际读取范围
        if end_idx is not None:
            raw_end = end_idx + 1  # 含结束标记行
        else:
            raw_end = total

        # 应用 limit
        if limit is not None:
            raw_end = min(raw_end, start_idx + limit)

        # 整文件上限
        raw_end = min(raw_end, start_idx + MAX_LINES)

        selected = lines[start_idx:raw_end]
        content = self._format_with_line_numbers(selected, start_idx + 1)
        read_count = len(selected)

        result: Dict[str, Any] = {
            "content": content,
            "total_lines": total,
            "read_lines": read_count,
            "section_start_line": start_idx + 1,
        }

        if end_idx is not None:
            result["section_end_line"] = end_idx + 1

        # 截断时附 next_hint
        actual_end = start_idx + read_count
        if actual_end < total and (end_idx is None or limit is not None):
            result["next_hint"] = (
                f"文件共 {total} 行，已读 {start_idx + 1}-{actual_end} 行。"
                f"继续: offset={actual_end}"
            )

        return result

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------

    @staticmethod
    def _format_with_line_numbers(lines: list[str], start_1based: int) -> str:
        """
        将行列表格式化为 cat -n 风格（行号右对齐，tab 分隔）。

        Args:
            lines: 文本行列表（不含换行符）
            start_1based: 起始行号（1-based）

        Returns:
            格式化后的字符串
        """
        if not lines:
            return ""

        # 计算最大行号的宽度，用于右对齐
        last_line_num = start_1based + len(lines) - 1
        width = len(str(last_line_num))

        parts: list[str] = []
        for i, line in enumerate(lines):
            line_num = start_1based + i
            parts.append(f"{line_num:>{width}}\t{line}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Word / Excel / PPT 路由
    # ------------------------------------------------------------------

    def _read_word_document(self, path: Path) -> Dict[str, Any]:
        """读取 Word 文档"""
        try:
            result = self.word_reader.read_word_document(str(path))
            if not result.get("success"):
                return result.get("error", "读取 Word 文档失败")

            content = result.get("content", "")
            lines = content.splitlines()
            total = len(lines)
            read_lines = min(total, MAX_LINES)
            selected = lines[:read_lines]

            formatted = self._format_with_line_numbers(selected, 1)

            ret: Dict[str, Any] = {
                "content": formatted,
                "total_lines": total,
                "read_lines": read_lines,
            }
            if read_lines < total:
                ret["next_hint"] = (
                    f"文件共 {total} 行，已读 1-{read_lines} 行。"
                    f"继续: offset={read_lines}"
                )
            return ret

        except Exception as e:
            logger.error(f"读取 Word 文档失败: {e}")
            return f"读取 Word 文档失败: {e}"

    def _read_excel_document(self, path: Path) -> Dict[str, Any]:
        """读取 Excel 文档"""
        try:
            result = self.excel_reader.read_excel_document(str(path))
            if not result.get("success"):
                return result.get("error", "读取 Excel 文档失败")

            content = result.get("content", "")
            lines = content.splitlines()
            total = len(lines)
            read_lines = min(total, MAX_LINES)
            selected = lines[:read_lines]

            formatted = self._format_with_line_numbers(selected, 1)

            ret: Dict[str, Any] = {
                "content": formatted,
                "total_lines": total,
                "read_lines": read_lines,
            }
            if read_lines < total:
                ret["next_hint"] = (
                    f"文件共 {total} 行，已读 1-{read_lines} 行。"
                    f"继续: offset={read_lines}"
                )
            return ret

        except Exception as e:
            logger.error(f"读取 Excel 文档失败: {e}")
            return f"读取 Excel 文档失败: {e}"

    def _read_ppt_document(self, path: Path) -> Dict[str, Any]:
        """读取 PPT 文档"""
        try:
            result = self.ppt_reader.read_ppt_document(str(path))
            if not result.get("success"):
                return result.get("error", "读取 PPT 文档失败")

            content = result.get("content", "")
            lines = content.splitlines()
            total = len(lines)
            read_lines = min(total, MAX_LINES)
            selected = lines[:read_lines]

            formatted = self._format_with_line_numbers(selected, 1)

            ret: Dict[str, Any] = {
                "content": formatted,
                "total_lines": total,
                "read_lines": read_lines,
            }
            if read_lines < total:
                ret["next_hint"] = (
                    f"文件共 {total} 行，已读 1-{read_lines} 行。"
                    f"继续: offset={read_lines}"
                )
            return ret

        except Exception as e:
            logger.error(f"读取 PPT 文档失败: {e}")
            return f"读取 PPT 文档失败: {e}"
