"""
文件读取工具

实现通用的文本文件读取功能，支持多种编码自动检测
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.base import BaseTool
from src.tools.file.word_reader import WordReader, is_word_document
from src.tools.file.excel_reader import ExcelReader, is_excel_document


class FileReaderTool(BaseTool):
    """文件读取工具"""

    name = "file_read"
    description = """读取各种文件的内容，支持以下格式：
1. 文本文件：自动检测文件编码（UTF-8、GBK、GB2312等），适用于.txt、.py、.md、.json、.csv等文本文件
2. Word文档：读取.docx文件，提取文本段落、表格内容和文档元信息（标题、作者等）
3. Excel文档：读取.xlsx文件，提取工作表数据、单元格内容和文档元信息
4. 支持行号范围读取，可指定start_line和end_line参数读取指定行
5. 默认最大文件大小限制为10MB，可通过max_size参数调整"""
    category = "file"
    parameters_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要读取的文件路径，可以是绝对路径或相对路径。支持.txt、.py、.md、.json、.csv、.docx、.xlsx等多种格式"
            },
            "encoding": {
                "type": "string",
                "description": "文件编码（可选，仅对文本文件有效），如果不指定则自动检测。常用编码：utf-8, gbk, gb2312, ascii等。对于.docx和.xlsx文件此参数无效"
            },
            "start_line": {
                "type": "integer",
                "description": "起始行号（可选），从第几行开始读取，默认为1"
            },
            "end_line": {
                "type": "integer",
                "description": "结束行号（可选），读到第几行，默认读取到文件末尾"
            },
            "max_size": {
                "type": "integer",
                "description": "最大读取字节数（可选），默认为10MB，防止读取超大文件"
            }
        },
        "required": ["file_path"],
        "examples": [
            {
                "file_path": "document.docx"
            },
            {
                "file_path": "data.xlsx"
            },
            {
                "file_path": "config.json"
            },
            {
                "file_path": "data.txt",
                "start_line": 1,
                "end_line": 100
            }
        ]
    }

    def __init__(self):
        """初始化文件读取工具"""
        # 常用编码列表，按优先级排序
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
        # Word文档读取器
        self.word_reader = WordReader()
        # Excel文档读取器
        self.excel_reader = ExcelReader()

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行文件读取

        Args:
            file_path: 文件路径
            encoding: 文件编码（可选）
            start_line: 起始行号（可选）
            end_line: 结束行号（可选）
            max_size: 最大读取字节数（可选）

        Returns:
            执行结果
        """
        file_path = kwargs.get("file_path", "")
        encoding = kwargs.get("encoding", "")
        start_line = kwargs.get("start_line", 1)
        end_line = kwargs.get("end_line", None)
        max_size = kwargs.get("max_size", 10 * 1024 * 1024)  # 默认10MB

        if not file_path:
            return {
                "success": False,
                "error": "文件路径不能为空"
            }

        try:
            # 解析文件路径
            path = Path(file_path)
            
            # 如果是相对路径，尝试从当前工作目录查找
            if not path.is_absolute():
                # 尝试当前工作目录
                if not path.exists():
                    # 尝试项目根目录
                    project_root = Path(__file__).parent.parent.parent.parent
                    path = project_root / file_path
            
            # 检查文件是否存在
            if not path.exists():
                return {
                    "success": False,
                    "error": f"文件不存在: {file_path}"
                }

            # 检查是否为文件
            if not path.is_file():
                return {
                    "success": False,
                    "error": f"路径不是文件: {file_path}"
                }

            # 检查文件大小
            file_size = path.stat().st_size
            if file_size > max_size:
                return {
                    "success": False,
                    "error": f"文件过大 ({file_size} 字节)，超过最大限制 {max_size} 字节"
                }

            # 检查是否为Word文档
            if is_word_document(str(path)):
                return self._read_word_document(path, start_line, end_line)

            # 检查是否为Excel文档
            if is_excel_document(str(path)):
                return self._read_excel_document(path, start_line, end_line)

            # 读取文件内容
            if encoding:
                # 使用指定编码读取
                content = self._read_with_encoding(path, encoding)
            else:
                # 自动检测编码并读取
                content, detected_encoding = self._read_with_auto_detection(path)
                encoding = detected_encoding

            # 按行分割内容
            lines = content.splitlines(keepends=True)
            total_lines = len(lines)

            # 应用行号范围
            if start_line < 1:
                start_line = 1
            if end_line is None:
                end_line = total_lines
            elif end_line > total_lines:
                end_line = total_lines

            # 提取指定范围的行
            selected_lines = lines[start_line - 1:end_line]
            content = ''.join(selected_lines)

            logger.info(f"成功读取文件: {path} (编码: {encoding}, 行数: {len(selected_lines)}/{total_lines})")

            return {
                "success": True,
                "message": f"成功读取文件内容",
                "file_path": str(path),
                "encoding": encoding,
                "total_lines": total_lines,
                "read_lines": len(selected_lines),
                "start_line": start_line,
                "end_line": end_line,
                "file_size": file_size,
                "content": content
            }

        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            return {
                "success": False,
                "error": f"读取文件失败: {str(e)}"
            }

    def _read_with_encoding(self, path: Path, encoding: str) -> str:
        """
        使用指定编码读取文件

        Args:
            path: 文件路径
            encoding: 编码名称

        Returns:
            文件内容
        """
        try:
            with open(path, 'r', encoding=encoding, errors='replace') as f:
                return f.read()
        except Exception as e:
            logger.warning(f"使用编码 {encoding} 读取失败: {e}")
            raise

    def _read_with_auto_detection(self, path: Path) -> tuple[str, str]:
        """
        自动检测编码并读取文件

        Args:
            path: 文件路径

        Returns:
            (文件内容, 检测到的编码)
        """
        # 先尝试读取文件的前几KB内容用于检测
        with open(path, 'rb') as f:
            sample = f.read(8192)

        # 尝试检测编码
        detected_encoding = self._detect_encoding(sample)
        
        # 如果检测到编码，先尝试使用检测到的编码
        if detected_encoding:
            try:
                content = self._read_with_encoding(path, detected_encoding)
                return content, detected_encoding
            except Exception:
                logger.warning(f"检测到的编码 {detected_encoding} 读取失败，尝试其他编码")

        # 遍历常用编码列表尝试读取
        for enc in self.common_encodings:
            try:
                content = self._read_with_encoding(path, enc)
                logger.info(f"使用编码 {enc} 成功读取文件")
                return content, enc
            except Exception:
                continue

        # 如果所有编码都失败，使用二进制模式读取并解码
        logger.warning("所有编码尝试失败，使用二进制模式读取")
        with open(path, 'rb') as f:
            content = f.read().decode('utf-8', errors='replace')
        return content, 'utf-8'

    def _detect_encoding(self, sample: bytes) -> Optional[str]:
        """
        检测字节序列的编码

        Args:
            sample: 文件样本字节

        Returns:
            检测到的编码名称，如果无法检测则返回None
        """
        # 检查BOM标记
        if sample.startswith(b'\xef\xbb\xbf'):
            return 'utf-8'
        elif sample.startswith(b'\xff\xfe'):
            return 'utf-16-le'
        elif sample.startswith(b'\xfe\xff'):
            return 'utf-16-be'

        # 尝试使用chardet库（如果可用）
        try:
            import chardet
            result = chardet.detect(sample)
            if result and result['confidence'] > 0.7:
                encoding = result['encoding']
                # chardet可能返回别名，需要规范化
                if encoding:
                    return self._normalize_encoding(encoding)
        except ImportError:
            logger.debug("chardet库未安装，使用简单检测")
        except Exception as e:
            logger.warning(f"chardet检测失败: {e}")

        # 简单的编码检测启发式方法
        # 检查是否为UTF-8
        try:
            sample.decode('utf-8')
            return 'utf-8'
        except UnicodeDecodeError:
            pass

        # 检查是否为GBK/GB2312（中文常见编码）
        try:
            sample.decode('gbk')
            # 检查是否包含常见中文字符
            decoded = sample.decode('gbk')
            # 简单判断：如果包含中文标点符号，很可能是GBK
            if any('\u4e00' <= char <= '\u9fff' for char in decoded):
                return 'gbk'
        except UnicodeDecodeError:
            pass

        return None

    def _normalize_encoding(self, encoding: str) -> str:
        """
        规范化编码名称

        Args:
            encoding: 原始编码名称

        Returns:
            规范化的编码名称
        """
        # 常见的编码别名映射
        encoding_map = {
            'GB2312': 'gb2312',
            'GB18030': 'gb18030',
            'ISO-8859-1': 'latin-1',
            'ASCII': 'ascii',
            'UTF-8': 'utf-8',
            'UTF-16': 'utf-16',
        }
        
        encoding_upper = encoding.upper()
        if encoding_upper in encoding_map:
            return encoding_map[encoding_upper]
        
        # 处理 GBK 的各种别名
        if encoding_upper in ['GBK', 'CP936', 'MS936']:
            return 'gbk'
        
        return encoding.lower()

    def _read_word_document(self, path: Path, start_line: int = 1, end_line: Optional[int] = None) -> Dict[str, Any]:
        """
        读取Word文档

        Args:
            path: Word文档路径
            start_line: 起始行号（可选）
            end_line: 结束行号（可选）

        Returns:
            执行结果
        """
        try:
            # 使用WordReader读取文档
            result = self.word_reader.read_word_document(str(path))

            if not result.get("success"):
                return result

            # 提取内容并应用行号范围
            full_content = result["content"]
            lines = full_content.splitlines()
            total_lines = len(lines)

            # 应用行号范围
            if start_line < 1:
                start_line = 1
            if end_line is None:
                end_line = total_lines
            elif end_line > total_lines:
                end_line = total_lines

            # 提取指定范围的行
            selected_lines = lines[start_line - 1:end_line]
            content = '\n'.join(selected_lines)

            logger.info(f"成功读取Word文档: {path} (行数: {len(selected_lines)}/{total_lines})")

            return {
                "success": True,
                "message": f"成功读取Word文档内容",
                "file_path": str(path),
                "file_type": "docx",
                "encoding": "utf-8",
                "total_lines": total_lines,
                "read_lines": len(selected_lines),
                "start_line": start_line,
                "end_line": end_line,
                "file_size": path.stat().st_size,
                "content": content,
                "paragraphs": result.get("paragraphs", []),
                "paragraph_count": result.get("paragraph_count", 0),
                "tables": result.get("tables", []),
                "table_count": result.get("table_count", 0),
                "document_info": result.get("document_info", {})
            }

        except Exception as e:
            logger.error(f"读取Word文档失败: {e}")
            return {
                "success": False,
                "error": f"读取Word文档失败: {str(e)}"
            }

    def _read_excel_document(self, path: Path, start_line: int = 1, end_line: Optional[int] = None) -> Dict[str, Any]:
        """
        读取Excel文档

        Args:
            path: Excel文档路径
            start_line: 起始行号（可选）
            end_line: 结束行号（可选）

        Returns:
            执行结果
        """
        try:
            # 使用ExcelReader读取文档
            result = self.excel_reader.read_excel_document(str(path))

            if not result.get("success"):
                return result

            # 提取内容并应用行号范围
            full_content = result["content"]
            lines = full_content.splitlines()
            total_lines = len(lines)

            # 应用行号范围
            if start_line < 1:
                start_line = 1
            if end_line is None:
                end_line = total_lines
            elif end_line > total_lines:
                end_line = total_lines

            # 提取指定范围的行
            selected_lines = lines[start_line - 1:end_line]
            content = '\n'.join(selected_lines)

            logger.info(f"成功读取Excel文档: {path} (行数: {len(selected_lines)}/{total_lines})")

            return {
                "success": True,
                "message": f"成功读取Excel文档内容",
                "file_path": str(path),
                "file_type": "xlsx",
                "encoding": "utf-8",
                "total_lines": total_lines,
                "read_lines": len(selected_lines),
                "start_line": start_line,
                "end_line": end_line,
                "file_size": path.stat().st_size,
                "content": content,
                "sheet_count": result.get("sheet_count", 0),
                "sheet_names": result.get("sheet_names", []),
                "current_sheet": result.get("current_sheet", ""),
                "sheet_data": result.get("sheet_data", {}),
                "document_info": result.get("document_info", {})
            }

        except Exception as e:
            logger.error(f"读取Excel文档失败: {e}")
            return {
                "success": False,
                "error": f"读取Excel文档失败: {str(e)}"
            }


