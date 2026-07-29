"""批量协会 CLI 使用的项目基础设施适配器。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from src.llm.gateway import llm_gateway
from src.services.association_profile_extractor import (
    PROFILE_FIELDS,
    _parse_json_object,
    extract_association_profile,
)
from src.services.official_site_browser_collector import (
    collect_official_pages_with_playwright,
)
from src.tools.search.search_tool import WebSearchTool


class ProjectAssociationProviders:
    """复用 WebSearchTool、llm_gateway、可见 Playwright 与微信 PowerShell。"""

    def __init__(self, *, repository_root: str | Path):
        self._root = Path(repository_root)
        self._search = WebSearchTool()

    @staticmethod
    def _validate_fallback_evidence(
        parsed: dict,
        candidates: list[dict],
    ) -> dict[str, str | None]:
        if set(parsed) != set(PROFILE_FIELDS):
            raise ValueError("WEB_FALLBACK_SCHEMA_INVALID")
        evidence_by_url: dict[str, str] = {}
        for candidate in candidates:
            url = candidate.get("url")
            if not isinstance(url, str):
                continue
            evidence_by_url[url] = "\n".join(
                str(candidate.get(key) or "")
                for key in ("title", "content", "raw_content")
            )
        result: dict[str, str | None] = {}
        for name in PROFILE_FIELDS:
            evidence = parsed[name]
            if not isinstance(evidence, dict) or set(evidence) != {
                "value", "evidence_quote", "source_url",
            }:
                raise ValueError("WEB_FALLBACK_SCHEMA_INVALID")
            value = evidence["value"]
            quote = evidence["evidence_quote"]
            source_url = evidence["source_url"]
            if value is None:
                if quote is not None or source_url is not None:
                    raise ValueError("WEB_FALLBACK_EVIDENCE_INVALID")
                result[name] = None
                continue
            if name in {"president_mobile", "secretary_general_mobile"}:
                raise ValueError("WEB_FALLBACK_MOBILE_FORBIDDEN")
            if not all(isinstance(item, str) and item.strip() for item in (
                value, quote, source_url,
            )):
                raise ValueError("WEB_FALLBACK_EVIDENCE_INVALID")
            source_text = evidence_by_url.get(source_url)
            if source_text is None or quote not in source_text:
                raise ValueError("WEB_FALLBACK_EVIDENCE_INVALID")
            compact_value = re.sub(r"\s+", "", value)
            compact_quote = re.sub(r"\s+", "", quote)
            if compact_value not in compact_quote:
                raise ValueError("WEB_FALLBACK_EVIDENCE_INVALID")
            result[name] = value.strip()
        return result

    @staticmethod
    async def _communicate_with_timeout(
        process: asyncio.subprocess.Process,
        *,
        timeout_seconds: float,
        error_code: str,
    ) -> tuple[bytes, bytes]:
        try:
            return await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(error_code) from None

    async def resolve_official_site(self, association_name: str) -> str | None:
        result = await self._search.execute(
            keyword=f"{association_name} 官方网站",
            max_results=8,
            include_answer=False,
            search_depth="advanced",
        )
        candidates = result.get("results", []) if result.get("success") else []
        if not candidates:
            return None
        response = await llm_gateway.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你只负责从搜索结果识别协会官方网站。只输出严格 JSON："
                        '{"official_url":字符串或null}。无法确认时必须为null。'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"association_name": association_name, "results": candidates},
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0,
            max_tokens=500,
        )
        parsed = _parse_json_object(response["content"])
        official_url = parsed.get("official_url")
        if official_url is None:
            return None
        if not isinstance(official_url, str) or not urlparse(official_url).hostname:
            raise ValueError("OFFICIAL_RESOLVER_INVALID_RESPONSE")
        return official_url

    async def collect_official_profile(
        self, entry_url: str, headless: bool
    ) -> dict[str, str | None]:
        domain = urlparse(entry_url).hostname
        if not domain:
            raise ValueError("OFFICIAL_SITE_URL_INVALID")
        pages = await collect_official_pages_with_playwright(
            entry_url,
            domain,
            headless=headless,
        )
        result = await extract_association_profile(pages, entry_url)
        if result.status != "success" or result.profile is None:
            raise ValueError(result.reason_code or "OFFICIAL_EXTRACTION_FAILED")
        return {
            name: getattr(result.profile, name).value
            for name in PROFILE_FIELDS
        }

    async def fallback_profile(self, association_name: str) -> dict[str, str | None]:
        result = await self._search.execute(
            keyword=f"{association_name} 协会简介 会长 秘书长 地址 邮箱 会员",
            max_results=10,
            include_answer=True,
            search_depth="advanced",
        )
        if not result.get("success"):
            raise RuntimeError("WEB_SEARCH_FAILED")
        candidates = result.get("results")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeError("WEB_SEARCH_NO_EVIDENCE")
        response = await llm_gateway.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "根据网络检索结果补充协会基础资料，只输出严格 JSON。每个字段"
                        "必须是仅含value、evidence_quote、source_url的对象；非空quote"
                        "必须逐字来自对应搜索结果的title/content/raw_content，value"
                        "必须出现在quote中；没有可靠依据时三个值均为null。"
                        f"键必须恰好为：{','.join(PROFILE_FIELDS)}。"
                        "手机字段三个值必须均为null，手机只由微信取证流程填写。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "association_name": association_name,
                            "search_result": result,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0,
            max_tokens=2500,
        )
        parsed = _parse_json_object(response["content"])
        return self._validate_fallback_evidence(parsed, candidates)

    async def wechat_mobile(
        self, association_name: str, person_name: str, role: str
    ) -> str | None:
        script = (
            self._root
            / "clients"
            / "wechat-souyisou-rpa"
            / "scripts"
            / "wechat-souyisou.ps1"
        )
        import os
        import sys

        child_environment = os.environ.copy()
        child_environment["PATH"] = (
            str(Path(sys.executable).parent)
            + os.pathsep
            + child_environment.get("PATH", "")
        )
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Command",
            "collect",
            "-AssociationName",
            association_name,
            "-PersonName",
            person_name,
            "-Execute",
            "-UseProjectLlm",
            "-Limit",
            "10",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_environment,
        )
        stdout, _stderr = await self._communicate_with_timeout(
            process,
            timeout_seconds=300,
            error_code="WECHAT_RPA_TIMEOUT",
        )
        if process.returncode != 0:
            raise RuntimeError("WECHAT_RPA_FAILED")
        payload = json.loads(stdout.decode("utf-8-sig").strip().splitlines()[-1])
        if not payload.get("ok") or not payload.get("session_closed"):
            raise RuntimeError("WECHAT_SESSION_NOT_CLOSED")
        if payload.get("status") != "found":
            return None

        helper = script.with_name("extract-mobile.ps1")
        extraction = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-ArtifactPath",
            payload["artifact_ref"],
            "-AssociationName",
            association_name,
            "-PersonName",
            person_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        private_stdout, _private_stderr = await self._communicate_with_timeout(
            extraction,
            timeout_seconds=30,
            error_code="WECHAT_ARTIFACT_READ_TIMEOUT",
        )
        if extraction.returncode != 0:
            raise RuntimeError("WECHAT_ARTIFACT_READ_FAILED")
        evidence = json.loads(private_stdout.decode("utf-8-sig").strip())
        mobile = evidence.get("mobile") if evidence.get("matched") else None
        if mobile is not None and not re.fullmatch(r"1[3-9]\d{9}", str(mobile)):
            raise RuntimeError("WECHAT_ARTIFACT_INVALID")
        return str(mobile) if mobile is not None else None
