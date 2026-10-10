#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旅游资源价格/项目表迁入 documents.metadata.raw_payload（统一知识库工具前置迁移）

设计：docs/system/knowledge-base/unified-kb-resource-table-metadata-design.md §4 D1
计划：docs/plans/plan-unified-kb-resource-metadata.md Phase 1

把 hotel_resource / attraction_resource 两类文档的固定 chunk 位次表文本
（酒店 chunk1=价格表；景点 chunk1=门票表、chunk2=项目表）迁入
documents.metadata.raw_payload，并镜像顶层业务键（region/category/images 等），
使统一工具 knowledge_base_search 默认透出（_parse_doc_metadata 白名单）。

用法：
    # 演练（默认 dry-run，只报告不写库）
    python3 scripts/migrate_travel_resource_tables_to_metadata.py --env test
    python3 scripts/migrate_travel_resource_tables_to_metadata.py --env test --tenant-id tenant_xxx

    # 写库
    python3 scripts/migrate_travel_resource_tables_to_metadata.py --env test --execute

    # 校验（只读）：逐文档比对 raw_payload 与 chunk 文本一致性，差异非零则退出码 1
    python3 scripts/migrate_travel_resource_tables_to_metadata.py --env test --verify

    # 清场（Phase 5，需部署授权）：删除 chunks 1/2 并修正 total_chunks
    python3 scripts/migrate_travel_resource_tables_to_metadata.py --env test --drop-chunks --execute

特性：幂等（raw_payload 已有键且一致则跳过，重跑零变更）、单文档事务、
冲突不覆盖（--force 强制）、raw_payload 超 32KB 告警清单（不截断）、
报告写 log/temp/travel_metadata_migrate_{env}.log。