class FileListTool(BaseTool):
    """文件列表工具"""

    name = "file_list"
    description = "列出指定目录下的文件和子目录，支持过滤和递归遍历"
    category = "file"
    parameters_schema = {
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "要列出的目录路径，默认为当前目录"
            },
            "pattern": {
                "type": "string",
                "description": "文件名匹配模式（可选），支持通配符，如 *.py, *.txt 等"
            },
            "recursive": {
                "type": "boolean",
                "description": "是否递归遍历子目录，默认False"
            },
            "show_hidden": {
                "type": "boolean",
                "description": "是否显示隐藏文件（以.开头的文件），默认False"
            }
        },
        "required": []
    }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行文件列表操作

        Args:
            directory: 目录路径
            pattern: 文件名匹配模式
            recursive: 是否递归
            show_hidden: 是否显示隐藏文件

        Returns:
            执行结果
        """
        directory = kwargs.get("directory", ".")
        pattern = kwargs.get("pattern", "*")
        recursive = kwargs.get("recursive", False)
        show_hidden = kwargs.get("show_hidden", False)

        try:
            # 解析目录路径
            dir_path = Path(directory)
            
            # 如果是相对路径，尝试从当前工作目录查找
            if not dir_path.is_absolute():
                if not dir_path.exists():
                    # 尝试项目根目录
                    project_root = Path(__file__).parent.parent.parent.parent
                    dir_path = project_root / directory

            # 检查目录是否存在
            if not dir_path.exists():
                return {
                    "success": False,
                    "error": f"目录不存在: {directory}"
                }

            if not dir_path.is_dir():
                return {
                    "success": False,
                    "error": f"路径不是目录: {directory}"
                }

            # 收集文件和目录信息
            files = []
            dirs = []

            if recursive:
                items = dir_path.rglob(pattern)
            else:
                items = dir_path.glob(pattern)

            for item in items:
                # 过滤隐藏文件
                if not show_hidden and item.name.startswith('.'):
                    continue

                # 获取文件信息
                try:
                    if item.is_file():
                        stat = item.stat()
                        files.append({
                            "name": item.name,
                            "path": str(item),
                            "size": stat.st_size,
                            "modified": stat.st_mtime
                        })
                    elif item.is_dir():
                        dirs.append({
                            "name": item.name,
                            "path": str(item)
                        })
                except Exception as e:
                    logger.warning(f"无法获取文件信息 {item}: {e}")

            logger.info(f"成功列出目录: {dir_path} (文件: {len(files)}, 目录: {len(dirs)})")

            return {
                "success": True,
                "message": f"成功列出目录内容",
                "directory": str(dir_path),
                "files": files,
                "directories": dirs,
                "file_count": len(files),
                "dir_count": len(dirs)
            }

        except Exception as e:
            logger.error(f"列出目录失败: {e}")
            return {
                "success": False,
                "error": f"列出目录失败: {str(e)}"
            }


def create_file_tools() -> List[BaseTool]:
    """
    创建文件工具实例

    Returns:
        文件工具列表
    """
    return [
        FileReaderTool(),
        FileListTool(),
    ]
