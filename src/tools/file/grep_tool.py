"""
文本搜索工具（grep）

调 ripgrep（rg）二进制实现文件/目录内正则搜索，用于在工具落盘的大响应文件、
代码、日志中定位内容。返回匹配行 + 行号（1-based）+ 上下文，行号与 read 的
cat -n 行号一致，可直接配合 read 精读。

设计依据：docs/tools/large-content-retrieval-design.md 第 4 节。

安全要点：
- pattern / path 一律走 subprocess 参数数组，绝对不经 shell，防注入。
- 路径白名单：复用 read 的逻辑（项目根 + 临时目录），支持目录（read 的 _resolve_path
  要求 is_file，这里实现支持目录的版本）。
- rg 超时 10s 兜底，防恶意大目录搜索。
- rg 不存在时返回明确错误，不崩溃。
"""

import asyncio
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field
from typing import Literal

from src.tools.base import BaseTool

# rg 执行超时（秒），防恶意大目录搜索
RG_TIMEOUT = 10

# 默认最大返回匹配数（防巨量匹配撑爆上下文）
DEFAULT_MAX_MATCHES = 50


class GrepInput(BaseModel):
    """grep 工具参数"""

    pattern: str = Field(
        ...,
        description="正则表达式（ripgrep 语法）。用于搜索文件内容。",
    )
    path: str = Field(
        ...,
        description="搜索目标：文件路径或目录。支持绝对路径或相对于项目根的相对路径。"
        "必须在允许范围内（项目根目录或系统临时目录）。",
    )
    glob: Optional[str] = Field(
        None,
        description="文件名 glob 过滤，如 '*.py'、'*.json'。仅目录搜索时有效。",
    )
    output_mode: Literal["content", "files_with_matches", "count"] = Field(
        "content",
        description="输出模式：content（匹配行+行号，默认）/ "
        "files_with_matches（仅文件名列表）/ count（每文件匹配计数）。",
    )
    ignore_case: bool = Field(
        False,
        description="忽略大小写（等同 rg -i）。",
    )
    context: int = Field(
        0,
        description="上下文行数，同时设匹配行前后各 N 行（等同 rg -C）。默认 0。",
    )
    before_context: int = Field(
        0,
        description="匹配行前 N 行（等同 rg -B）。默认 0。",
    )
    after_context: int = Field(
        0,
        description="匹配行后 N 行（等同 rg -A）。默认 0。",
    )
    max_matches: int = Field(
        DEFAULT_MAX_MATCHES,
        description="最大返回匹配数，超过则截断并置 truncated=True。默认 50。",
    )


