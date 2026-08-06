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


# ============== collect 命令 ==============

async def cmd_collect(args: argparse.Namespace) -> int:
    """协会信息收集主流程。"""
    access_token = get_access_token()
    if not access_token:
        print(json.dumps({"event": "error", "error_code": "NOT_ACTIVATED", "message": "客户端未激活"}))
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
    emit_start(session_id, names, server_url)
    emit_log("INFO", f"开始收集 {len(names)} 个协会")

    # 构造 ProxyLLMGateway 并注入 providers
    gateway = ProxyLLMGateway(server_url, access_token)

    try:
        from src.services.association_enrichment_providers import ProjectAssociationProviders

        providers = ProjectAssociationProviders(
            repository_root=ROOT,
            gateway=gateway,
        )

        from src.services.association_batch_enrichment import (
            AssociationBatchEnricher,
            write_enrichment_workbook,
        )

        reporter = CliProgressReporter()
        enricher = AssociationBatchEnricher(
            official_profile_collector=providers.collect_official_profile,
            fallback_profile_provider=providers.search_profile,
            wechat_mobile_provider=providers.wechat_mobile,
            wechat_leader_name_provider=providers.wechat_search_leader_name,
            headless=False,
            progress_reporter=reporter,
        )

        rows = await enricher.enrich_many(names)
        emit_log("INFO", "正在写入 Excel 结果")
        output = write_enrichment_workbook(rows, args.output)

        # 统计
        complete_count = sum(1 for r in rows if r.processing_status == "complete")
        partial_count = sum(1 for r in rows if r.processing_status == "partial")
        failed_count = sum(1 for r in rows if r.processing_status == "failed")

        emit_complete(
            session_id=session_id,
            total_consumed=reporter.total_consumed,
            output=output,
            summary={
                "total": len(rows),
                "complete": complete_count,
                "partial": partial_count,
                "failed": failed_count,
            },
        )

        # 退出码：全部成功 0，部分失败 2，全失败 1
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
        error_code = str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]+", str(exc)) else type(exc).__name__
        emit_error(
            association="",
            error_code=error_code,
            message=f"收集过程出错: {type(exc).__name__}",
            session_fatal=True,
        )
        return 1
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

    # collect
    p_col = sub.add_parser("collect", help="协会信息收集")
    p_col.add_argument("--associations", nargs="*", default=[], help="协会名称列表")
    p_col.add_argument("--input", default=None, help="输入文件（CSV/XLSX）")
    p_col.add_argument("--output", required=True, help="输出 XLSX 路径")
    p_col.add_argument("--server-url", default=None, help="服务端地址（覆盖配置）")
    p_col.add_argument("--no-wechat", action="store_true", help="跳过微信RPA步骤（调试用）")

    return parser


def main() -> int:
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
    elif args.command == "collect":
        return await cmd_collect(args)
    else:
        parser = build_parser()
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
