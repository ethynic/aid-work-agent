"""批量协会 CLI 使用的项目基础设施适配器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable
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
from src.services.llm_usage_meter import record_usage
from src.services.association_batch_enrichment import WechatRpaError


_WECHAT_SESSION_FATAL_CODES = {
    "SESSION_CLEANUP_FAILED",
    "PLUGIN_IDENTITY_INVALID",
    "PLUGIN_ACTIVATION_FAILED",
    "PLUGIN_CLOSE_REJECTED",
    "PLUGIN_CLOSE_TIMEOUT",
    "MAIN_WINDOW_MISSING",
    "MAIN_WINDOW_UNTRUSTED",
    "MAIN_ACTIVATION_FAILED",
    "MAIN_FOREGROUND_NOT_RESTORED",
    "FOREGROUND_LOST",
    "SOUYISOU_WINDOW_UNTRUSTED",
    "WECHAT_RPA_TIMEOUT",
    "WECHAT_WORK_TIMEOUT",
    "WECHAT_HANDOFF_FAILED",
}
_WECHAT_INPUT_FAILURE_CODES = {
    "SEARCH_INPUT_FOCUS_FAILED",
    "SEARCH_INPUT_LOCATOR_FAILED",
    "SEARCH_INPUT_CLICK_STRUCTURE_INVALID",
    "SEARCH_INPUT_READBACK_MISMATCH",
    "SEARCH_INPUT_FINAL_STRUCTURE_INVALID",
    "INPUT_FOCUS_LOST",
}
_WECHAT_RPA_TIMEOUT_SECONDS = 600
# 文心联网采集单题超时：提问+生成+稳定判断最多约 90s，留余量到 180s。
_WENXIN_COLLECT_TIMEOUT_SECONDS = 180


class ProjectAssociationProviders:
    """复用 llm_gateway、可见 Playwright 与微信 PowerShell。"""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        audit_callback: Callable[..., None] | None = None,
        gateway=None,
    ):
        self._root = Path(repository_root)
        self._audit_callback = audit_callback
        # gateway=None 时走模块级 llm_gateway（延迟解析，便于测试 monkeypatch）；
        # 客户端 CLI 传 ProxyLLMGateway 走服务端代理计费。
        self._gateway_override = gateway
        # Provider 在 CLI 单批次/UI 单次运行内创建一次，微信调用由 enricher 串行执行。
        # 只有上一条明确完成且 session_closed=true，下一条才允许消费一次交接。
        self._wechat_handoff_ready = False
        self._wechat_query_index = 0
        self._wechat_handoff_sleep = asyncio.sleep

    @property
    def _gateway(self):
        """延迟解析：传了 gateway 用传的，否则取模块级 llm_gateway（兼容测试 monkeypatch）。"""
        return self._gateway_override if self._gateway_override is not None else llm_gateway

    def _audit(self, **event) -> None:
        if self._audit_callback is None:
            return
        try:
            self._audit_callback(**event)
        except Exception:
            # 审计展示故障不能改变资料采集结果。
            pass

    @staticmethod
    def _validated_wechat_artifact_path(artifact_ref: str) -> Path:
        artifact_root = (
            Path(os.environ["LOCALAPPDATA"])
            / "AidWorkAgent"
            / "wechat-souyisou-rpa"
            / "artifacts"
        ).resolve()
        candidate = Path(artifact_ref).resolve()
        if (
            candidate.parent != artifact_root
            or not re.fullmatch(r"[a-f0-9]{32}\.dpapi", candidate.name)
            or not candidate.is_file()
        ):
            raise ValueError("WECHAT_ARTIFACT_REF_INVALID")
        return candidate

    async def _strict_json_chat(self, messages: list[dict], *, max_tokens: int) -> dict:
        """Retry once when a provider returns non-JSON or a non-object."""
        retry_messages = list(messages)
        for attempt in range(2):
            response = await self._gateway.chat(
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
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                if attempt:
                    raise ValueError("LLM_STRICT_JSON_INVALID") from exc
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
        rejected: list[str] | None = None,
    ) -> dict[str, str | None]:
        """网络回退路径的结构安全校验。

        只校验 schema、字段类型、手机号业务隔离和 value 非空；不再校验
        evidence_quote 是否在 source_text 中或 value 是否在 quote 中。模型解析出的
        字段一律接受，evidence_quote/source_url 仅作审计记录。
        """
        if set(parsed) - set(PROFILE_FIELDS):
            raise ValueError("WEB_FALLBACK_SCHEMA_INVALID")
        rejected = rejected if rejected is not None else []
        result: dict[str, str | None] = {}
        for name in PROFILE_FIELDS:
            evidence = parsed.get(name)
            if not isinstance(evidence, dict) or set(evidence) != {
                "value", "evidence_quote", "source_url",
            }:
                result[name] = None
                rejected.append(f"{name}:WEB_FALLBACK_FIELD_SCHEMA_INVALID")
                continue
            value = evidence["value"]
            quote = evidence["evidence_quote"]
            source_url = evidence["source_url"]
            if value is None:
                if quote is not None or source_url is not None:
                    rejected.append(f"{name}:WEB_FALLBACK_NULL_EVIDENCE_INVALID")
                result[name] = None
                continue
            if name in {"president_mobile", "secretary_general_mobile"}:
                result[name] = None
                rejected.append(f"{name}:WEB_FALLBACK_MOBILE_FORBIDDEN")
                continue
            if not isinstance(value, str) or not value.strip():
                result[name] = None
                rejected.append(f"{name}:WEB_FALLBACK_EVIDENCE_INVALID")
                continue
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
            try:
                process.kill()
            except ProcessLookupError:
                # The child may exit between wait_for timing out and kill().
                # It still exceeded the caller's deadline, so preserve the
                # stable timeout contract while reaping it below.
                pass
            await process.wait()
            raise RuntimeError(error_code) from None

    async def collect_official_profile(
        self, entry_url: str, headless: bool, *, association_name: str = ""
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
            navigation_timeout_ms=10_000,
            audit_callback=self._audit,
        )
        result = await extract_association_profile(pages, domain, gateway=self._gateway)
        if result.status != "success" and result.reason_code in {
            "STRICT_JSON_INVALID", "PROFILE_SCHEMA_INVALID", "INVALID_EVIDENCE",
        }:
            result = await extract_association_profile(pages, domain, gateway=self._gateway)
        values = {name: None for name in PROFILE_FIELDS}
        if result.status == "success" and result.profile is not None:
            values.update({
                name: getattr(result.profile, name)
                for name in PROFILE_FIELDS
            })
        if not (
            values.get("president_name")
            and values.get("secretary_general_name")
        ):
            leadership_pages = [
                page
                for page in pages
                if any(
                    term in f"{page.title}\n{page.content}"
                    for term in ("会长", "秘书长", "组织领导", "领导班子")
                )
            ]
            try:
                focused = await self._extract_leadership(
                    leadership_pages[:2], domain
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._audit(
                    association=domain,
                    stage="官网领导独立提取",
                    kind="official_leadership_failed",
                    summary=(str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]+", str(exc)) else type(exc).__name__),
                )
                focused = {}
            for name, value in focused.items():
                if value and not values.get(name):
                    values[name] = value
        if result.status != "success" and not any(values.values()):
            raise ValueError(result.reason_code or "OFFICIAL_EXTRACTION_FAILED")
        return values

    async def _extract_leadership(
        self,
        pages,
        verified_domain: str,
    ) -> dict[str, str | None]:
        if not pages:
            return {"president_name": None, "secretary_general_name": None}
        parsed = await self._strict_json_chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "只从给定协会官网页面提取现任会长和秘书长姓名，只输出严格JSON。"
                        "顶层键必须恰好为president_name、secretary_general_name；"
                        "每个值必须恰好包含value、evidence_quote、source_url。"
                        "未找到时三个值均为null，不得输出其他字段。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        [
                            {
                                "url": str(page.url),
                                "title": page.title,
                                "content": page.content,
                            }
                            for page in pages
                        ],
                        ensure_ascii=False,
                    ),
                },
            ],
            max_tokens=800,
        )
        if set(parsed) != {"president_name", "secretary_general_name"}:
            raise ValueError("LEADERSHIP_SCHEMA_INVALID")
        result: dict[str, str | None] = {}
        for field_name in ("president_name", "secretary_general_name"):
            try:
                evidence = parsed[field_name]
                if not isinstance(evidence, dict) or set(evidence) != {
                    "value", "evidence_quote", "source_url",
                }:
                    raise ValueError("LEADERSHIP_FIELD_SCHEMA_INVALID")
                value = evidence["value"]
                if value is None:
                    result[field_name] = None
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("LEADERSHIP_EVIDENCE_INVALID")
                result[field_name] = value.strip()
            except (KeyError, TypeError, ValueError):
                result[field_name] = None
        return result

    async def _collect_wenxin(self, association_name: str) -> dict | None:
        """进程内调用文心联网采集协会基础信息原文。

        用 exe 内嵌的 playwright 连接 9222 常驻浏览器（由 ensure_wenxin_browser
        保证就绪），返回 collect_one 的 {ok, answer, note}。不再 spawn 外部
        python——PyInstaller onefile 打包后无客户机 python 可用，进程内调用让
        打包 exe 真正自包含（客户机零安装）。任何失败返回 None，由上层走
        DeepSeek 兜底——文心只是优化信息源，不可让它拖垮整步。
        """
        try:
            from runtime.wenxin_collector import collect_one

            return await asyncio.wait_for(
                collect_one(association_name),
                timeout=_WENXIN_COLLECT_TIMEOUT_SECONDS,
            )
        except Exception:
            # playwright/CDP 异常或超时；文心非关键，降级 DeepSeek 不拖垮整步。
            return None

    def _wechat_script_path(self, filename: str) -> Path:
        """wechat ps1 路径：打包下从 _MEIPASS/scripts/，dev 下从源码仓库解析。

        build.spec 把 scripts/*.ps1 打成扁平 _MEIPASS/scripts/（非源码的
        clients/wechat-souyisou-rpa/scripts/ 嵌套结构），故打包后不能照
        self._root 模式解析——照 runtime.powershell_runner.scripts_dir() 做
        frozen-aware 解析。
        """
        import sys
        if getattr(sys, "frozen", False):
            base = (
                Path(sys._MEIPASS)
                if hasattr(sys, "_MEIPASS")
                else Path(sys.executable).parent
            )
            return base / "scripts" / filename
        return (
            self._root
            / "clients"
            / "wechat-souyisou-rpa"
            / "scripts"
            / filename
        )

    async def search_profile(self, association_name: str) -> dict[str, str | None]:
        """第1步：文心联网采集协会基础信息原文 → DeepSeek 解析成结构化字段。

        文心联网采集根治 DeepSeek 不联网直出官网的幻觉。文心失败时 fallback
        原 DeepSeek 直出（保底，无浏览器/服务端环境仍可运行，不至于整步空）。
        对外接口（dict[str, str|None]）不变。
        """
        # 手机号始终不由本步返回——人员手机号由微信搜一搜精确取证，避免幻觉。
        # 会长/秘书长姓名：文心联网原文路径返回（联网核实，可靠）；文心采集失败走
        # DeepSeek 直出兜底时不返回（不联网，姓名易过期/幻觉，交给官网组织领导页或
        # 微信搜一搜精确获取）。
        _mobile_locked = ("president_mobile", "secretary_general_mobile")
        _leader_name_locked = ("president_name", "secretary_general_name")

        # 1) 文心联网采集原文（含"官网网址：..."），根治官网幻觉
        wenxin = await self._collect_wenxin(association_name)
        raw_text = wenxin.get("answer") if (
            isinstance(wenxin, dict)
            and wenxin.get("ok")
            and isinstance(wenxin.get("answer"), str)
        ) else None
        has_raw_text = bool(raw_text and raw_text.strip())

        if has_raw_text:
            # 2a) 有原文：DeepSeek 从原文提取，官网从原文「官网网址」取，不再靠模型瞎猜；
            #     会长/秘书长从原文「现任会长/秘书长」提取（联网核实，可靠）
            search_fields = [
                name for name in PROFILE_FIELDS if name not in _mobile_locked
            ]
            system_prompt = (
                "你是协会信息提取器。下面是文心一言联网采集到的协会资料原文，"
                "请严格依据该原文提取字段，原文未提及的值返回 null，不要编造或补全。"
                f"只输出严格 JSON 对象，键恰好为：{','.join(search_fields)}。"
                "每个值是字符串或 null。"
                "official_website 必须从原文「官网网址」一行提取该协会真实的官网完整网址"
                "（原文未给出网址则设为 null，严禁照搬示例域名后缀猜测）。"
                "president_name、secretary_general_name 只取原文明确写明的现任会长、秘书长姓名，"
                "注意区分现任与离任/前任，原文未明确给出则设为 null。"
                "不要返回手机号。"
            )
            user_content = raw_text.strip()[:8000]
            self._audit(
                association=association_name,
                stage="基础信息·文心采集",
                kind="wenxin_collected",
                summary=f"原文 {len(raw_text.strip())} 字",
            )
        else:
            # 2b) 文心失败：fallback 原 DeepSeek 直出（保底，无浏览器/服务端环境不崩）。
            #     不联网，姓名易过期/幻觉，故只抽基础信息，会长/秘书长交给官网/微信取证。
            search_fields = [
                name for name in PROFILE_FIELDS
                if name not in _mobile_locked + _leader_name_locked
            ]
            self._audit(
                association=association_name,
                stage="基础信息·文心采集",
                kind="wenxin_fallback",
                summary=(
                    str(wenxin.get("note"))
                    if isinstance(wenxin, dict)
                    else "wenxin_collect_failed"
                ),
            )
            system_prompt = (
                "你是一个协会信息提取器。只输出JSON，不要其他文字。"
                f"键必须恰好为：{','.join(search_fields)}。"
                "每个值是字符串或null。找不到的值为null。"
                "official_website 是最重要的字段，必须返回该协会真实的、可访问的官方网站完整网址（含 https://）。"
                "绝对不能猜测或编造网址。如果不确定官网地址，official_website 必须设为 null。"
                "不要返回人员姓名或手机号。"
            )
            user_content = (
                f"给出{association_name}的以下信息："
                "地址、邮箱、官网网址、主管单位、单位等级、会员数量、"
                "分支机构数量、公众号名称。"
            )

        # 3) DeepSeek 严格 JSON 解析（自带围栏去除 + 一次重试）
        parsed = await self._strict_json_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4000,
        )
        values: dict[str, str | None] = {}
        for name in PROFILE_FIELDS:
            value = parsed.get(name)
            if isinstance(value, str) and value.strip():
                values[name] = value.strip()
            else:
                values[name] = None
        # 手机号始终强制 null——人员手机号由微信搜一搜精确取证，不让模型猜测
        for locked in _mobile_locked:
            values[locked] = None
        # 文心采集失败走 DeepSeek 直出时，会长/秘书长也强制 null
        # （不联网易过期/幻觉；交给官网组织领导页或微信搜一搜精确获取）
        if not has_raw_text:
            for locked in _leader_name_locked:
                values[locked] = None
        return values

    async def wechat_mobile(
        self, association_name: str, person_name: str, role: str
    ) -> str | None:
        try:
            handoff_performed = await self._prepare_wechat_query_handoff()
        except Exception as exc:
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary="WECHAT_HANDOFF_FAILED",
            )
            raise WechatRpaError(
                "WECHAT_HANDOFF_FAILED", stage="handoff", session_fatal=True
            ) from exc
        script = self._wechat_script_path("wechat-souyisou.ps1")
        import sys

        child_environment = os.environ.copy()
        child_environment["PATH"] = (
            str(Path(sys.executable).parent)
            + os.pathsep
            + child_environment.get("PATH", "")
        )
        self._audit(
            association=association_name,
            stage=f"微信搜一搜·{role}",
            kind="wechat_start",
            summary=f"开始检索 {person_name}",
            detail={
                "query_index": self._wechat_query_index + 1,
                "handoff_performed": handoff_performed,
            },
        )
        # ps1 collect 正常返回但 status != found（搜一搜焦点丢失/返回空/未匹配
        # 到手机号）时，重试一次——等于重发 Ctrl+F/下/回车 组合键重搜，兜住偶
        # 发焦点/加载问题。fatal 错误（超时、returncode!=0、非 dict 响应、会话
        # 未关闭）在 _run_wechat_collect_once 内直接 raise，不会走到重试。
        for mobile_attempt in range(2):
            payload = await self._run_wechat_collect_once(
                script,
                child_environment,
                association_name,
                person_name,
                role,
            )
            status = payload.get("status")
            if status == "found":
                return await self._extract_wechat_mobile(
                    script,
                    str(payload.get("artifact_ref") or ""),
                    association_name,
                    person_name,
                    role=role,
                )
            # 只在「没读到搜一搜列表/没进详情」(inconclusive + checked=0)时重试一次。
            # 进过详情(checked>0)说明已尽力搜过，重搜结果一样；not_found 等也不重试。
            if status != "inconclusive" or payload.get("checked") or mobile_attempt > 0:
                return None
            # 重试前 alt+tab 把微信切到后台，ps1 打开搜一搜时会 activate 把微信
            # 重新置顶，重置焦点（用户经验：这样一般能保证焦点落到输入框）。
            self._send_alt_tab_once()
            await self._wechat_handoff_sleep(1.0)
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_mobile_empty_retry",
                summary="搜一搜结果为空，重发组合键重试",
            )
        return None

    async def _run_wechat_collect_once(
        self,
        script: Path,
        child_environment: dict,
        association_name: str,
        person_name: str,
        role: str,
    ) -> dict:
        """运行一次 ps1 collect 子进程，返回通过校验的 payload。

        payload 此时 ok=True、session_closed=True，status 是已知枚举值。任何
        fatal 错误（超时、returncode!=0、非 dict 响应、会话未关闭）直接 raise，
        不在此重试——只有「正常返回但 status != found」才由调用方重试。
        """
        import sys
        # 打包 exe（PyInstaller frozen）下，ps1 collect 的 LLM judge 改用 cli exe
        # 的 llm-judge 子命令（内含 ProxyLLMGateway，走服务端代理计费），避免依赖
        # 客户机 python / 本地 key。dev 下不传，ps1 走 python + llm_judge.py。
        judge_args = (
            ["-CliExe", sys.executable] if getattr(sys, "frozen", False) else []
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
            *judge_args,
            "-Limit",
            "3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_environment,
        )
        self._wechat_query_index += 1
        try:
            stdout, stderr = await self._communicate_with_timeout(
                process,
                timeout_seconds=_WECHAT_RPA_TIMEOUT_SECONDS,
                error_code="WECHAT_RPA_TIMEOUT",
            )
        except Exception as exc:
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary=(
                    str(exc)
                    if re.fullmatch(r"[A-Z][A-Z0-9_]+", str(exc))
                    else type(exc).__name__
                ),
            )
            code = str(exc) if str(exc) in _WECHAT_SESSION_FATAL_CODES else "WECHAT_RPA_TIMEOUT"
            raise WechatRpaError(
                code, stage="timeout", session_fatal=True
            ) from exc
        stderr_diagnostic = {
            "stderr_bytes": len(stderr),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest() if stderr else None,
        }
        payload = None
        try:
            payload = json.loads(
                stdout.decode("utf-8-sig").strip().splitlines()[-1]
            )
        except (IndexError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        artifact_ref = (
            payload.get("artifact_ref") if isinstance(payload, dict) else None
        )
        if isinstance(artifact_ref, str):
            try:
                await self._audit_wechat_artifact(
                    artifact_ref,
                    association_name=association_name,
                    person_name=person_name,
                    role=role,
                )
            except Exception as exc:
                self._audit(
                    association=association_name,
                    stage=f"微信搜一搜·{role}",
                    kind="wechat_audit_failed",
                    summary=type(exc).__name__,
                )
        if process.returncode != 0:
            error_code = (
                str(payload.get("error_code"))
                if isinstance(payload, dict)
                and re.fullmatch(r"[A-Z][A-Z0-9_]+", str(payload.get("error_code")))
                else "WECHAT_RPA_FAILED"
            )
            failure_stage = (
                str(payload.get("stage"))
                if isinstance(payload, dict)
                and re.fullmatch(r"[a-z][a-z0-9_]+", str(payload.get("stage")))
                else "unknown"
            )
            recovered_mobile = None
            if (
                isinstance(payload, dict)
                and payload.get("result_status") == "found"
                and isinstance(artifact_ref, str)
            ):
                try:
                    recovered_mobile = await self._extract_wechat_mobile(
                        script, artifact_ref, association_name, person_name
                    )
                except Exception as exc:
                    self._audit(
                        association=association_name,
                        stage=f"微信搜一搜·{role}",
                        kind="wechat_recovery_failed",
                        summary=type(exc).__name__,
                    )
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary=error_code,
                detail={"rpa_stage": failure_stage, **stderr_diagnostic},
            )
            raise WechatRpaError(
                error_code,
                stage=failure_stage,
                session_fatal=(
                    failure_stage == "cleanup"
                    or error_code in _WECHAT_SESSION_FATAL_CODES
                    or (
                        error_code in _WECHAT_INPUT_FAILURE_CODES
                        and payload.get("session_closed") is not True
                    )
                ),
                recovered_mobile=recovered_mobile,
            )
        if not isinstance(payload, dict):
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary="WECHAT_RPA_RESPONSE_INVALID",
            )
            raise RuntimeError("WECHAT_RPA_RESPONSE_INVALID")
        reported_status = payload.get("status")
        if reported_status not in {
            "captured",
            "failed",
            "found",
            "inconclusive",
            "not_found",
        }:
            reported_status = "unknown"
        self._audit(
            association=association_name,
            stage=f"微信搜一搜·{role}",
            kind="wechat_returned",
            summary=(
                f"status={reported_status}; "
                f"session_closed={bool(payload.get('session_closed'))}"
            ),
        )
        if not payload.get("ok") or not payload.get("session_closed"):
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary="WECHAT_SESSION_NOT_CLOSED",
            )
            raise WechatRpaError(
                "WECHAT_SESSION_NOT_CLOSED",
                stage=str(payload.get("stage") or "cleanup"),
                session_fatal=True,
            )
        # 仅明确成功且确认会话关闭的真实查询，才授权下一条发送一次交接键。
        self._wechat_handoff_ready = True
        return payload

    async def wechat_search_leader_name(
        self, association_name: str, role: str, known_president: str = ""
    ) -> str | None:
        """用微信搜一搜搜索协会领导姓名。

        搜 "协会名 会长" 或 "协会名 秘书长"，复制列表文本，让 LLM 解析出姓名。
        搜索完关闭搜一搜窗口。不进详情、不找手机号。
        """
        try:
            handoff_performed = await self._prepare_wechat_query_handoff()
        except Exception as exc:
            raise WechatRpaError(
                "WECHAT_HANDOFF_FAILED", stage="handoff", session_fatal=True
            ) from exc
        script = self._wechat_script_path("wechat-souyisou.ps1")
        import sys

        child_environment = os.environ.copy()
        child_environment["PATH"] = (
            str(Path(sys.executable).parent)
            + os.pathsep
            + child_environment.get("PATH", "")
        )
        self._audit(
            association=association_name,
            stage=f"微信搜领导·{role}",
            kind="wechat_start",
            summary=f"搜索{role}姓名",
            detail={
                "query_index": self._wechat_query_index + 1,
                "handoff_performed": handoff_performed,
            },
        )
        list_text = ""
        for search_attempt in range(2):
            process = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Command",
                "search",
                "-AssociationName",
                association_name,
                "-PersonName",
                role,
                "-Execute",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_environment,
            )
            self._wechat_query_index += 1
            try:
                stdout, stderr = await self._communicate_with_timeout(
                    process,
                    timeout_seconds=_WECHAT_RPA_TIMEOUT_SECONDS,
                    error_code="WECHAT_RPA_TIMEOUT",
                )
            except Exception as exc:
                code = str(exc) if str(exc) in _WECHAT_SESSION_FATAL_CODES else "WECHAT_RPA_TIMEOUT"
                raise WechatRpaError(
                    code, stage="timeout", session_fatal=True
                ) from exc
            payload = None
            try:
                payload = json.loads(
                    stdout.decode("utf-8-sig").strip().splitlines()[-1]
                )
            except (IndexError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            artifact_ref = (
                payload.get("artifact_ref") if isinstance(payload, dict) else None
            )
            if isinstance(artifact_ref, str):
                try:
                    list_text = await self._read_wechat_search_list_text(artifact_ref)
                except Exception:
                    pass
            if list_text.strip():
                break
            # 搜索列表为空（搜一搜结果没读到/没加载）：重试一次，等于重发
            # Ctrl+F/下/回车 组合键 + 重输 + 重搜。偶发焦点/加载问题靠它兜底。
            if search_attempt == 0:
                # 重试前 alt+tab 把微信切到后台，ps1 打开搜一搜时 activate 回前台，重置焦点
                self._send_alt_tab_once()
                await self._wechat_handoff_sleep(1.0)
                self._audit(
                    association=association_name,
                    stage=f"微信搜领导·{role}",
                    kind="list_text_empty_retry",
                    summary="搜索列表为空，重发组合键重试",
                )
        if not list_text.strip():
            self._audit(
                association=association_name,
                stage=f"微信搜领导·{role}",
                kind="list_text_empty",
                summary="搜索列表文本为空",
            )
            return None
        # LLM 从列表文本解析领导姓名
        president_hint = f"当前会长是{known_president}，" if known_president else ""
        parsed = await self._strict_json_chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"从下面微信搜一搜的搜索结果中，找出这个协会现任{role}的姓名。"
                        f"{president_hint}"
                        "只输出JSON：{\"name\":\"姓名\"}。找不到时输出{\"name\":null}。"
                        "注意区分现任和前任，只提取最新的。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"协会：{association_name}\n角色：{role}\n\n搜索结果：\n{list_text[:4000]}",
                },
            ],
            max_tokens=1000,
        )
        name = parsed.get("name")
        if isinstance(name, str) and name.strip():
            self._audit(
                association=association_name,
                stage=f"微信搜领导·{role}",
                kind="leader_name_found",
                summary=name.strip(),
            )
            return name.strip()
        self._audit(
            association=association_name,
            stage=f"微信搜领导·{role}",
            kind="leader_name_not_found",
            summary="LLM未能从搜索结果解析出姓名",
        )
        return None

    async def _read_wechat_search_list_text(self, artifact_ref: str) -> str:
        """读取微信 search 命令保存的列表文本（DPAPI artifact）。"""
        artifact_path = Path(artifact_ref)
        if not artifact_path.exists():
            return ""
        import subprocess
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File",
                str(self._wechat_script_path("read-artifact.ps1")),
                "-ArtifactPath", str(artifact_path),
            ],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            return ""
        import json as _json
        try:
            data = _json.loads(result.stdout.decode("utf-8-sig"))
            return str(data.get("text") or "")
        except Exception:
            return ""

    async def _prepare_wechat_query_handoff(self) -> bool:
        if not self._wechat_handoff_ready:
            return False
        # 先消费资格；发送或等待失败时不得在后续调用再次向未知前台补发。
        self._wechat_handoff_ready = False
        self._send_alt_tab_once()
        await self._wechat_handoff_sleep(1.0)
        return True

    @staticmethod
    def _send_alt_tab_once() -> None:
        if os.name != "nt":
            raise RuntimeError("WECHAT_HANDOFF_WINDOWS_REQUIRED")
        import ctypes

        user32 = ctypes.windll.user32
        vk_menu = 0x12
        vk_tab = 0x09
        key_up = 0x0002
        user32.keybd_event(vk_menu, 0, 0, 0)
        try:
            user32.keybd_event(vk_tab, 0, 0, 0)
            user32.keybd_event(vk_tab, 0, key_up, 0)
        finally:
            user32.keybd_event(vk_menu, 0, key_up, 0)

    async def _extract_wechat_mobile(
        self,
        script: Path,
        artifact_ref: str,
        association_name: str,
        person_name: str,
        *,
        role: str = "负责人",
    ) -> str | None:
        try:
            artifact_path = self._validated_wechat_artifact_path(artifact_ref)
        except (KeyError, OSError, ValueError) as exc:
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary="WECHAT_ARTIFACT_REF_INVALID",
            )
            raise RuntimeError("WECHAT_ARTIFACT_REF_INVALID") from exc
        helper = script.with_name("extract-mobile.ps1")
        extraction = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-ArtifactPath",
            str(artifact_path),
            "-AssociationName",
            association_name,
            "-PersonName",
            person_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            private_stdout, _private_stderr = await self._communicate_with_timeout(
                extraction,
                timeout_seconds=30,
                error_code="WECHAT_ARTIFACT_READ_TIMEOUT",
            )
        except Exception as exc:
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary=(
                    str(exc)
                    if re.fullmatch(r"[A-Z][A-Z0-9_]+", str(exc))
                    else type(exc).__name__
                ),
            )
            raise
        if extraction.returncode != 0:
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary="WECHAT_ARTIFACT_READ_FAILED",
            )
            raise RuntimeError("WECHAT_ARTIFACT_READ_FAILED")
        try:
            evidence = json.loads(private_stdout.decode("utf-8-sig").strip())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary="WECHAT_ARTIFACT_INVALID",
            )
            raise RuntimeError("WECHAT_ARTIFACT_INVALID") from exc
        mobile = evidence.get("mobile") if evidence.get("matched") else None
        if mobile is not None and not re.fullmatch(r"1[3-9]\d{9}", str(mobile)):
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_failed",
                summary="WECHAT_ARTIFACT_INVALID",
            )
            raise RuntimeError("WECHAT_ARTIFACT_INVALID")
        self._audit(
            association=association_name,
            stage=f"微信搜一搜·{role}",
            kind="wechat_complete",
            summary="已完成微信取证",
        )
        return str(mobile) if mobile is not None else None

    async def _audit_wechat_artifact(
        self,
        artifact_ref: str,
        *,
        association_name: str,
        person_name: str,
        role: str,
    ) -> None:
        helper = self._wechat_script_path("read-artifact.ps1")
        if not helper.is_file():
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_audit_failed",
                summary="WECHAT_AUDIT_HELPER_NOT_FOUND",
            )
            return
        try:
            artifact_path = self._validated_wechat_artifact_path(artifact_ref)
            process = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
                "-ArtifactPath",
                str(artifact_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await self._communicate_with_timeout(
                process,
                timeout_seconds=15,
                error_code="WECHAT_AUDIT_READ_TIMEOUT",
            )
            if process.returncode != 0:
                raise RuntimeError("WECHAT_AUDIT_READ_FAILED")
            artifact = json.loads(stdout.decode("utf-8-sig"))
        except Exception as exc:
            self._audit(
                association=association_name,
                stage=f"微信搜一搜·{role}",
                kind="wechat_audit_failed",
                summary=type(exc).__name__,
            )
            return
        list_artifact = artifact.get("list_artifact")
        if not isinstance(list_artifact, dict):
            list_artifact = {}
        records = artifact.get("records")
        if not isinstance(records, list):
            records = []
        llm_usages = artifact.get("llm_usages")
        if isinstance(llm_usages, list):
            for usage in llm_usages:
                record_usage(usage)
        self._audit(
            association=association_name,
            stage=f"微信搜一搜·{role}",
            kind="wechat_artifact",
            summary=f"{person_name}：列表及详情记录 {len(records)} 条",
            detail={
                "query": artifact.get("query"),
                "source": artifact.get("source"),
                "list_text": list_artifact.get("text") or artifact.get("text"),
                "records": [
                    {
                        "ordinal": record.get("ordinal"),
                        "text": record.get("text"),
                        "ocr_text": record.get("ocr_text"),
                        "ocr_performed": bool(record.get("ocr_hashes")),
                    }
                    for record in records
                    if isinstance(record, dict)
                ],
            },
        )
