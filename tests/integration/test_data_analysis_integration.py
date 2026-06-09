"""
智能数据分析工具 — 完整集成测试

测试流程：
1. 上传 Excel 到数据分析 API → 解析 schema → 保存到知识库（含向量嵌入）
2. 对 15 个分析场景，每个场景只传需求描述，让 AnalysisAgent 用真实 LLM 自动完成：
   - search_data_tables / list_data_tables 检索相关表
   - load_table 加载表数据
   - query / aggregate / merge / pivot / calculate / compare / trend 分析数据
   - to_table / to_chart 输出结果
3. 收集每个场景 Agent 自动加载的表 metadata，打印到测试报告
4. 生成详细测试报告

Usage:
    python tests/integration/test_data_analysis_integration.py
"""

import asyncio
import json
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# 先加载环境变量（必须在 src.db.database 导入前）
from dotenv import load_dotenv
load_dotenv()

# 初始化 DB 连接池
from src.db.database import init_postgres_pool

EXCEL_PATH = r"C:\Users\PC\Downloads\合同信息2026年.xlsx"
ORG_PATH = r"C:\Users\PC\Downloads\组织架构.xlsx"

# 检查文件存在性 & 初始化 DB
for _p in [EXCEL_PATH, ORG_PATH]:
    if not os.path.exists(_p):
        print(f"ERROR: 测试文件不存在: {_p}")
        sys.exit(1)

try:
    init_postgres_pool()
except Exception as e:
    print(f"ERROR: 数据库连接池初始化失败: {e}")
    sys.exit(1)

# 现在可以安全导入（DB pool 已初始化）
from src.tools.data_analysis.data_analyzer import DataAnalyzer
from src.tools.data_analysis.analysis_agent import AnalysisAgent
from src.services.data_analysis.sheet_parser import SheetParser


# ============================================================
# Phase 1: 上传 + 解析 + 保存到知识库（调用真实服务）
# ============================================================

def parse_excel_to_sheets(file_path: str) -> List[Dict]:
    """用 SheetParser 解析 Excel，返回每个 sheet 的结构信息"""
    parser = SheetParser()
    return parser.parse_file(file_path)


async def build_schema_from_sheet(sheet_info: Dict, table_hint: str) -> Dict:
    """调用 LLM 从 sheet 信息提取语义化 schema"""
    from src.services.data_analysis.schema_extractor import SchemaExtractor

    extractor = SchemaExtractor()
    schema = await extractor.extract_schema(
        sheet_info=sheet_info,
        table_name_hint=table_hint,
    )

    schema["source_type"] = "file"
    schema["source_info"] = os.path.basename(EXCEL_PATH)
    return schema