class GrepTool(BaseTool):
    """文本搜索工具（调 ripgrep）"""

    name = "grep"
    description = (
        "在文件或目录中搜索文本（正则匹配），返回匹配行+1-based行号+上下文。"
        "output_mode: content(默认,返回匹配行+上下文)/files_with_matches(只返回文件名)/count(匹配数)。"
        "context/before_context/after_context 取上下文行，glob 按扩展名过滤（如 *.json），"
        "max_matches 默认50。配合 read 精读：offset=行号-1。只读不写。"
    )
    usage_guide = ""
    display_name = "搜索文本"
    category = "file"
    InputModel = GrepInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行 grep 搜索。"""
        pattern: str = kwargs.get("pattern", "")
        path_str: str = kwargs.get("path", "")
        glob_filter: Optional[str] = kwargs.get("glob")
        output_mode: str = kwargs.get("output_mode", "content")
        ignore_case: bool = kwargs.get("ignore_case", False)
        context: int = kwargs.get("context", 0) or 0
        before_context: int = kwargs.get("before_context", 0) or 0
        after_context: int = kwargs.get("after_context", 0) or 0
        max_matches: int = kwargs.get("max_matches", DEFAULT_MAX_MATCHES) or 0

        if not pattern:
            return {"success": False, "error": "搜索 pattern 不能为空"}
        if not path_str:
            return {"success": False, "error": "搜索 path 不能为空"}
        if max_matches <= 0:
            max_matches = DEFAULT_MAX_MATCHES

        # 负数上下文归零
        context = max(0, context)
        before_context = max(0, before_context)
        after_context = max(0, after_context)

        # 路径白名单解析（支持文件或目录）
        try:
            target_path = self._resolve_path(path_str)
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        except ValueError as e:
            return {"success": False, "error": str(e)}

        # 构建 rg 参数数组（绝对不经 shell）
        try:
            cmd, parse_mode = self._build_command(
                pattern=pattern,
                target_path=target_path,
                glob_filter=glob_filter,
                output_mode=output_mode,
                ignore_case=ignore_case,
                context=context,
                before_context=before_context,
                after_context=after_context,
            )
        except Exception as e:
            logger.error(f"构建 rg 命令失败: {e}")
            return {"success": False, "error": f"构建搜索命令失败: {e}"}

        # 执行 rg（通过 to_thread 避免阻塞事件循环）
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=RG_TIMEOUT,
                # 不经 shell
                shell=False,
            )
        except FileNotFoundError:
            # rg 二进制不存在
            return {"success": False, "error": "ripgrep 未安装，无法执行搜索"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"搜索超时（>{RG_TIMEOUT}s），请缩小搜索范围"}
        except Exception as e:
            logger.error(f"执行 rg 失败: {e}")
            return {"success": False, "error": f"执行搜索失败: {e}"}

        # rg 退出码：0=有匹配，1=无匹配（非错误），2=错误（如非法正则）
        if proc.returncode == 2:
            err_msg = self._extract_rg_error(proc.stderr)
            return {"success": False, "error": f"搜索失败: {err_msg}"}

        stdout = proc.stdout or ""

        # 按模式解析输出
        if output_mode == "files_with_matches":
            return self._parse_files_with_matches(stdout)
        if output_mode == "count":
            return self._parse_count(stdout)
        return self._parse_content(stdout, max_matches)

    # ------------------------------------------------------------------
    # 路径解析（支持文件或目录，复用 read 白名单：项目根 + 临时目录）
    # ------------------------------------------------------------------

    def _resolve_path(self, path_str: str) -> Path:
        """解析路径并做白名单校验，支持文件和目录。

        与 read_tool._resolve_path 的白名单一致（项目根 + 系统临时目录），
        但本方法支持目录（read 要求 is_file）。

        Raises:
            FileNotFoundError: 路径不存在
            ValueError: 路径超出允许范围
        """
        path = Path(path_str)

        # 相对路径：基于项目根目录解析（与 read 一致的根定位）
        if not path.is_absolute():
            project_root = self._get_project_root()
            path = project_root / path_str

        path = path.resolve()

        # 安全校验：路径必须在项目根目录或临时目录内
        project_root = self._get_project_root()
        tmp_dir = Path(tempfile.gettempdir()).resolve()
        try:
            path.relative_to(project_root)
        except ValueError:
            try:
                path.relative_to(tmp_dir)
            except ValueError:
                raise ValueError(
                    f"路径超出允许范围（必须在项目根目录或临时目录内）: {path_str}"
                )

        # 存在性检查（文件或目录均可）
        if not path.exists():
            raise FileNotFoundError(f"路径不存在: {path_str}")

        return path

    @staticmethod
    def _get_project_root() -> Path:
        """获取项目根目录（与 read_tool 的定位方式一致）。"""
        # src/tools/file/grep_tool.py → 上溯 4 层到项目根
        return Path(__file__).resolve().parent.parent.parent.parent

    # ------------------------------------------------------------------
    # 构建 rg 命令
    # ------------------------------------------------------------------

    @staticmethod
    def _build_command(
        *,
        pattern: str,
        target_path: Path,
        glob_filter: Optional[str],
        output_mode: str,
        ignore_case: bool,
        context: int,
        before_context: int,
        after_context: int,
    ) -> tuple:
        """构建 rg 参数数组。

        Returns:
            (cmd_list, parse_mode) —— parse_mode 标记输出解析方式
            （content / files / count），供调用方区分。
        """
        cmd: List[str] = ["rg"]

        if output_mode == "files_with_matches":
            cmd.append("-l")
            return cmd + [pattern] + GrepTool._common_flags(
                target_path, glob_filter, ignore_case
            ), "files"

        if output_mode == "count":
            cmd.append("-c")
            # 计数模式也带文件名，便于解析
            cmd.append("--with-filename")
            return cmd + [pattern] + GrepTool._common_flags(
                target_path, glob_filter, ignore_case
            ), "count"

        # content 模式
        cmd.append("--line-number")
        cmd.append("--with-filename")

        # 上下文：context 同时设前后；before/after 单独设
        # 注意：context 非零时优先用 -C，否则用 -B/-A
        if context > 0:
            cmd += ["-C", str(context)]
        else:
            if before_context > 0:
                cmd += ["-B", str(before_context)]
            if after_context > 0:
                cmd += ["-A", str(after_context)]

        if ignore_case:
            cmd.append("-i")

        if glob_filter:
            cmd += ["-g", glob_filter]

        # color=never 避免输出混入 ANSI 转义码干扰解析
        cmd.append("--color=never")

        cmd.append(pattern)
        cmd.append(str(target_path))
        return cmd, "content"

    @staticmethod
    def _common_flags(
        target_path: Path,
        glob_filter: Optional[str],
        ignore_case: bool,
    ) -> List[str]:
        """files_with_matches / count 模式共用的尾部参数。"""
        flags: List[str] = []
        if ignore_case:
            flags.append("-i")
        if glob_filter:
            flags += ["-g", glob_filter]
        flags.append("--color=never")
        flags.append(str(target_path))
        return flags

    # ------------------------------------------------------------------
    # 输出解析
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_content(stdout: str, max_matches: int) -> Dict[str, Any]:
        """解析 content 模式输出（含上下文）。

        rg 格式：
        - 匹配行：``{file}:{lineno}:{content}``
        - 上下文行：``{file}-{lineno}-{content}``

        本方法只收集「匹配行」（分隔符为 : 的行），上下文行（分隔符为 - 的行）
        在独立分组中被 rg 用 ``--`` 分隔，这里不单独解析上下文（调用方可通过
        ``-C`` 让匹配行自带上下文，但 rg 的上下文输出是连续多行块，这里按
        「匹配行」粒度收集 + 截断 max_matches）。
        """
        matches: List[Dict[str, Any]] = []
        truncated = False

        lines = stdout.splitlines()
        for raw_line in lines:
            if not raw_line:
                continue
            parsed = GrepTool._parse_match_line(raw_line)
            if parsed is None:
                # 上下文行或分隔行，跳过（不单独返回，避免行数失控）
                continue
            if len(matches) >= max_matches:
                truncated = True
                break
            matches.append(parsed)

        return {
            "success": True,
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
        }

    @staticmethod
    def _parse_match_line(raw_line: str) -> Optional[Dict[str, Any]]:
        """解析单行 rg 输出为匹配项。

        匹配行格式 ``{file}:{lineno}:{content}``，上下文行格式
        ``{file}-{lineno}-{content}``。本方法只认匹配行（含两个 ``:`` 分隔）。

        解析策略：行首是文件名，后跟 ``:lineno:content``。由于文件名、内容都可能
        含 ``:``，采用「从左找第一个 ``:``，其后必须是纯数字行号，再后跟一个 ``:``」
        的正则定位，稳健且不依赖 rpartition（内容含 ``:`` 时 rpartition 会错位）。
        """
        # 匹配行：{file}:{lineno}:{content}
        # file 不含换行；lineno 是 1+ 位数字；content 任意（可为空）
        m = re.match(r"^(.*?):(\d+):(.*)$", raw_line)
        if not m:
            return None
        file_str, lineno_str, content = m.group(1), m.group(2), m.group(3)
        try:
            lineno = int(lineno_str)
        except ValueError:
            return None
        return {
            "file": file_str,
            "line": lineno,
            "content": content,
        }

    @staticmethod
    def _parse_files_with_matches(stdout: str) -> Dict[str, Any]:
        """解析 files_with_matches 模式（-l）输出：每行一个文件名。"""
        files = [line for line in stdout.splitlines() if line.strip()]
        return {
            "success": True,
            "files": files,
            "count": len(files),
        }

    @staticmethod
    def _parse_count(stdout: str) -> Dict[str, Any]:
        """解析 count 模式（-c --with-filename）输出：``{file}:{count}``。"""
        counts: List[Dict[str, Any]] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            # 格式 {file}:{count}
            idx = line.rfind(":")
            if idx <= 0:
                continue
            file_str = line[:idx]
            count_str = line[idx + 1 :]
            try:
                cnt = int(count_str)
            except ValueError:
                continue
            counts.append({"file": file_str, "count": cnt})
        return {
            "success": True,
            "counts": counts,
        }

    @staticmethod
    def _extract_rg_error(stderr: str) -> str:
        """从 rg stderr 提取可读错误信息（脱敏：截断，不暴露完整堆栈）。"""
        if not stderr:
            return "未知错误"
        # 取 stderr 第一行作为主要错误（rg 错误通常首行即核心）
        first_line = stderr.splitlines()[0] if stderr.splitlines() else stderr
        # 截断防过长
        if len(first_line) > 200:
            first_line = first_line[:200] + "..."
        return first_line
