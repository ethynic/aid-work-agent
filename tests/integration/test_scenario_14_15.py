"""
独立运行场景14和15
场景14：从客户角度整体看看，各区域签了多少合同、金额多少、回款情况怎么样
场景15：把合同按金额分个档，10万以下、10到50万、50到100万、100万以上
每个场景跑3次，汇总对比结果
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


async def run_once(requirement: str, tag: str) -> dict:
    """跑一次分析，返回摘要信息"""
    from src.core import master_agent

    analyzer = DataAnalyzer(session_id=f"test_{tag}_{int(time.time())}")
    analysis_id = f"test_{tag}_{int(time.time())}"

    agent = AnalysisAgent(
        llm_gateway=master_agent.llm,
        analyzer=analyzer,
        analysis_id=analysis_id,
        tables_metadata=None,
        tenant_id=None,
    )

    result = await agent.run(requirement)
    return result


def print_summary(scenario_name: str, requirement: str, results: list):
    """打印多次测试对比表"""
    print(f"\n{'='*70}")
    print(f"  {scenario_name}: {requirement}")
    print(f"{'='*70}")
    print(f"| 次序 | 轮次 | tokens | 成功 | 表格 | 图表 |")
    print(f"|------|------|--------|------|------|------|")
    for i, r in enumerate(results, 1):
        usage = r.get("total_usage", {})
        total_tokens = usage.get("total_tokens", "?")
        success = "✅" if r.get("success") else "❌"
        tables = len(r.get("tables", []))
        charts = len(r.get("charts", []))
        print(f"| {i} | {r.get('iterations', '?')} | {total_tokens} | {success} | {tables} | {charts} |")

    # 打印每次的步骤序列
    for i, r in enumerate(results, 1):
        steps = r.get("steps", [])
        seq = " → ".join(s.get("method", "?") for s in steps)
        print(f"\n  第{i}次步骤: {seq}")


async def main():
    scenarios = [
        ("场景15", "把合同按金额分个档：10万以下、10万到50万、50万到100万、100万以上。然后统计每个档位有多少个合同，总金额是多少，用表格和图表展示"),
    ]

    run_count = 3

    for scenario_name, requirement in scenarios:
        results = []
        for i in range(run_count):
            print(f"\n--- {scenario_name} 第{i+1}/{run_count}次 ---")
            result = await run_once(requirement, scenario_name)
            results.append(result)
            usage = result.get("total_usage", {})
            print(f"  轮次={result.get('iterations')}, tokens={usage.get('total_tokens')}")

        print_summary(scenario_name, requirement, results)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