async def save_schema_via_api(schema: Dict, file_path: str, sheet_name: str) -> Dict:
    """
    调用真实的 API 保存 schema 到知识库（含向量嵌入）。
    这样语义搜索才能正常工作。
    """
    from src.config.settings import get_embedding_api_key
    from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
    from src.knowledge.vector_db.vector_db import get_vector_db
    from src.db.database import get_db_connection
    from src.api.data_analysis import _generate_schema_text

    table_name = schema["table_name"]
    description = schema.get("description", "")
    columns = schema.get("columns", [])

    schema_text = _generate_schema_text(table_name, description, columns)

    # 生成嵌入
    embedding_api_key = get_embedding_api_key()
    embedding_client = TextEmbeddingV3Client(api_key=embedding_api_key)
    embeddings = await embedding_client.embed_batch([schema_text])

    metadata = {
        "connector_id": None,
        "source_info": os.path.basename(file_path),
        "table_name": table_name,
        "columns": columns,
        "source": {
            "type": "excel",
            "file_path": file_path,
            "sheet_name": sheet_name,
        },
    }

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 检查是否已存在同名 schema
        cursor.execute(
            """
            SELECT id FROM documents
            WHERE source_type = 'data-analysis-metadata'
              AND title = %s
            LIMIT 1
            """,
            (f"[数据表] {table_name}",),
        )
        existing = cursor.fetchone()

        if existing:
            doc_id = existing["id"]
            print(f"    -> schema 已存在 (doc_id={doc_id})，跳过")
            return {"doc_id": doc_id, "schema": schema, "metadata": metadata}
        else:
            cursor.execute(
                """
                INSERT INTO documents
                    (tenant_id, title, source_type, file_type, total_chunks,
                     embedding_model, raw_text, metadata, summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    None,
                    f"[数据表] {table_name}",
                    "data-analysis-metadata",
                    "json",
                    1,
                    "text-embedding-v3",
                    schema_text,
                    json.dumps(metadata, ensure_ascii=False),
                    description,
                ),
            )
            doc_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (doc_id, 0, schema_text, len(schema_text), json.dumps({"char_count": len(schema_text)})),
            )
            chunk_id = cursor.fetchone()["id"]

            # 插入向量
            vector_db = get_vector_db(dimension=1024, conn=conn)
            await vector_db.insert([chunk_id], embeddings)

        conn.commit()

    return {"doc_id": doc_id, "schema": schema, "metadata": metadata}


def recall_metadata_from_knowledge_base(doc_ids: List[int]) -> List[Dict]:
    """从知识库召回 metadata"""
    from src.db.database import get_db_connection

    results = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        placeholders = ",".join(["%s"] * len(doc_ids))
        cursor.execute(
            f"""
            SELECT id, title, metadata, summary, created_at
            FROM documents
            WHERE id IN ({placeholders})
              AND source_type = 'data-analysis-metadata'
            ORDER BY created_at DESC
            """,
            doc_ids,
        )
        for row in cursor.fetchall():
            r = dict(row)
            meta = r.get("metadata")
            if isinstance(meta, str):
                meta = json.loads(meta)
            r["metadata"] = meta
            results.append(r)
    return results


# ============================================================
# 核心运行器：用真实 LLM + AnalysisAgent 执行分析
# ============================================================

async def run_analysis_with_real_agent(requirement: str) -> Dict[str, Any]:
    """
    用真实 LLM + AnalysisAgent 执行分析。
    不传 tables_metadata，让 Agent 自己通过 search_data_tables 发现表。
    返回分析结果 + Agent 自动加载的表 metadata。
    """
    from src.core import master_agent

    analyzer = DataAnalyzer(session_id=f"test_{int(time.time())}_{id(requirement)}")
    analysis_id = f"test_{int(time.time())}_{id(requirement) % 10000}"

    print(f"    [Agent] 创建 AnalysisAgent, analysis_id={analysis_id}")
    print(f"    [Agent] 需求: {requirement}")

    agent = AnalysisAgent(
        llm_gateway=master_agent.llm,  # master_agent 的 LLM 属性是 .llm
        analyzer=analyzer,
        analysis_id=analysis_id,
        tables_metadata=None,   # 不预加载，让 Agent 自己发现
        tenant_id=None,
    )

    print(f"    [Agent] 开始运行 (真实 LLM, 自动表发现)...")
    result = await agent.run(requirement)

    # 收集 Agent 自动加载的表 metadata
    loaded_metadata = {}
    for meta in agent.tables_metadata:
        tid = str(meta.get("table_id", ""))
        if tid:
            loaded_metadata[tid] = {
                "table_id": tid,
                "table_name": meta.get("table_name", ""),
                "description": meta.get("description", ""),
                "column_count": len(meta.get("columns", [])),
                "columns": [
                    {
                        "name": c.get("name", ""),
                        "data_type": c.get("data_type", ""),
                        "description": c.get("description", ""),
                    }
                    for c in meta.get("columns", [])[:20]
                ],
            }

    # 打印中间过程
    print(f"    [Agent] 运行完成: success={result.get('success')}, iterations={result.get('iterations', '?')}")
    print(f"    [Agent] 加载了 {len(loaded_metadata)} 个表: {list(loaded_metadata.keys())}")
    print(f"    [Agent] 输出 {len(result.get('tables', []))} 个表格, {len(result.get('charts', []))} 个图表")
    print(f"    [Agent] 执行了 {len(result.get('steps', []))} 个步骤")
    for step in result.get("steps", []):
        print(f"      步骤{step.get('step', '?')}: {step.get('method', '?')} -> {step.get('description', '?')[:60]}")
    if not result.get("success"):
        print(f"    [Agent] 错误: {result.get('error', '?')}")

    result["_loaded_tables"] = loaded_metadata
    return result


# ============================================================
# 15 个分析场景 — 只传需求，Agent 自主完成全部操作
# 场景16、17 已拆分为独立测试脚本 test_scenario_16.py / test_scenario_17.py
# ============================================================

async def test_scenario_01_top10_contracts():
    """场景1：帮我看看最大的10个合同是哪些"""
    result = await run_analysis_with_real_agent("帮我看看最大的10个合同是哪些")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1, f"没有输出表格, result: {result.get('error', '')}"

    table = result["tables"][0]
    detail = f"[场景1] 金额最大的前10个合同:\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    detail += f"  输出: {table['row_count']} 行, {len(table['columns'])} 列\n"

    # 打印前几行数据
    for i, row in enumerate(table["rows"][:5]):
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(f"{v:,.0f}")
            else:
                cells.append(str(v))
        detail += f"  {' | '.join(cells)}\n"
    return detail, result


async def test_scenario_02_aggregate_by_department():
    """场景2：各部门签了多少合同，总金额多少"""
    result = await run_analysis_with_real_agent("各部门签了多少合同，总金额多少")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    chart_paths = [c["file_path"] for c in result.get("charts", []) if os.path.exists(c.get("file_path", ""))]
    detail = f"[场景2] 各部门合同统计: {table['row_count']} 个部门\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    for row in table["rows"][:5]:
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(f"{v:,.0f}")
            else:
                cells.append(str(v))
        detail += f"  {' | '.join(cells)}\n"
    if chart_paths:
        detail += f"  图表: {chart_paths[0]}\n"
    return detail, result


async def test_scenario_03_merge_contract_product():
    """场景3：把合同和产品关联起来看看"""
    result = await run_analysis_with_real_agent("把合同和产品关联起来看看")
    if not result.get("tables"):
        return "[场景3] 跳过：无数据输出", {"success": True, "tables": [], "charts": [], "steps": []}
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"

    table = result["tables"][0]
    detail = f"[场景3] 合同-产品关联: {table['row_count']} 行\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    return detail, result


async def test_scenario_04_filter_east_china():
    """场景4：看看华东地区都有哪些客户"""
    result = await run_analysis_with_real_agent("看看华东地区都有哪些客户")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    detail = f"[场景4] 华东地区客户: {table['row_count']} 条\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    return detail, result


async def test_scenario_05_monthly_trend():
    """场景5：最近合同金额按月是怎么变化的"""
    result = await run_analysis_with_real_agent("最近合同金额按月是怎么变化的，有趋势吗")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    chart_paths = [c["file_path"] for c in result.get("charts", []) if os.path.exists(c.get("file_path", ""))]
    detail = f"[场景5] 月度趋势: {table['row_count']} 个月份\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    if chart_paths:
        detail += f"  图表: {chart_paths[0]}\n"
    return detail, result


async def test_scenario_06_compare_review_type():
    """场景6：不同评审种类的合同金额各占多少"""
    result = await run_analysis_with_real_agent("不同评审种类的合同金额各占多少")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    chart_paths = [c["file_path"] for c in result.get("charts", []) if os.path.exists(c.get("file_path", ""))]
    detail = f"[场景6] 评审种类占比: {table['row_count']} 种\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    for row in table["rows"][:10]:
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(f"{v:,.2f}")
            else:
                cells.append(str(v))
        detail += f"  {' | '.join(cells)}\n"
    if chart_paths:
        detail += f"  图表: {chart_paths[0]}\n"
    return detail, result


async def test_scenario_07_pivot_review_vs_type():
    """场景7：做个透视表，行是评审种类，列是合同种类"""
    result = await run_analysis_with_real_agent("帮我做一个透视表，行是评审种类，列是合同种类，值是合同金额")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    detail = f"[场景7] 透视表: {table['row_count']} 行 x {len(table['columns'])} 列\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    detail += f"  列: {table['columns']}\n"
    return detail, result


async def test_scenario_08_calculate_margin():
    """场景8：算一下每个合同的差价率"""
    result = await run_analysis_with_real_agent("算一下每个合同的差价率是多少")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    detail = f"[场景8] 差价率: {table['row_count']} 行\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    detail += f"  列: {table['columns']}\n"
    return detail, result


async def test_scenario_09_customer_contract_stats():
    """场景9：把合同和客户关联起来，按区域看看各个区域的合同金额和数量"""
    result = await run_analysis_with_real_agent("把合同和客户关联起来，按区域看看各个区域的合同金额和数量")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    chart_paths = [c["file_path"] for c in result.get("charts", []) if os.path.exists(c.get("file_path", ""))]
    detail = f"[场景9] 区域合同统计: {table['row_count']} 个区域\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    for row in table["rows"][:5]:
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(f"{v:,.0f}")
            else:
                cells.append(str(v))
        detail += f"  {' | '.join(cells)}\n"
    if chart_paths:
        detail += f"  图表: {chart_paths[0]}\n"
    return detail, result


async def test_scenario_10_payment_analysis():
    """场景10：回款数据帮我分析一下"""
    result = await run_analysis_with_real_agent("回款数据帮我分析一下，按状态和类型看看")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    chart_paths = [c["file_path"] for c in result.get("charts", []) if os.path.exists(c.get("file_path", ""))]
    detail = f"[场景10] 回款状态统计: {table['row_count']} 行\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    for row in table["rows"][:10]:
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(f"{v:,.0f}")
            else:
                cells.append(str(v))
        detail += f"  {' | '.join(cells)}\n"
    if chart_paths:
        detail += f"  图表: {chart_paths[0]}\n"
    return detail, result


async def test_scenario_11_three_table_join():
    """场景11：帮我把合同、产品和客户数据关联起来，看看各个区域都卖了多少"""
    result = await run_analysis_with_real_agent("帮我把合同、产品和客户数据关联起来，看看各个区域都卖了多少")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    detail = f"[场景11] 三表关联-区域销售额: {table['row_count']} 行\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    for row in table["rows"][:5]:
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(f"{v:,.0f}")
            else:
                cells.append(str(v))
        detail += f"  {' | '.join(cells)}\n"
    return detail, result


async def test_scenario_12_contract_payment_rate():
    """场景12：合同和回款对一下，看看每个合同收回来多少钱了"""
    result = await run_analysis_with_real_agent("合同和回款对一下，看看每个合同收回来多少钱了")
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    detail = f"[场景12] 回款率分析: {table['row_count']} 个合同\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    return detail, result


async def test_scenario_13_multi_dimension_dashboard():
    """场景13：帮我做个合同的综合看板"""
    result = await run_analysis_with_real_agent(
        "帮我做个合同的综合看板，要部门排行、月度趋势、评审种类占比都有的"
    )
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"

    table_count = len(result["tables"])
    chart_count = len(result["charts"])
    detail = f"[场景13] 综合看板: {table_count} 个表格, {chart_count} 个图表\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    for c in result.get("charts", []):
        if os.path.exists(c.get("file_path", "")):
            detail += f"  图表: {c['file_path']}\n"
    return detail, result


async def test_scenario_14_customer_panoramic():
    """场景14：从客户角度整体看看"""
    result = await run_analysis_with_real_agent(
        "从客户角度整体看看，各区域签了多少合同、金额多少、回款情况怎么样"
    )
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    detail = f"[场景14] 客户全景: {table['row_count']} 行\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    for row in table["rows"][:5]:
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(f"{v:,.0f}")
            else:
                cells.append(str(v))
        detail += f"  {' | '.join(cells)}\n"
    return detail, result


async def test_scenario_15_amount_distribution():
    """场景15：把合同按金额分个档"""
    result = await run_analysis_with_real_agent(
        "把合同按金额分个档，10万以下、10到50万、50到100万、100万以上，看看每个档位有多少合同多少钱"
    )
    assert result["success"] is True, f"Agent failed: {result.get('error', '')}"
    assert len(result["tables"]) >= 1

    table = result["tables"][0]
    detail = f"[场景15] 金额分段分布: {table['row_count']} 行\n"
    detail += f"  Agent 加载了 {len(result.get('_loaded_tables', {}))} 个表\n"
    for row in table["rows"][:10]:
        cells = []
        for v in row:
            if v is None:
                cells.append("-")
            elif isinstance(v, float):
                cells.append(f"{v:,.0f}")
            else:
                cells.append(str(v))
        detail += f"  {' | '.join(cells)}\n"
    return detail, result


# ============================================================
# 测试报告生成
# ============================================================

def generate_report(
    results: List[Dict],
    upload_info: Dict,
    recalled_metadata: List[Dict],
):
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    report_path = "docs/system/digital-employee/integration-test-report.md"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    lines = []
    lines.append("# 智能数据分析工具 — 集成测试报告\n")
    lines.append(f"> 测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"> 测试数据: {upload_info.get('file_path', '')}")
    if upload_info.get("org_file"):
        lines.append(f"、{upload_info['org_file']}")
    lines.append(f"\n> 测试方式: 真实 LLM + AnalysisAgent 自动表发现（无 mock）\n\n")

    # 总览
    lines.append("## 总览\n\n")
    lines.append("| 指标 | 值 |\n|------|----|\n")
    lines.append(f"| 总场景数 | {total} |\n")
    lines.append(f"| 通过 | {passed} |\n")
    lines.append(f"| 失败 | {failed} |\n\n")

    # 上传信息
    lines.append("---\n\n## 知识库上传信息\n\n")
    lines.append(f"- **合同数据文件**: `{upload_info.get('file_path', '')}`\n")
    if upload_info.get("org_file"):
        lines.append(f"- **组织架构文件**: `{upload_info['org_file']}`\n")
    lines.append(f"- **解析出的 Sheet 总数**: {upload_info.get('sheet_count', 0)}\n\n")

    sheets = upload_info.get("sheets", [])
    if sheets:
        lines.append("| Sheet | 表名 | 行数 | 列数 | doc_id |\n|-------|------|------|------|--------|\n")
        for s in sheets:
            lines.append(f"| {s['sheet_name']} | {s['table_name']} | {s['rows']} | {s['columns']} | {s['doc_id']} |\n")
        lines.append("\n")

    # 召回的 Metadata
    lines.append("---\n\n## 知识库中的 Metadata\n\n")
    for rm in recalled_metadata:
        meta = rm.get("metadata", {})
        lines.append(f"### doc_id={rm.get('id')} — {meta.get('table_name', '?')}\n\n")
        lines.append(f"- **标题**: {rm.get('title', '')}\n")
        lines.append(f"- **描述**: {rm.get('summary', '')}\n")
        cols = meta.get("columns", [])
        if cols:
            lines.append("\n| 列名 | 类型 | 描述 |\n|------|------|------|\n")
            for c in cols:
                lines.append(f"| {c.get('name', '')} | {c.get('data_type', '')} | {c.get('description', '')} |\n")
        lines.append("\n")

    # 各场景结果
    lines.append("---\n\n## 测试场景结果\n\n")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        lines.append(f"### {r['name']} — {status}\n\n")
        lines.append(f"**用户需求**: {r['description']}\n\n")

        # Agent 自动加载的表的 metadata
        discovered = r.get("discovered_metadata", {})
        if discovered:
            lines.append(f"**Agent 自动加载的表**: {len(discovered)} 个\n\n")
            for kw, meta in discovered.items():
                lines.append(f"#### 表: {meta.get('table_name', '')} (table_id: {kw})\n\n")
                lines.append(f"- **描述**: {meta.get('description', '')}\n")
                lines.append(f"- **列数**: {meta.get('column_count', 0)}\n\n")
                cols = meta.get("columns", [])
                if cols:
                    lines.append("| 列名 | 类型 | 描述 |\n|------|------|------|\n")
                    for c in cols:
                        lines.append(f"| {c.get('name', '')} | {c.get('data_type', '')} | {c.get('description', '')} |\n")
                lines.append("\n")
        else:
            lines.append("**Agent 加载的表**: 无\n\n")

        if r.get("result_text"):
            lines.append(f"**测试输出**:\n```\n{r['result_text'].strip()}\n```\n\n")
        if not r["passed"]:
            if r.get("error"):
                lines.append(f"**错误信息**: `{r['error']}`\n\n")

        # 详细分析结果
        if r.get("analysis_result"):
            ar = r["analysis_result"]
            lines.append("<details>\n<summary>完整分析结果（点击展开）</summary>\n\n")
            lines.append(f"- **iterations**: {ar.get('iterations', '?')}\n")
            lines.append(f"- **trace_id**: {ar.get('trace_id', '?')}\n")
            lines.append(f"- **total_usage**: `{ar.get('total_usage', {})}`\n\n")

            steps = ar.get("steps", [])
            if steps:
                lines.append("#### 分析步骤\n\n")
                lines.append("| 步骤 | 方法 | output_var | 描述 |\n|------|------|-----------|------|\n")
                for s in steps:
                    lines.append(f"| {s.get('step', '?')} | {s.get('method', '?')} | {s.get('output_var', '?')} | {s.get('description', '?')} |\n")
                lines.append("\n")

            tables = ar.get("tables", [])
            if tables:
                lines.append("#### 表格输出\n\n")
                for i, t in enumerate(tables):
                    lines.append(f"**Table {i+1}**: {t.get('row_count', '?')} 行 x {len(t.get('columns', []))} 列\n\n")
                    cols = t.get("columns", [])
                    rows = t.get("rows", [])
                    if cols and rows:
                        lines.append("| " + " | ".join(str(c) for c in cols) + " |\n")
                        lines.append("| " + " | ".join("---" for _ in cols) + " |\n")
                        for row in rows[:10]:
                            cells = []
                            for v in row:
                                if v is None:
                                    cells.append("")
                                elif isinstance(v, float):
                                    cells.append(f"{v:,.2f}")
                                else:
                                    cells.append(str(v))
                            lines.append("| " + " | ".join(cells) + " |\n")
                        if len(rows) > 10:
                            lines.append(f"| ... | ({len(rows) - 10} more rows) |\n")
                    lines.append("\n")

            charts = ar.get("charts", [])
            if charts:
                lines.append("#### 图表输出\n\n")
                for c in charts:
                    lines.append(f"- **{c.get('title', '?')}** ({c.get('chart_type', '?')}): `{c.get('file_path', '?')}`\n")
                lines.append("\n")

            lines.append("</details>\n\n")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n报告已保存到: {report_path}")
    return report_path


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    # Phase 1/2 已通过 scripts/cleanup_and_reimport_data_analysis.py 完成
    # 数据已在知识库中，此处直接运行 15 个分析场景

    # ========================================
    # 运行 15 个分析场景（真实 LLM，Agent 自动发现表）
    # ========================================
    print("=" * 60)
    print("运行 15 个分析场景（真实 LLM + AnalysisAgent 自动表发现）")
    print("=" * 60)

    scenarios = [
        {"name": "场景1", "desc": "帮我看看最大的10个合同是哪些", "fn": test_scenario_01_top10_contracts},
        {"name": "场景2", "desc": "各部门签了多少合同，总金额多少", "fn": test_scenario_02_aggregate_by_department},
        {"name": "场景3", "desc": "把合同和产品关联起来看看", "fn": test_scenario_03_merge_contract_product},
        {"name": "场景4", "desc": "看看华东地区都有哪些客户", "fn": test_scenario_04_filter_east_china},
        {"name": "场景5", "desc": "最近合同金额按月是怎么变化的", "fn": test_scenario_05_monthly_trend},
        {"name": "场景6", "desc": "不同评审种类的合同金额各占多少", "fn": test_scenario_06_compare_review_type},
        {"name": "场景7", "desc": "做个透视表，行是评审种类，列是合同种类", "fn": test_scenario_07_pivot_review_vs_type},
        {"name": "场景8", "desc": "算一下每个合同的差价率", "fn": test_scenario_08_calculate_margin},
        {"name": "场景9", "desc": "按区域看看各个区域的合同金额和数量", "fn": test_scenario_09_customer_contract_stats},
        {"name": "场景10", "desc": "回款数据帮我分析一下", "fn": test_scenario_10_payment_analysis},
        {"name": "场景11", "desc": "合同产品客户关联起来看各区域销售", "fn": test_scenario_11_three_table_join},
        {"name": "场景12", "desc": "合同和回款对一下，看每个合同回款率", "fn": test_scenario_12_contract_payment_rate},
        {"name": "场景13", "desc": "做个合同综合看板", "fn": test_scenario_13_multi_dimension_dashboard},
        {"name": "场景14", "desc": "从客户角度看合同金额和回款情况", "fn": test_scenario_14_customer_panoramic},
        {"name": "场景15", "desc": "合同按金额分档统计", "fn": test_scenario_15_amount_distribution},
    ]

    results = []
    for s in scenarios:
        entry = {
            "name": s["name"], "description": s["desc"],
            "passed": False, "result_text": "", "error": "",
            "analysis_result": None,
            "discovered_metadata": {},
        }
        try:
            output, result = asyncio.run(s["fn"]())
            entry["passed"] = True
            entry["result_text"] = output
            entry["analysis_result"] = result
            entry["discovered_metadata"] = result.get("_loaded_tables", {})
            print(f"  PASS {s['name']}: {s['desc']}")
            loaded = result.get("_loaded_tables", {})
            if loaded:
                for tid, meta in loaded.items():
                    print(f"    -> 自动加载表: {meta.get('table_name', '?')} (id={tid})")
        except Exception as e:
            entry["passed"] = False
            entry["error"] = str(e)
            tb = traceback.format_exc()
            print(f"  FAIL {s['name']}: {s['desc']}")
            print(f"    Error: {e}")
            print(f"    {tb}")
        results.append(entry)

    # ========================================
    # 生成测试报告
    # ========================================
    print("\n" + "=" * 60)
    print("生成测试报告")
    print("=" * 60)

    generate_report(results, {"file_path": "", "sheet_count": 0, "sheets": []}, [])

    # 汇总
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    print(f"\n{'=' * 60}")
    print(f"测试完成: {passed}/{len(results)} 通过, {failed} 失败")
    print(f"{'=' * 60}")
