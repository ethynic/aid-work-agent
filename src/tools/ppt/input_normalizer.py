"""Normalize structured and legacy inputs for the PPT tool."""

import json
import re
from html import unescape
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class NormalizedPptInput:
    instruction: Optional[str]
    content: Optional[str]
    content_type: str
    output_name: Optional[str]
    export_mode: Optional[str]
    context: Optional[str]
    file_paths: List[str] = field(default_factory=list)
    extracted_title: Optional[str] = None


class PptInputNormalizer:
    """Convert the public input contract into deterministic internal fields."""

    _MARKDOWN_START = re.compile(r"(?m)^#{1,6}\s+")
    _HTML_START = re.compile(r"(?is)<(?:!doctype\s+html|html|head|title|body|h1)\b")
    _LEGACY_INSTRUCTION = re.compile(
        r"(?i)(?:生成|制作|创建|转换|导出).*(?:pptx?|演示文稿|幻灯片)"
    )

    def normalize(
        self,
        *,
        instruction: Optional[str] = None,
        content: Optional[str] = None,
        content_type: Optional[str] = None,
        output_name: Optional[str] = None,
        export_mode: Optional[str] = None,
        context: Optional[str] = None,
        file_paths: Optional[List[str]] = None,
    ) -> NormalizedPptInput:
        clean_instruction = self._clean(instruction)
        clean_content = self._clean(content)
        clean_context = self._clean(context)

        if clean_content is None and clean_context:
            legacy_instruction, legacy_content = self._split_context(clean_context)
            clean_content = legacy_content
            if clean_instruction is None:
                clean_instruction = legacy_instruction

        detected_type = self._normalize_content_type(content_type, clean_content)
        clean_output_name = self._clean(output_name)
        return NormalizedPptInput(
            instruction=clean_instruction,
            content=clean_content,
            content_type=detected_type,
            output_name=clean_output_name,
            export_mode=self._clean(export_mode),
            context=clean_context,
            file_paths=[
                path.strip()
                for path in (file_paths or [])
                if isinstance(path, str) and path.strip()
            ],
            extracted_title=self.extract_title(clean_content, detected_type),
        )

    def _split_context(self, context: str) -> tuple[Optional[str], str]:
        starts = [
            match.start()
            for pattern in (self._MARKDOWN_START, self._HTML_START)
            if (match := pattern.search(context))
        ]
        json_start = self._find_json_start(context)
        if json_start is not None:
            starts.append(json_start)

        if starts:
            content_start = min(starts)
            instruction = context[:content_start].strip() or None
            return instruction, context[content_start:].strip()
        first_line, separator, remainder = context.partition("\n")
        if separator and remainder.strip() and self._LEGACY_INSTRUCTION.search(first_line):
            return first_line.strip(), remainder.strip()
        inline = re.match(
            r"(?is)^(.+?(?:pptx?|演示文稿|幻灯片))\s*[：:，,]\s*(.+)$",
            context,
        )
        if inline and self._LEGACY_INSTRUCTION.search(inline.group(1)):
            return inline.group(1).strip(), inline.group(2).strip()
        return None, context

    def _normalize_content_type(
        self, content_type: Optional[str], content: Optional[str]
    ) -> str:
        explicit = (content_type or "auto").strip().lower()
        aliases = {"json": "slide_deck_spec", "spec": "slide_deck_spec", "md": "markdown"}
        explicit = aliases.get(explicit, explicit)
        if explicit != "auto" or not content:
            return explicit
        if self._HTML_START.search(content):
            return "html"
        if self._looks_like_spec(content):
            return "slide_deck_spec"
        if self._MARKDOWN_START.search(content):
            return "markdown"
        return "text"

    def extract_title(
        self, content: Optional[str], content_type: Optional[str] = None
    ) -> Optional[str]:
        if not content:
            return None

        if content_type == "slide_deck_spec" or self._looks_like_spec(content):
            try:
                title = json.loads(content).get("title")
                if isinstance(title, str) and title.strip():
                    return title.strip()
            except (json.JSONDecodeError, AttributeError):
                pass

        if content_type == "html" or self._HTML_START.search(content):
            for tag in ("title", "h1"):
                match = re.search(
                    rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>",
                    content,
                )
                if match:
                    title = unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
                    if title:
                        return title

        markdown = re.search(r"(?m)^#{1,2}\s+(.+?)\s*$", content)
        if markdown:
            return markdown.group(1).strip().rstrip("#").strip() or None
        return None

    @staticmethod
    def output_title(output_name: Optional[str]) -> Optional[str]:
        if not output_name:
            return None
        name = re.split(r"[\\/]", output_name)[-1].strip()
        if name.lower().endswith(".pptx"):
            name = name[:-5]
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
        return name or None

    @staticmethod
    def _find_json_start(text: str) -> Optional[int]:
        for match in re.finditer(r"\{", text):
            try:
                value = json.loads(text[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and "title" in value:
                return match.start()
        return None

    @staticmethod
    def _looks_like_spec(content: str) -> bool:
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(value, dict) and "title" in value and "slides" in value

    @staticmethod
    def _clean(value: Optional[str]) -> Optional[str]:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None
