# 酒店知识库搜索工具 - 开发计划

> 关联设计文档：`docs/subagent/travel-consultant/hotel_search_tool_design.md`

## 任务清单

| # | 任务 | 状态 |
|---|------|------|
| 1 | 新增 `src/tools/knowledge/hotel_search_tool.py`（BaseTool + 名称优先/向量兜底 + price_table 批量查询） | ✅ |
| 2 | `src/core/agent.py`：注册 `HotelSearchTool` + 两处 tenant_id 注入元组加入 `hotel_search` | ✅ |
| 3 | `subagents/travel-consultant/SUBAGENT.md`：新增「酒店与价格查询」小节 + 行为约束第 13 条 | ✅ |
| 4 | 新增设计文档 `docs/subagent/travel-consultant/hotel_search_tool_design.md` | ✅ |
| 5 | 新增本开发计划 `plans/plan-hotel-search-tool.md` | ✅ |
| 6 | 登记 `docs/ideas.md`（含补登景点搜索工具） | ✅ |
| 7 | 新增真实 DB 集成测试 `tests/integration/test_hotel_search_tool.py` 并运行通过 | ✅ |

## 验证记录

- [x] `venv/Scripts/python.exe -m pytest tests/integration/test_hotel_search_tool.py` 通过（8 passed，连 .env 真实 DB）
- [ ] 工具注册核对（启动后 `hotel_search` 在列表 + 旅游顾问继承）
- [ ] 端到端：酒店价格问答命中 `price_table`；行程正文不出现酒店价格
