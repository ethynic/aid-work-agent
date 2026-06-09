"""
单独运行场景16：统计所有四级部门今年的签约额
打印完整的分析结果 JSON（每步处理过程、最终表格、图表路径）
运行完成后自动生成带时间戳的测试报告
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
load_dotenv()

from src.db.database import init_postgres_pool
from src.tools.data_analysis.data_analyzer import DataAnalyzer
from src.tools.data_analysis.analysis_agent import AnalysisAgent

init_postgres_pool()


class _NativeEncoder(json.JSONEncoder):
    """处理 numpy/NaN 等类型的 JSON 序列化"""
    import numpy as np
    def default(self, obj):
        if isinstance(obj, self.np.integer):
            return int(obj)
        if isinstance(obj, self.np.floating):
            v = float(obj)
            if self.np.isnan(v) or self.np.isinf(v):
                return None
            return v
        if isinstance(obj, self.np.ndarray):
            return obj.tolist()
        if isinstance(obj, self.np.bool_):
            return bool(obj)
        if isinstance(obj, (self.np.datetime64, self.np.timedelta64)):
            return str(obj)
        return super().default(obj)


def generate_report(requirement: str, result: dict, report_dir: str):
    """生成带时间戳的测试报告"""
    os.makedirs(report_dir, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"scenario_16_report_{ts}.md")

    lines = []
    lines.append("# 场景16 测试报告\n\n")
    lines.append(f"> 测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    # 总览
    success = result.get("success", False)
    status = "PASS" if success else "FAIL"
    lines.append("## 总览\n\n")
    lines.append(f"| 指标 | 值 |\n|------|----|\n")
    lines.append(f"| 状态 | {status} |\n")
    lines.append(f"| 用户需求 | {requirement} |\n")
    lines.append(f"| iterations | {result.get('iterations', '?')} |\n")
    lines.append(f"| steps | {len(result.get('steps', []))} |\n")

    usage = result.get("total_usage", {})
    lines.append(f"| prompt_tokens | {usage.get('prompt_tokens', '?')} |\n")
    lines.append(f"| completion_tokens | {usage.get('completion_tokens', '?')} |\n")
    lines.append(f"| total_tokens | {usage.get('total_tokens', '?')} |\n")
    lines.append(f"| 表格输出 | {len(result.get('tables', []))} 个 |\n")
    lines.append(f"| 图表输出 | {len(result.get('charts', []))} 个 |\n\n")

    # 总结
    summary = result.get("summary", "")
    if summary:
        lines.append("## 分析总结\n\n")
        lines.append(f"{summary}\n\n")

    # 分析步骤
    steps = result.get("steps", [])
    if steps:
        lines.append("## 分析步骤\n\n")
        lines.append("| 步骤 | 方法 | output_var | 描述 |\n|------|------|-----------|------|\n")
        for s in steps:
            lines.append(f"| {s.get('step', '?')} | {s.get('method', '?')} | {s.get('output_var', '?')} | {s.get('description', '?')} |\n")
        lines.append("\n")

    # 表格输出
    tables = result.get("tables", [])
    if tables:
        lines.append("## 表格输出\n\n")
        for i, t in enumerate(tables):
            lines.append(f"### Table {i+1}: {t.get('row_count', '?')} 行 x {len(t.get('columns', []))} 列\n\n")
            cols = t.get("columns", [])
            rows = t.get("rows", [])
            if cols and rows:
                lines.append("| " + " | ".join(str(c) for c in cols) + " |\n")
                lines.append("| " + " | ".join("---" for _ in cols) + " |\n")
                for row in rows:
                    cells = []
                    for v in row:
                        if v is None:
                            cells.append("")
                        elif isinstance(v, float):
                            cells.append(f"{v:,.2f}")
                        else:
                            cells.append(str(v))
                    lines.append("| " + " | ".join(cells) + " |\n")
                if t.get("total_count") and t["total_count"] > len(rows):
                    lines.append(f"| ... | ({t['total_count'] - len(rows)} more rows) |\n")
            lines.append("\n")

    # 图表输出
    charts = result.get("charts", [])
    if charts:
        lines.append("## 图表输出\n\n")
        for c in charts:
            lines.append(f"- **{c.get('title', '?')}** ({c.get('chart_type', '?')}): `{c.get('file_path', '?')}`\n")
        lines.append("\n")

    # 中间文件
    intermediate = result.get("intermediate_files", [])
    if intermediate:
        lines.append("## 中间文件\n\n")
        lines.append("| 变量名 | 方法 | 行数 | 描述 |\n|--------|------|------|------|\n")
        for f in intermediate:
            lines.append(f"| {f.get('output_var', '?')} | {f.get('method', '?')} | {f.get('rows', '?')} | {f.get('description', '?')} |\n")
        lines.append("\n")

    # 完整 JSON（折叠）
    lines.append("<details>\n<summary>完整 JSON 结果（点击展开）</summary>\n\n")
    lines.append("```json\n")
    lines.append(json.dumps(result, ensure_ascii=False, indent=2, cls=_NativeEncoder))
    lines.append("\n```\n\n</details>\n")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n测试报告已保存到: {report_path}")
    return report_path


async def main():
    from src.core import master_agent

    requirement = "统计所有四级部门今年的签约额是多少"

    analyzer = DataAnalyzer(session_id=f"test_s16_{int(time.time())}")
    analysis_id = f"test_s16_{int(time.time())}"

    print("=" * 60)
    print(f"场景16: {requirement}")
    print(f"analysis_id: {analysis_id}")
    print("=" * 60)

    agent = AnalysisAgent(
        llm_gateway=master_agent.llm,
        analyzer=analyzer,
        analysis_id=analysis_id,
        tables_metadata=None,
        tenant_id=None,
    )

    result = await agent.run(requirement)

    # 打印完整结果 JSON
    print("\n" + "=" * 60)
    print("完整分析结果 JSON:")
    print("=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2, cls=_NativeEncoder))

    # 生成测试报告
    report_dir = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "test-reports")
    generate_report(requirement, result, report_dir)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
