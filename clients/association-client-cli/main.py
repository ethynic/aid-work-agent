"""
协会信息收集客户端 CLI 入口。

三个命令：
    activate  —— 激活码激活，换取 access_token 存本地
    credits   —— 查询租户积分余额
    collect   —— 协会信息收集主流程（5步流水线）

用法见设计文档 §3.2。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Optional

# 让 src.* 可 import（项目根目录）
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from runtime.config import (  # noqa: E402
    clear_config,
    get_access_token,
    get_machine_id,
    get_server_url,
    load_config,
    save_config,
)
from runtime.progress_reporter import (  # noqa: E402
    CliProgressReporter,
    emit_complete,
    emit_error,
    emit_log,
    emit_start,
)
from runtime.proxy_gateway import NoCreditError, ProxyLLMGateway  # noqa: E402


# ============== activate 命令 ==============

async def cmd_activate(args: argparse.Namespace) -> int:
    """激活码激活。"""
    server_url = get_server_url(args.server_url)
    machine_id = get_machine_id()

    payload = {
        "activation_code": args.code.strip().upper(),
        "machine_id": machine_id,
        "client_name": args.client_name or "",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{server_url}/api/client/v1/activate",
                json=payload,
            )
    except httpx.ConnectError as exc:
        print(f"无法连接服务端 {server_url}: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            pass
        print(f"激活失败（{resp.status_code}）: {detail}", file=sys.stderr)
        return 1

    data = resp.json()

    # 保存配置
    config = {
        "binding_id": data["binding_id"],
        "access_token": data["access_token"],
        "tenant_id": data["tenant_id"],
        "tenant_name": data.get("tenant_name", ""),
        "server_url": server_url,
        "machine_id": machine_id,
    }
    save_config(config)

    # stdout 输出 JSON（供 Electron 解析）
    result = {
        "ok": True,
        "binding_id": data["binding_id"],
        "tenant_id": data["tenant_id"],
        "tenant_name": data.get("tenant_name"),
        "credit_balance": data.get("credit_balance"),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


# ============== credits 命令 ==============

async def cmd_credits(args: argparse.Namespace) -> int:
    """查询积分余额。"""
    access_token = get_access_token()
    if not access_token:
        print("客户端未激活，请先执行 activate 命令", file=sys.stderr)
        return 1

    server_url = get_server_url(args.server_url)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{server_url}/api/client/v1/credits",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.ConnectError as exc:
        print(f"无法连接服务端: {exc}", file=sys.stderr)
        return 1

    if resp.status_code == 401:
        print("客户端令牌无效，请重新激活", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(f"查询失败（{resp.status_code}）", file=sys.stderr)
        return 1

    data = resp.json()
    print(json.dumps(data, ensure_ascii=False))
    return 0


async def cmd_credits_detail(args: argparse.Namespace) -> int:
    """查询消耗明细。"""
    access_token = get_access_token()
    if not access_token:
        print("客户端未激活", file=sys.stderr)
        return 1

    server_url = get_server_url(args.server_url)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{server_url}/api/client/v1/credits/detail",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"limit": getattr(args, 'limit', 100)},
            )
    except httpx.ConnectError as exc:
        print(f"无法连接服务端: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(f"查询失败（{resp.status_code}）", file=sys.stderr)
        return 1

    data = resp.json()
    print(json.dumps(data, ensure_ascii=False))
    return 0


# ============== collect 命令 ==============

def _query_balance(server_url: str, access_token: str) -> Optional[float]:
    """查询当前积分余额，失败返回 None。"""
    try:
        with httpx.Client(timeout=10) as c:
            resp = c.get(
                f"{server_url}/api/client/v1/credits",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            return float(resp.json().get("balance", 0))
    except Exception:
        pass
    return None


def _make_credit_guard(server_url: str, access_token: str):
    """余额守卫：每个协会开始前检查租户积分，不足即抛 NoCreditError 停止批次。

    查询失败（网络抖动返回 None）不阻断——沿用 402 兜底（服务端每次 LLM
    调用前扣减校验），透支上限被压到一个协会的消耗内。
    """

    async def _guard():
        balance = _query_balance(server_url, access_token)
        if balance is not None and balance <= 0:
            raise NoCreditError(
                f"积分余额不足（{balance:.2f}），已停止后续协会采集，"
                f"充值后重新运行即可继续"
            )

    return _guard

async def cmd_collect(args: argparse.Namespace) -> int:
    """协会信息收集主流程。"""
    access_token = get_access_token()
    if not access_token:
        emit_error(
            association="",
            error_code="NOT_ACTIVATED",
            message="客户端未激活",
        )
        return 1

    server_url = get_server_url(args.server_url)

    # 解析协会名输入
    from src.services.association_batch_enrichment import parse_association_input

    names = parse_association_input(
        text_values=args.associations,
        input_path=args.input,
    )
    if not names:
        emit_log("ERROR", "未解析到有效的协会名称")
        return 1

    session_id = str(uuid.uuid4())
    from runtime import run_log, telemetry
    run_log.set_session_id(session_id)
    telemetry.set_session_id(session_id)
    emit_start(session_id, names, server_url)
    emit_log("INFO", f"开始收集 {len(names)} 个协会")

    # 记录任务开始前余额（任务结束后用差值算真实总消耗，含微信 judge 子进程的消耗）
    balance_before = _query_balance(server_url, access_token)

    # 构造 ProxyLLMGateway 并注入 providers
    gateway = ProxyLLMGateway(server_url, access_token)

    try:
        from src.services.association_enrichment_providers import ProjectAssociationProviders

        def _audit_to_log(**event):
            # 审计事件落到 app.log，便于排查采集分支（文心原文 wenxin_collected /
            # 降级 wenxin_fallback）、原文长度、失败 note（captcha/cdp_attach_failed 等）。
            # association/stage/kind/detail 透传给遥测：经 /api/client/v1/logs 落到
            # 服务端 client_usage_logs（官网未拿到秘书长等事件按 audit_kind 可查）
            extra = {"stage": event.get("stage", ""), "audit_kind": event.get("kind", "")}
            detail = event.get("detail")
            if detail:
                extra["audit_detail"] = detail
            emit_log(
                "INFO",
                f"[audit] stage={event.get('stage', '')} kind={event.get('kind', '')} "
                f"summary={event.get('summary', '')}",
                association=event.get("association", "") or "",
                **extra,
            )

        providers = ProjectAssociationProviders(
            repository_root=ROOT,
            gateway=gateway,
            audit_callback=_audit_to_log,
        )

        # 第1步改走文心联网采集，需要常开调试浏览器(9222)。失败仅警告——
        # providers.search_profile 会 fallback 到 DeepSeek 兜底，不致命。
        try:
            from runtime.wenxin_browser import ensure_wenxin_browser

            await ensure_wenxin_browser()
            emit_log("INFO", "文心采集浏览器已就绪(localhost:9222)")
        except Exception as exc:
            emit_log("WARNING", f"文心浏览器未就绪，第1步将降级为 DeepSeek 直出：{exc}")

        from src.services.association_batch_enrichment import (
            AssociationBatchEnricher,
            write_enrichment_workbook,
        )

        reporter = CliProgressReporter()
        reporter.gateway = gateway  # 让 reporter 同步 current_association 到 gateway
        enricher = AssociationBatchEnricher(
            official_profile_collector=providers.collect_official_profile,
            fallback_profile_provider=providers.search_profile,
            wechat_mobile_provider=providers.wechat_mobile,
            wechat_leader_name_provider=providers.wechat_search_leader_name,
            wenxin_secretary_mobile_provider=providers.wenxin_search_secretary_mobile,
            headless=False,
            progress_reporter=reporter,
            credit_guard=_make_credit_guard(server_url, access_token),
        )

        rows = await enricher.enrich_many(names)
        if rows.aborted:
            # 余额不足等批次熔断：明确提示 + 服务端遥测，充值后重跑即可
            remaining = [
                r.association_name
                for r in rows
                if r.processing_status == "aborted"
            ]
            emit_error(
                association="",
                error_code=rows.abort_error_code or "BATCH_ABORTED",
                message=(
                    f"批次已停止（{rows.abort_error_code}），"
                    f"未处理协会：{'、'.join(remaining) or '无'}；充值后重新运行即可继续"
                ),
                session_fatal=True,
            )
        emit_log("INFO", "正在写入 Excel 结果")
        output = str(write_enrichment_workbook(rows, args.output))

        # 真实总消耗 = 任务前余额 - 任务后余额（含微信 judge 子进程的消耗）
        balance_after = _query_balance(server_url, access_token)
        total_consumed = round(balance_before - balance_after, 2) if balance_before is not None and balance_after is not None else reporter.total_consumed

        # 统计
        complete_count = sum(1 for r in rows if r.processing_status == "complete")
        partial_count = sum(1 for r in rows if r.processing_status == "partial")
        failed_count = sum(1 for r in rows if r.processing_status == "failed")
        aborted_count = sum(1 for r in rows if r.processing_status == "aborted")

        emit_complete(
            session_id=session_id,
            total_consumed=total_consumed,
            output=output,
            summary={
                "total": len(rows),
                "complete": complete_count,
                "partial": partial_count,
                "failed": failed_count,
                "aborted": aborted_count,
            },
        )

        # 退出码：全部成功 0，部分失败 2，全失败 1，余额不足熔断 3
        if rows.aborted:
            return 3
        if failed_count == len(rows):
            return 1
        if failed_count > 0:
            return 2
        return 0

    except NoCreditError as exc:
        emit_error(
            association="",
            error_code="NO_CREDIT",
            message=str(exc),
            session_fatal=True,
        )
        return 3
    except KeyboardInterrupt:
        emit_log("WARNING", "用户中断")
        return 1
    except Exception as exc:
        import traceback
        traceback.print_exc()  # 输出到 stderr 便于调试
        error_code = str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]+", str(exc)) else type(exc).__name__
        emit_error(
            association="",
            error_code=error_code,
            message=f"收集过程出错: {type(exc).__name__}: {exc}",
            session_fatal=True,
        )
        return 1
    finally:
        try:
            await telemetry.flush()
        except Exception:
            pass
        gateway.close()


# ============== llm-judge 命令 ==============

async def cmd_llm_judge(args: argparse.Namespace) -> int:
    """LLM 证据判断（stdin JSON → stdout JSON，供 ps1 collect 调用）。

    打包 exe 内含 python 运行时 + ProxyLLMGateway（走服务端代理计费），不依赖
    客户机 python / 本地 key。逻辑单点在 runtime/llm_judge.py 的 run_judge。
    """
    access_token = get_access_token()
    if not access_token:
        # 无 token 无法走代理，输出 inconclusive 让 ps1 走兜底分支。
        sys.stdout.write(json.dumps({"inconclusive": True}, ensure_ascii=False) + "\n")
        return 1

    server_url = get_server_url(args.server_url)
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.stderr.write("judge_failed:ValueError\n")
        return 2

    gateway = ProxyLLMGateway(server_url, access_token)
    try:
        from runtime.llm_judge import run_judge

        # 隔离 run_judge / gateway 内部日志，保证 stdout 只有一行 JSON。
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = await run_judge(payload, gateway)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    except Exception as exc:
        import os as _os
        import traceback as _tb
        try:
            with open(_os.path.join(_os.environ.get("TEMP", ""), "wechat_diag.log"), "a", encoding="utf-8") as _f:
                _f.write(f"cmd_llm_judge EXCEPTION: {_tb.format_exc()}\n")
        except Exception:
            pass
        token_usage = getattr(exc, "token_usage", None)
        if isinstance(token_usage, dict) and any(token_usage.values()):
            sys.stdout.write(json.dumps(
                {"inconclusive": True, "token_usage": token_usage},
                ensure_ascii=False,
                separators=(",", ":"),
            ) + "\n")
            return 0
        sys.stderr.write(f"judge_failed:{type(exc).__name__}\n")
        return 2
    finally:
        gateway.close()


# ============== 命令行解析 ==============

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="association-client",
        description="协会信息收集客户端 CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # activate
    p_act = sub.add_parser("activate", help="激活码激活")
    p_act.add_argument("--code", required=True, help="激活码（格式 AC-XXXXXXXXXXXX）")
    p_act.add_argument("--client-name", default="", help="客户端显示名")
    p_act.add_argument("--server-url", default=None, help="服务端地址（默认从配置或 https://agent.aidingyi.cn）")

    # credits
    p_cred = sub.add_parser("credits", help="查询积分余额")
    p_cred.add_argument("--server-url", default=None)

    # credits-detail
    p_detail = sub.add_parser("credits-detail", help="查询消耗明细")
    p_detail.add_argument("--server-url", default=None)
    p_detail.add_argument("--limit", type=int, default=100)

    # collect
    p_col = sub.add_parser("collect", help="协会信息收集")
    p_col.add_argument("--associations", nargs="*", default=[], help="协会名称列表")
    p_col.add_argument("--input", default=None, help="输入文件（CSV/XLSX）")
    p_col.add_argument("--output", required=True, help="输出 XLSX 路径")
    p_col.add_argument("--server-url", default=None, help="服务端地址（覆盖配置）")
    p_col.add_argument("--no-wechat", action="store_true", help="跳过微信RPA步骤（调试用）")

    # llm-judge
    p_judge = sub.add_parser(
        "llm-judge",
        help="LLM 证据判断（stdin JSON → stdout JSON，供 ps1 collect 调用）",
    )
    p_judge.add_argument("--server-url", default=None, help="服务端地址（覆盖配置）")

    return parser


def _ensure_utf8_stdout() -> None:
    """强制 stdin/stdout/stderr UTF-8。

    PyInstaller exe 在 Windows pipe 默认 cp936（GBK）：stdout/stderr 让中文乱码；
    stdin 会把 UTF-8 的中文 JSON（如 llm-judge 读的 payload）解码出 surrogate，
    导致 httpx 编码报 UnicodeEncodeError。显式 reconfigure 兜底。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main() -> int:
    _ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args()

    try:
        return asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        return 130


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "activate":
        return await cmd_activate(args)
    elif args.command == "credits":
        return await cmd_credits(args)
    elif args.command == "credits-detail":
        return await cmd_credits_detail(args)
    elif args.command == "collect":
        return await cmd_collect(args)
    elif args.command == "llm-judge":
        return await cmd_llm_judge(args)
    else:
        parser = build_parser()
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
