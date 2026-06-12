"""
文件局部编辑工具

对已存在文件做局部编辑，支持三种模式：
- replace_string：精确字符串替换（强制唯一匹配）
- replace_section：标记之间内容替换（标记行保留）
- replace_lines：按行号范围替换

关键不变量：先校验再写，失败不影响原文件。
"""

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class EditError(Exception):
    """edit 工具内部错误，用于信号传递，不向上抛"""
    pass


class EditInput(BaseModel):
    """文件编辑参数"""

    file_path: str = Field(
        ...,
        description="目标文件路径（必须已存在），相对项目根目录或绝对路径。",
    )
    mode: str = Field(
        "replace_string",
        description="编辑模式：replace_string（精确字符串替换，默认）/ "
        "replace_section（标记之间内容替换）/ replace_lines（按行号替换）。",
    )

    # === replace_string 专用 ===
    old_string: Optional[str] = Field(
        None,
        description="[replace_string 专用] 文件中要被替换的精确字符串。"
        "必须在文件中唯一出现，否则报错（防止误改多处）。"
        "建议包含足够上下文（如整行或多行）以确保唯一。",
    )
    new_string: Optional[str] = Field(
        None,
        description="[replace_string 专用] 替换为的新内容。",
    )

    # === replace_section 专用 ===
    section_start: Optional[str] = Field(
        None,
        description="[replace_section 专用] 起始标记文本。"
        "工具在文件中查找包含此文本的行，从该行下一行开始替换，标记行本身保留。",
    )
    section_end: Optional[str] = Field(
        None,
        description="[replace_section 专用] 结束标记文本。"
        "工具在文件中查找包含此文本的行（在 section_start 之后），替换到该行前一行，标记行本身保留。",
    )

    # === replace_lines 专用 ===
    offset: Optional[int] = Field(
        None,
        description="[replace_lines 专用] 起始行偏移（0-based），第 0 行 = 文件第 1 行。",
    )
    limit: Optional[int] = Field(
        None,
        description="[replace_lines 专用] 要替换的行数。",
    )

    # === 共用 ===
    content: Optional[str] = Field(
        None,
        description="新内容。replace_section 和 replace_lines 模式必填。"
        "replace_string 模式不使用此参数（用 new_string）。",
    )


