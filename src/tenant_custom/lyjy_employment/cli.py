"""聊易就业同步 CLI（薄壳，业务全在本包；用法见 --help）。

用法（容器内 / agent2 宿主机执行）:
  python -m src.tenant_custom.lyjy_employment.cli --init-source \
      --tenant-id <租户> --db-host <host> --db-port 3306 --db-name gt_lyjy \
      --db-user <只读账号>          # 密码交互输入（getpass，不进 shell 历史）
  python -m src.tenant_custom.lyjy_employment.cli --dry-run --tenant-id <租户>
  python -m src.tenant_custom.lyjy_employment.cli --limit 3 --tenant-id <租户>   # 冒烟
  python -m src.tenant_custom.lyjy_employment.cli --tenant-id <租户>             # 全量
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys


def _init_source(args) -> int:
    from src.db.database import init_postgres_pool
    from src.tenant_custom.lyjy_employment.bootstrap import init_source

    init_postgres_pool()
    password = args.db_password or getpass.getpass("客户库密码: ")
    inserted = init_source(args.tenant_id, {
        "host": args.db_host,
        "port": args.db_port,
        "database": args.db_name,
        "user": args.db_user,
        "password": password,
    })
    print(f"[source] {'created' if inserted else 'updated'} tenant={args.tenant_id}", flush=True)
    return 0


def _load_source_config(tenant_id: str) -> dict:
    """CLI 路径取源行连接配置（run 执行入口会再走服务内同款逻辑）。"""
    from src.db.database import get_db_connection, init_postgres_pool
    from src.tenant_custom.lyjy_employment.fetcher import load_connection_config

    init_postgres_pool()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_content_sync_sources "
            "WHERE tenant_id = %s AND module = 'lyjy_employment'",
            (tenant_id,),
        )
        row = cursor.fetchone()
    if not row:
        raise SystemExit(f"源行不存在：先执行 --init-source（tenant={tenant_id}）")
    return load_connection_config(dict(row))


async def _dry_run(args) -> int:
    """四步拉取 + 渲染样例（零写库、零计费）。"""
    from src.tenant_custom.lyjy_employment import fetcher
    from src.tenant_custom.lyjy_employment.service import (
        SYNC_TABLES,
        LyjyEmploymentSyncService,
    )

    connection = _load_source_config(args.tenant_id)
    engine = fetcher.build_engine(connection)
    try:
        service = LyjyEmploymentSyncService()
        plan = await asyncio.to_thread(
            service._fetch_plan, engine, list(SYNC_TABLES), {}, args.limit
        )
    finally:
        engine.dispose()

    print(
        "[fetch] 目录: " + ", ".join(
            f"{t}={len(plan['catalogs'][t])}" for t in plan["tables"]
        ),
        flush=True,
    )
    print(
        "[fetch] 增量: " + ", ".join(
            f"{t}={len(plan['increments'][t])}" for t in plan["tables"]
        )
        + "；删除事件: " + ", ".join(
            f"{t}={len(plan['deletes'][t])}" for t in plan["tables"]
        )
        + f"；到期岗位: {len(plan['expired_job_ids'])}",
        flush=True,
    )
    shown = 0
    for table in plan["tables"]:
        for row in (plan["increments"][table] or [])[:1]:
            rendered = service._render(table, row, plan)
            print(f"\n[render] {rendered.external_id} → {rendered.category_display_name} "
                  f"hash={rendered.content_hash[:12]}… expires_at={rendered.expires_at}",
                  flush=True)
            print(rendered.content_md[:600] + ("\n…" if len(rendered.content_md) > 600 else ""),
                  flush=True)
            shown += 1
            if shown >= 3:
                break
        if shown >= 3:
            break
    print(f"\n[dry-run] 渲染 {shown} 条样例；未写库、未计费", flush=True)
    return 0


async def _run(args) -> int:
    from src.db.database import init_postgres_pool
    from src.tenant_custom.lyjy_employment.service import lyjy_employment_service

    init_postgres_pool()
    result = await lyjy_employment_service.run_now(args.tenant_id, limit=args.limit)
    print(f"[done] {result}", flush=True)
    return 0 if result.get("status") in ("success", "queued") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="聊易就业数据库同步（lyjy_employment 模块）")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--init-source", action="store_true", help="建/更新源行（连接配置加密写入）")
    parser.add_argument("--db-host", default="")
    parser.add_argument("--db-port", type=int, default=3306)
    parser.add_argument("--db-name", default="gt_lyjy")
    parser.add_argument("--db-user", default="")
    parser.add_argument("--db-password", default="", help="缺省交互输入（getpass）")
    parser.add_argument("--dry-run", action="store_true", help="拉取+渲染样例，不写库不计费")
    parser.add_argument("--limit", type=int, default=None, help="每表增量截断 N 条（冒烟；截断 run 不推进水位）")
    args = parser.parse_args()

    if args.init_source:
        return _init_source(args)
    if args.dry_run:
        return asyncio.run(_dry_run(args))
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
