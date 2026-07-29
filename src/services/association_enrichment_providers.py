"""批量协会 CLI 使用的项目基础设施适配器。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

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

    _DIRECTORY_DOMAIN_SUFFIXES = (
        "baidu.com",
        "npoall.com",
        "huixx.cn",
        "ttbz.org.cn",
        "emagecompany.com",
    )

    def __init__(self, *, repository_root: str | Path):
        self._root = Path(repository_root)
        self._search = WebSearchTool()

    @staticmethod
    def _candidate_urls(candidates: list[dict]) -> dict[str, str]:
        """Return original candidate URLs keyed by a normalized URL identity."""
        urls: dict[str, str] = {}
        for candidate in candidates:
            url = candidate.get("url")
            if not isinstance(url, str):
                continue
            original = url.strip()
            parsed = urlparse(original)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            scheme = parsed.scheme.casefold()
            hostname = parsed.hostname.casefold()
            port = parsed.port
            if port == (443 if scheme == "https" else 80):
                port = None
            netloc = hostname if port is None else f"{hostname}:{port}"
            path = parsed.path.rstrip("/") or "/"
            identity = urlunparse(
                (scheme, netloc, path, parsed.params, parsed.query, "")
            )
            urls.setdefault(identity, original)
        return urls

    @classmethod
    def _deterministic_official_candidate(
        cls,
        association_name: str,
        candidates: list[dict],
    ) -> str | None:
        """Use only an exact-title root candidate outside known directory platforms."""
        expected_title = re.sub(r"\s+", "", association_name).casefold()
        matches: list[str] = []
        for candidate in candidates:
            title = candidate.get("title")
            url = candidate.get("url")
            if not isinstance(title, str) or not isinstance(url, str):
                continue
            if re.sub(r"\s+", "", title).casefold() != expected_title:
                continue
            parsed = urlparse(url.strip())
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            hostname = parsed.hostname.casefold()
            if any(
                hostname == suffix or hostname.endswith(f".{suffix}")
                for suffix in cls._DIRECTORY_DOMAIN_SUFFIXES
            ):
                continue
            if parsed.path.rstrip("/") or parsed.params or parsed.query:
                continue
            matches.append(url.strip())
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    async def _strict_json_chat(messages: list[dict], *, max_tokens: int) -> dict:
        """Retry once when a provider returns non-JSON or a non-object."""
        retry_messages = list(messages)
        for attempt in range(2):
            response = await llm_gateway.chat(
                messages=retry_messages,
                temperature=0,
                max_tokens=max_tokens,
            )
            try:
                content = response["content"]
                if not isinstance(content, str):
                    raise TypeError("LLM_CONTENT_NOT_STRING")
                parsed = _parse_json_object(content)
                if not isinstance(parsed, dict):
                    raise TypeError("LLM_JSON_NOT_OBJECT")
                return parsed
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                if attempt:
                    raise
                retry_messages = messages + [
                    {
                        "role": "user",
                        "content": (
                            "上一次输出不是符合要求的严格 JSON 对象。"
                            "请重新输出且只输出 JSON 对象，不要 Markdown 或解释。"
                        ),
                    }
                ]
        raise RuntimeError("LLM_JSON_RETRY_EXHAUSTED")

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
        candidates: list[dict] = []
        seen_urls: set[str] = set()
        for keyword, search_depth, max_results in (
            (association_name, "basic", 20),
            (association_name, "advanced", 8),
            (f"{association_name} 官网", "basic", 8),
            (f"{association_name} 官方网站", "basic", 8),
        ):
            try:
                result = await self._search.execute(
                    keyword=keyword,
                    max_results=max_results,
                    include_answer=False,
                    search_depth=search_depth,
                )
            except Exception:
                continue
            results = result.get("results", []) if result.get("success") else []
            if not isinstance(results, list):
                continue
            for candidate in results:
                if not isinstance(candidate, dict):
                    continue
                url = candidate.get("url")
                if not isinstance(url, str) or url in seen_urls:
                    continue
                seen_urls.add(url)
                candidates.append(candidate)
        if not candidates:
            return None
        resolver_messages = [
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
            ]
        try:
            parsed = await self._strict_json_chat(
                messages=resolver_messages,
                max_tokens=500,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            deterministic = self._deterministic_official_candidate(
                association_name, candidates
            )
            if deterministic is not None:
                return deterministic
            raise
        official_url = parsed.get("official_url")
        if official_url is None:
            indexed_candidates = [
                {
                    "index": index,
                    "title": candidate.get("title"),
                    "url": candidate.get("url"),
                    "content": candidate.get("content"),
                }
                for index, candidate in enumerate(candidates)
            ]
            try:
                indexed = await self._strict_json_chat(
                    messages=[
                    {
                        "role": "system",
                        "content": (
                            "从候选中选择目标协会自己的独立官方网站。排除百科、协会名录、"
                            "黄页、新闻媒体、其他协会和第三方聚合平台。只能返回候选编号，"
                            '严格输出 {"candidate_index":整数或null}。如果存在标题与目标'
                            "协会一致的独立官网，不要因为同时存在目录站而返回null。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "association_name": association_name,
                                "candidates": indexed_candidates,
                            },
                            ensure_ascii=False,
                        ),
                    },
                    ],
                    max_tokens=300,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                deterministic = self._deterministic_official_candidate(
                    association_name, candidates
                )
                if deterministic is not None:
                    return deterministic
                raise
            candidate_index = indexed.get("candidate_index")
            if candidate_index is None:
                return self._deterministic_official_candidate(
                    association_name, candidates
                )
            if (
                isinstance(candidate_index, bool)
                or not isinstance(candidate_index, int)
                or candidate_index < 0
                or candidate_index >= len(candidates)
            ):
                raise ValueError("OFFICIAL_RESOLVER_INVALID_RESPONSE")
            selected = candidates[candidate_index].get("url")
            if not isinstance(selected, str):
                raise ValueError("OFFICIAL_RESOLVER_INVALID_RESPONSE")
            allowed = self._candidate_urls([{"url": selected}])
            if len(allowed) != 1:
                raise ValueError("OFFICIAL_RESOLVER_INVALID_RESPONSE")
            return selected
        if not isinstance(official_url, str):
            raise ValueError("OFFICIAL_RESOLVER_INVALID_RESPONSE")
        normalized_selection = self._candidate_urls([{"url": official_url}])
        if len(normalized_selection) != 1:
            raise ValueError("OFFICIAL_RESOLVER_INVALID_RESPONSE")
        candidate_urls = self._candidate_urls(candidates)
        selected_identity = next(iter(normalized_selection))
        original_candidate = candidate_urls.get(selected_identity)
        if original_candidate is None:
            raise ValueError("OFFICIAL_RESOLVER_INVALID_RESPONSE")
        return original_candidate

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
            max_pages=4,
            max_navigation_attempts=12,
            navigation_timeout_ms=6_000,
        )
        result = await extract_association_profile(pages, domain)
        if result.status != "success" and result.reason_code == "INVALID_EVIDENCE":
            result = await extract_association_profile(pages, domain)
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
        parsed = await self._strict_json_chat(
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
            max_tokens=2500,
        )
        try:
            return self._validate_fallback_evidence(parsed, candidates)
        except (TypeError, ValueError):
            retry = await self._strict_json_chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "上一次结果的 JSON 结构或证据不合格。只输出严格 JSON。"
                            f"顶层键必须恰好为：{','.join(PROFILE_FIELDS)}。"
                            "每个值必须恰好包含 value、evidence_quote、source_url；"
                            "无法证明的字段三个值都写 null；手机号字段必须全部为 null。"
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
                max_tokens=2500,
            )
            return self._validate_fallback_evidence(retry, candidates)

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
            timeout_seconds=120,
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