class EditTool(BaseTool):
    """文件局部编辑工具"""

    name = "edit"
    description = """对已存在文件做局部编辑。支持三种模式：

1. replace_string（精确字符串替换，相当于 sed 's/old/new/'）：
   edit(file_path="...", mode="replace_string", old_string="<title>旧标题</title>", new_string="<title>新标题</title>")
   → old_string 必须在文件中唯一（多个匹配会报错），防止误改
   → skill 要求改 title / 改占位符时用此模式

2. replace_section（标记之间内容替换）：
   edit(file_path="...", mode="replace_section",
        section_start="<!-- SLIDES_HERE -->", section_end="<!-- END_SLIDES -->",
        content="<section>实际页面...</section>")
   → 标记行保留，只替换两标记之间的内容
   → skill 要求填充占位区域、改 :root 主题色块时用此模式

3. replace_lines（按行号范围替换）：
   edit(file_path="...", mode="replace_lines", offset=120, limit=10, content="新内容")
   → 替换从 offset+1 行开始的 limit 行

目标文件必须已存在；先校验再写，失败不影响原文件。
任何需要修改已存在文件内容的场景都用本工具：修改配置项、替换占位符、
填充模板区域、改 HTML 元素内容等。"""
    display_name = "编辑文件"
    category = "file"
    InputModel = EditInput

    def _resolve_path(self, file_path: str) -> Path:
        """解析文件路径，限制在项目根目录或临时目录内。

        Args:
            file_path: 文件路径，绝对路径或相对项目根目录的相对路径

        Returns:
            解析后的 Path 对象

        Raises:
            ValueError: 路径超出允许范围
        """
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        p = Path(file_path)

        if not p.is_absolute():
            p = project_root / file_path

        p = p.resolve()

        # 安全检查：路径必须在项目根目录内或系统临时目录内
        tmp_dir = Path(tempfile.gettempdir()).resolve()
        try:
            p.relative_to(project_root)
        except ValueError:
            try:
                p.relative_to(tmp_dir)
            except ValueError:
                raise ValueError(
                    f"文件路径超出允许范围: {file_path}。"
                    f"文件必须在项目根目录或临时目录内。"
                )

        return p

    def _do_replace_string(
        self, original: str, old_string: str, new_string: str
    ) -> Tuple[str, int]:
        """精确字符串替换，强制 old_string 唯一。

        Args:
            original: 原始文件内容
            old_string: 要被替换的字符串
            new_string: 替换为的新字符串

        Returns:
            (新内容, 匹配行号) 的元组

        Raises:
            EditError: old_string 未找到或出现多次
        """
        count = original.count(old_string)
        if count == 0:
            raise EditError(
                f"未在文件中找到 old_string: {old_string[:60]}..."
            )
        if count > 1:
            raise EditError(
                f"old_string 在文件中出现 {count} 次，无法唯一匹配。"
                f"请加入更多上下文使其唯一。"
            )
        new_content = original.replace(old_string, new_string, 1)
        matched_line = original[: original.index(old_string)].count("\n") + 1
        return new_content, matched_line

    def _do_replace_section(
        self,
        original: str,
        section_start: str,
        section_end: str,
        content: str,
    ) -> Tuple[str, int, int]:
        """标记之间内容替换。标记行保留，只替换中间内容。
        同行情况：section_start 和 section_end 在同一行时，
        整段含标记本身替换为 content。

        Args:
            original: 原始文件内容
            section_start: 起始标记文本
            section_end: 结束标记文本
            content: 替换为的新内容

        Returns:
            (新内容, 起始行号, 结束行号) 的元组，行号 1-based

        Raises:
            EditError: 标记未找到
        """
        lines = original.splitlines()
        start_idx = None
        end_idx = None

        for i, line in enumerate(lines):
            if section_start in line and start_idx is None:
                start_idx = i
                # 同行检查：起始和结束标记在同一行（仅当标记文本不同时）
                if section_start != section_end and section_end in line:
                    end_idx = i
                    break
            elif section_end in line and start_idx is not None:
                end_idx = i
                break

        if start_idx is None:
            raise EditError(f"未找到起始标记: {section_start}")
        if end_idx is None:
            raise EditError(
                f"未在起始标记之后找到结束标记: {section_end}"
            )

        if start_idx == end_idx:
            # 同行：用新内容替换从 start 标记到 end 标记的整段（含标记本身）
            line = lines[start_idx]
            start_pos = line.index(section_start)
            end_pos = line.index(section_end, start_pos + len(section_start))
            new_line = (
                line[:start_pos] + content + line[end_pos + len(section_end):]
            )
            new_lines = lines[:start_idx] + [new_line] + lines[start_idx + 1:]
        else:
            # 跨行：保留标记行，替换中间内容
            new_lines = lines[: start_idx + 1]  # 到 section_start 行（含）
            new_lines.append(content)            # 新内容
            new_lines.extend(lines[end_idx:])    # section_end 行（含）及之后

        return "\n".join(new_lines), start_idx + 1, end_idx + 1

    def _do_replace_lines(
        self,
        original: str,
        offset: int,
        limit: int,
        content: str,
    ) -> Tuple[str, int, int]:
        """按行号范围替换。

        Args:
            original: 原始文件内容
            offset: 起始行偏移（0-based）
            limit: 要替换的行数
            content: 替换为的新内容

        Returns:
            (新内容, 起始行号, 结束行号) 的元组，行号 1-based

        Raises:
            EditError: offset 超出文件行数
        """
        lines = original.splitlines(keepends=True)
        start = offset
        end = offset + limit

        if start >= len(lines):
            raise EditError(
                f"offset={offset} 超出文件行数 {len(lines)}"
            )
        if end > len(lines):
            end = len(lines)

        new_lines = lines[:start] + [content + "\n"] + lines[end:]
        return "".join(new_lines), start + 1, end

    async def execute(self, **kwargs) -> Union[Dict[str, Any], str]:
        """执行文件编辑。

        Args:
            file_path: 目标文件路径
            mode: 编辑模式
            其他参数按 mode 不同而异

        Returns:
            成功时返回 dict，失败时返回错误字符串
        """
        file_path = kwargs.get("file_path", "")
        mode = kwargs.get("mode", "replace_string")

        if not file_path:
            return "文件路径不能为空"

        # 1. 解析路径
        try:
            path = self._resolve_path(file_path)
        except ValueError as e:
            return str(e)

        # 2. 文件必须已存在
        if not path.exists():
            return f"目标文件不存在: {file_path}"
        if not path.is_file():
            return f"目标路径不是文件: {file_path}"

        # 3. 读取原文件
        try:
            original = path.read_text(encoding="utf-8")
        except Exception as e:
            return f"读取文件失败: {e}"

        # 4. 在内存中计算新内容（关键：失败不影响原文件）
        try:
            if mode == "replace_string":
                old_string = kwargs.get("old_string")
                new_string = kwargs.get("new_string")
                if not old_string:
                    return "replace_string 模式需要 old_string 参数"
                if new_string is None:
                    return "replace_string 模式需要 new_string 参数"
                new_content, matched_info = self._do_replace_string(
                    original, old_string, new_string
                )
                matched_lines = matched_info

            elif mode == "replace_section":
                section_start = kwargs.get("section_start")
                section_end = kwargs.get("section_end")
                content = kwargs.get("content")
                if not section_start:
                    return "replace_section 模式需要 section_start 参数"
                if not section_end:
                    return "replace_section 模式需要 section_end 参数"
                if content is None:
                    return "replace_section 模式需要 content 参数"
                new_content, start_line, end_line = self._do_replace_section(
                    original, section_start, section_end, content
                )
                matched_lines = [start_line, end_line]

            elif mode == "replace_lines":
                offset = kwargs.get("offset")
                limit = kwargs.get("limit")
                content = kwargs.get("content")
                if offset is None:
                    return "replace_lines 模式需要 offset 参数"
                if limit is None:
                    return "replace_lines 模式需要 limit 参数"
                if content is None:
                    return "replace_lines 模式需要 content 参数"
                new_content, start_line, end_line = self._do_replace_lines(
                    original, offset, limit, content
                )
                matched_lines = [start_line, end_line]

            else:
                return f"不支持的编辑模式: {mode}"

        except EditError as e:
            return str(e)

        # 5. 原子写入：临时文件 + rename
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(new_content, encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            return f"写入文件失败: {e}"

        file_size = path.stat().st_size
        logger.info(
            f"文件编辑成功: {path} ({file_size} bytes, mode={mode})"
        )

        return {
            "file_path": str(path),
            "file_size": file_size,
            "matched_lines": matched_lines,
        }