生命周期（重要）：本脚本是一次性运维工具。当正式服务器全量数据迁移完成
（--execute → --verify 通过 → --drop-chunks 清场 → verify 复查通过）后，
本脚本与配套测试（tests/unit/test_migrate_travel_resource_tables.py、
tests/integration/test_migrate_travel_resource_tables_db.py）即完成使命，
可整体删除，不留长期维护负担（见开发计划 Phase 5 收尾清理）。
"""

import argparse
import json
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# 常量与规格
# ---------------------------------------------------------------------------

# 各 source_type 的「chunk 位次 → raw_payload 表键」映射
SPEC = {
    "hotel_resource": {1: "price_table"},
    "attraction_resource": {1: "ticket_table", 2: "project_table"},
}

# 镜像时排除的系统内部键（溯源/管线痕迹，不进 LLM 上下文；
# 两类旅游资源当前顶层均为 parser 业务键，此清单为防御性兜底）
SYSTEM_KEYS = {
    "source_code", "external_id", "native_id", "run_id", "trace",
    "pipeline_version", "ingested_at", "content_md",
}

# raw_payload 序列化软上限（与统一工具 doc_metadata 契约一致）
RAW_PAYLOAD_LIMIT_BYTES = 32 * 1024

LOG_LINES = []


def log(msg):
    LOG_LINES.append(msg)
    print(msg)


# ---------------------------------------------------------------------------
# 纯逻辑（无 DB，单测覆盖）
# ---------------------------------------------------------------------------

def _parse_metadata_dict(raw):
    """documents.metadata 列（TEXT 存 JSON）→ dict；损坏返回 {}"""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def mirror_keys(metadata):
    """顶层业务键镜像（排除 raw_payload 自身与系统键）"""
    return {
        k: v for k, v in metadata.items()
        if k != "raw_payload" and k not in SYSTEM_KEYS
    }


def build_raw_payload(source_type, metadata, chunk_texts):
    """合成目标 raw_payload：表文本（chunk 行存在才含键）+ 顶层业务键镜像。

    chunk_texts: {chunk_index: text}，仅含实际存在的行（可为空 dict）。
    表键不被镜像键覆盖（chunk 文本是表数据的唯一权威来源）。
    """
    payload = {}
    for idx, key in SPEC[source_type].items():
        if idx in chunk_texts:
            payload[key] = chunk_texts[idx]
    for k, v in mirror_keys(metadata).items():
        if k not in payload:
            payload[k] = v
    return payload


def plan_document(source_type, metadata, chunk_texts, force=False):
    """幂等迁移决策。

    Returns:
        {"action": "migrate", "new_metadata": dict, "oversized": bool, "conflicts": []}
        {"action": "ok"}                                  —— 已迁移且一致，无需写
        {"action": "skip_conflict", "conflicts": [键…]}   —— 已有键值不同，未 --force
    """
    raw = metadata.get("raw_payload")
    target = build_raw_payload(source_type, metadata, chunk_texts)

    if raw is None:
        conflicts = []
    elif not isinstance(raw, dict):
        conflicts = ["raw_payload（非对象，疑似损坏）"]
    else:
        conflicts = [k for k, v in target.items() if k in raw and raw[k] != v]

    if conflicts and not force:
        return {"action": "skip_conflict", "conflicts": conflicts}

    if isinstance(raw, dict):
        need_write = any(k not in raw or raw[k] != v for k, v in target.items())
        if not need_write:
            return {"action": "ok"}

    # 合并保留 raw_payload 中已有的未知键（不删已有数据），表键/镜像键以本次合成为准
    base = raw if isinstance(raw, dict) else {}
    merged_payload = {**base, **target}
    oversized = len(json.dumps(merged_payload, ensure_ascii=False).encode("utf-8")) > RAW_PAYLOAD_LIMIT_BYTES
    return {
        "action": "migrate",
        "new_metadata": {**metadata, "raw_payload": merged_payload},
        "oversized": oversized,
        "conflicts": conflicts,
    }


def verify_document(source_type, metadata, chunk_texts):
    """校验 raw_payload 与 chunk 文本/顶层镜像键的一致性（只读）。

    Returns:
        (status, diffs)：status ∈ ok / mismatch / no_source
        - 有表 chunk 行 → 逐键比对，缺键或值不同为 mismatch
        - 无任何表 chunk 行（uuid 导入存量 / --drop-chunks 之后）→
          raw_payload 含表键即 ok，否则 no_source（无可比对数据）
    """
    raw = metadata.get("raw_payload")
    raw = raw if isinstance(raw, dict) else {}

    diffs = []
    compared = 0
    for idx, key in SPEC[source_type].items():
        if idx in chunk_texts:
            compared += 1
            if key not in raw:
                diffs.append(f"raw_payload 缺 {key}（chunk {idx} 有文本）")
            elif raw[key] != chunk_texts[idx]:
                diffs.append(f"raw_payload.{key} 与 chunk {idx} 文本不一致")

    # 镜像键漂移检查（双写一致性，风险表 #5）：双向——值不同、顶层有而 raw 未镜像。
    # 不查反向（raw 有而顶层无）：raw_payload 中保留的未知业务键是设计行为，不误报。
    top = mirror_keys(metadata)
    for k, v in top.items():
        if k not in raw:
            diffs.append(f"顶层键 {k} 未镜像进 raw_payload")
        elif raw[k] != v:
            diffs.append(f"镜像键 {k} 与顶层 metadata 不一致")

    if compared == 0:
        # 无表 chunk 行（uuid 存量 / --drop-chunks 之后）：raw_payload 表键是唯一数据源，
        # 镜像漂移此时最需把关（drop 后顶层更新未同步 raw 即数据丢失），diffs 非空即 mismatch
        has_any = any(k in raw for k in SPEC[source_type].values())
        if diffs:
            return "mismatch", diffs
        return ("ok" if has_any else "no_source"), diffs
    return ("mismatch" if diffs else "ok"), diffs


def plan_drop(source_type, metadata, chunk_texts):
    """--drop-chunks 清场决策（仅当表数据已完整落入 raw_payload 才放行）。

    Returns:
        (should_drop, reason, new_total_chunks)
        new_total_chunks：chunk 0 存在 → 1；整文档无任何 chunk → 0（如实修正）
    """
    raw = metadata.get("raw_payload")
    raw = raw if isinstance(raw, dict) else {}
    table_keys = list(SPEC[source_type].values())
    chunk_rows = [i for i in SPEC[source_type] if i in chunk_texts]

    if not chunk_rows:
        return False, "无表 chunk 行，无需清理", None
    missing = [k for k in table_keys if k not in raw]
    if missing:
        return False, f"raw_payload 缺表键 {missing}，拒绝清场", None
    # 值一致性：键在而值落后（观察期内旧 PUT 只改了 chunk 未改 metadata）时，
    # chunk 是唯一较新数据源，删除不可逆——拒绝并提示先重跑迁移
    stale = [(i, k) for i, k in SPEC[source_type].items()
             if i in chunk_texts and raw.get(k) != chunk_texts[i]]
    if stale:
        return False, f"raw_payload 表键值落后于 chunk（{[k for _, k in stale]}），请先重跑迁移再清场", None
    return True, "", (1 if 0 in chunk_texts else 0)


# ---------------------------------------------------------------------------
# DB I/O（薄封装，单文档事务）
# ---------------------------------------------------------------------------

def _connect():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    import psycopg2
    import psycopg2.extras
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/aid_work_agent")
    from urllib.parse import urlparse
    parsed = urlparse(url)
    conn = psycopg2.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        database=parsed.path.lstrip("/"),
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = False
    return conn


def scan_documents(conn, tenant_id=None):
    """扫描两类资源文档行（按 id 升序）"""
    sql = (
        "SELECT id, tenant_id, source_type, title, total_chunks, metadata "
        "FROM documents WHERE source_type IN ('hotel_resource', 'attraction_resource')"
    )
    params = []
    if tenant_id:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    sql += " ORDER BY id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def load_chunk_texts(conn, doc_id, source_type):
    """读取该文档 chunk 0 + 表位次行 → {chunk_index: text}（text None 归一为 ''）"""
    indexes = [0] + list(SPEC[source_type].keys())
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_index, text FROM chunks WHERE doc_id = %s AND chunk_index = ANY(%s)",
            (doc_id, indexes),
        )
        rows = cur.fetchall()
    return {row["chunk_index"]: (row["text"] or "") for row in rows}


def write_metadata(conn, doc_id, metadata_dict, expected_raw):
    """单文档事务写回 documents.metadata（整列 JSON 替换），带原值乐观锁。

    expected_raw 为 scan 时读到的 metadata 原值（TEXT 原文或 None）；
    执行期间被并发更新（如旧 PUT 接口）则 0 行命中，放弃本次写入
    （避免用 scan 时的旧顶层键整列覆盖丢掉并发更新），重跑迁移即可。

    Returns: True 写入成功 / False 原值已变（并发跳过）
    """
    with conn.cursor() as cur:
        # IS NOT DISTINCT FROM = NULL 安全的相等：仅当 metadata 仍是 scan 时的原值
        # （含双 NULL）才命中。方向不能写反——IS DISTINCT FROM 会变成「已变才写」，
        # 恰好放行要防的丢失更新（CodeReview P0 回归锚点）
        cur.execute(
            "UPDATE documents SET metadata = %s WHERE id = %s AND metadata IS NOT DISTINCT FROM %s",
            (json.dumps(metadata_dict, ensure_ascii=False), doc_id, expected_raw),
        )
        changed = cur.rowcount > 0
    if changed:
        conn.commit()
    else:
        conn.rollback()
    return changed


def drop_chunks_for_doc(conn, doc_id, source_type, new_total_chunks):
    """单文档事务：删除表位次 chunk 行 + 修正 total_chunks"""
    indexes = list(SPEC[source_type].keys())
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE doc_id = %s AND chunk_index = ANY(%s)",
            (doc_id, indexes),
        )
        cur.execute(
            "UPDATE documents SET total_chunks = %s WHERE id = %s",
            (new_total_chunks, doc_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_migrate(conn, tenant_id, execute, force):
    """迁移主流程：dry-run 报告或 --execute 写库"""
    docs = scan_documents(conn, tenant_id)
    stats = {"scanned": 0, "migrated": 0, "ok": 0, "conflict": 0, "oversized": 0, "concurrent": 0}
    oversized_docs = []

    for row in docs:
        stats["scanned"] += 1
        doc_id = row["id"]
        source_type = row["source_type"]
        title = row.get("title") or ""
        metadata = _parse_metadata_dict(row.get("metadata"))
        chunk_texts = load_chunk_texts(conn, doc_id, source_type)

        plan = plan_document(source_type, metadata, chunk_texts, force=force)
        action = plan["action"]

        if action == "skip_conflict":
            stats["conflict"] += 1
            log(f"[{source_type}] doc={doc_id} 「{title}」→ 冲突跳过: {plan['conflicts']}（--force 可覆盖）")
            continue
        if action == "ok":
            stats["ok"] += 1
            continue
        if plan["oversized"]:
            stats["oversized"] += 1
            oversized_docs.append(f"doc={doc_id} 「{title}」")
            log(f"[{source_type}] doc={doc_id} 「{title}」→ ⚠️ 迁移后 raw_payload 超 32KB（照常写入，不截断，需人工处理）")

        table_keys = [SPEC[source_type][i] for i in SPEC[source_type] if i in chunk_texts]
        if execute:
            if write_metadata(conn, doc_id, plan["new_metadata"], row.get("metadata")):
                stats["migrated"] += 1
                log(f"[{source_type}] doc={doc_id} 「{title}」→ 已迁移（{', '.join(table_keys) or '仅镜像键'}）")
            else:
                stats["concurrent"] += 1
                log(f"[{source_type}] doc={doc_id} 「{title}」→ 并发变更跳过（执行期间 metadata 被外部更新，重跑迁移即可）")
        else:
            stats["migrated"] += 1  # dry-run 计入「将迁移」
            log(f"[{source_type}] doc={doc_id} 「{title}」→ 将迁移（{', '.join(table_keys) or '仅镜像键'}）")

    mode = "EXECUTE" if execute else "DRY-RUN"
    log(f"—— 迁移汇总（{mode}）: 扫描 {stats['scanned']} / "
        f"{'已迁移' if execute else '将迁移'} {stats['migrated']} / 幂等跳过 {stats['ok']} / "
        f"冲突 {stats['conflict']} / 超限告警 {stats['oversized']} / 并发跳过 {stats['concurrent']}")
    if oversized_docs:
        log(f"—— 超限清单（人工拆分或精简）: {'; '.join(oversized_docs)}")
    return stats


def run_verify(conn, tenant_id):
    """校验主流程（只读）：不一致则退出码非零"""
    docs = scan_documents(conn, tenant_id)
    stats = {"scanned": 0, "ok": 0, "mismatch": 0, "no_source": 0}

    for row in docs:
        stats["scanned"] += 1
        doc_id = row["id"]
        source_type = row["source_type"]
        title = row.get("title") or ""
        metadata = _parse_metadata_dict(row.get("metadata"))
        chunk_texts = load_chunk_texts(conn, doc_id, source_type)

        status, diffs = verify_document(source_type, metadata, chunk_texts)
        stats[status] = stats.get(status, 0) + 1
        if diffs:
            log(f"[{source_type}] doc={doc_id} 「{title}」→ {status}: {'; '.join(diffs)}")

    log(f"—— 校验汇总: 扫描 {stats['scanned']} / 一致 {stats['ok']} / "
        f"不一致 {stats['mismatch']} / 无 chunk 可比对 {stats['no_source']}")
    return stats


def run_drop_chunks(conn, tenant_id, execute):
    """清场主流程：仅对 raw_payload 表键齐全的文档删除 chunks 1/2"""
    docs = scan_documents(conn, tenant_id)
    stats = {"scanned": 0, "dropped": 0, "skipped": 0, "no_chunks": 0}

    for row in docs:
        stats["scanned"] += 1
        doc_id = row["id"]
        source_type = row["source_type"]
        title = row.get("title") or ""
        metadata = _parse_metadata_dict(row.get("metadata"))
        chunk_texts = load_chunk_texts(conn, doc_id, source_type)

        should_drop, reason, new_total = plan_drop(source_type, metadata, chunk_texts)
        if not should_drop:
            if "无表 chunk 行" in reason:
                stats["no_chunks"] += 1
            else:
                stats["skipped"] += 1
                log(f"[{source_type}] doc={doc_id} 「{title}」→ 跳过: {reason}")
            continue

        if execute:
            drop_chunks_for_doc(conn, doc_id, source_type, new_total)
            log(f"[{source_type}] doc={doc_id} 「{title}」→ 已删除表 chunk 行，total_chunks→{new_total}")
        else:
            log(f"[{source_type}] doc={doc_id} 「{title}」→ 将删除表 chunk 行，total_chunks→{new_total}")
        stats["dropped"] += 1

    mode = "EXECUTE" if execute else "DRY-RUN"
    log(f"—— 清场汇总（{mode}）: 扫描 {stats['scanned']} / "
        f"{'已删除' if execute else '将删除'} {stats['dropped']} / 拒绝 {stats['skipped']} / "
        f"无表 chunk 行 {stats['no_chunks']}")
    return stats


def write_report_file(env):
    """报告留档（验收标准 6）；路径基于脚本位置，不依赖 CWD"""
    if not LOG_LINES:
        return
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "log", "temp")
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"travel_metadata_migrate_{env}.log")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n===== {datetime.now().isoformat()} =====\n")
        f.write("\n".join(LOG_LINES) + "\n")
    print(f"\n报告已写入: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="旅游资源价格/项目表迁入 documents.metadata.raw_payload（幂等，默认 dry-run）")
    parser.add_argument("--env", default="local", help="环境标签（仅用于报告文件名，如 test/agent1）")
    parser.add_argument("--tenant-id", default=None, help="限定租户（documents.tenant_id 原值），默认全量")
    parser.add_argument("--dry-run", action="store_true", help="仅报告不写库（默认行为，无需显式传）")
    parser.add_argument("--execute", action="store_true", help="实际写库（迁移或清场）")
    parser.add_argument("--verify", action="store_true", help="校验模式（只读）：比对 raw_payload 与 chunk 文本")
    parser.add_argument("--force", action="store_true", help="raw_payload 已有键值不同时强制覆盖（默认跳过）")
    parser.add_argument("--drop-chunks", action="store_true",
                        help="清场：删除 chunks 1/2 并修正 total_chunks（需配合 --execute 才实际删除）")
    args = parser.parse_args(argv)

    conn = _connect()
    verify_stats = None
    try:
        if args.verify:
            verify_stats = run_verify(conn, args.tenant_id)
        elif args.drop_chunks:
            run_drop_chunks(conn, args.tenant_id, args.execute)
        else:
            if args.execute and args.force:
                log("注意：--force 将覆盖 raw_payload 中已有差异键，请确认目标环境")
            run_migrate(conn, args.tenant_id, args.execute, args.force)
    except Exception:
        # 中途异常也留档已积累的进度与告警清单（验收标准 6），随后原样上抛
        log(f"—— 异常中断: {sys.exc_info()[1]}")
        write_report_file(args.env)
        raise
    finally:
        try:
            conn.rollback()  # 释放只读路径遗留的事务快照（已提交数据不受影响）
        except Exception:
            pass
        conn.close()
    write_report_file(args.env)
    return 1 if (verify_stats is not None and verify_stats.get("mismatch", 0)) else 0


if __name__ == "__main__":
    sys.exit(main())
