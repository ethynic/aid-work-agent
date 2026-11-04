# 知识库多轮检索新鲜度代码评审（2026-11-04）

> 范围：[设计](../system/knowledge-base/knowledge-search-freshness-design.md)、[实施计划](../plans/plan-knowledge-search-freshness.md)对应的本次代码改动。评审者只读核对最终工作区，未修改开发代码。
>
> 结论：核心实现与设计一致；发现的 1 项兼容回归已修正并通过定向测试。本地真实模型配合合成知识的四轮验收已通过，真实数据库、渠道和共享授权场景仍待验收。

## 发现

### P2：空 `source_type` 改变共享知识检索范围

`knowledge_base_search` 的 `source_type` 是可选字符串，调用方可以传空字符串。原实现把空字符串交给 `load_shared_ranges()`；该函数以 `if source_type:` 判断，空字符串等同未指定分类，保留所有已授权共享范围。新实现先读取全部共享范围，再以 `isinstance(source_type, str)` 决定是否筛选；空字符串因此把共享范围筛成空集（`src/tools/knowledge/knowledge_base_tool.py:182-183`，对照 `src/knowledge/retriever/tenant_range.py:67`）。在子智能体搜索全部分类时，模型若传 `source_type=""`，原本可命中的共享文档会消失。

最小修正：仅在 `source_type` 为非空字符串时筛选共享范围。用一个空字符串入参的回归用例证明旧语义即可，不需要新增通用参数规范化层。

修正记录：`knowledge_base_tool.py` 仅在非空字符串时筛选共享范围；`test_kb_search_empty_source_type_keeps_authorized_shared_ranges` 验证空字符串仍保留两个已授权共享栏目。运行 `bash -c 'export PATH="/tmp/aid-test-bin:$PATH"; ./scripts/dev_test.sh tests/unit/test_knowledge_base_tool.py tests/unit/test_knowledge_search_freshness.py tests/unit/test_tenant_range.py -p no:cacheprovider -q'`，59 passed，退出码 0。独立测试运行新增用例、相邻授权用例与 `test_tenant_range.py`，27 passed，退出码 0；独立 CR 无阻断。测试有 4 条既有 Pydantic 弃用警告。

## 已核对的实现边界

- 每次调用仍实际检索；裁剪发生在排序及 `top_k` 之后，未引入知识库版本号、版本表、触发器或版本比较。
- chunk 指纹排除检索分数；文档资料独立判同，`raw_payload` 更新时会返回。清单在每次工具执行前从当前 `state.messages` 构造；旧格式或无可信清单时完整返回。
- `matched_count`、`count` 和两个 `already_in_context_*` 字段的工具说明与设计一致。旧授权读取失败时会明确失败；没有费用门禁。
- 新增测试围绕重检、内容变化、旧格式、同轮调用与恢复、授权失败，未发现仅镜像实现或重复证明同一行为的明显冗余测试。

## 验证与限制

开发会话报告：最终定向测试 59 项通过，独立窄范围验证 27 项通过；此前 Runner 相邻链路 110 项及知识可见性相关回归 96 项通过，独立 CodeReview 无阻断项。本评审核对了最终代码 diff、用例及结果交接，没有重复运行同一套测试。

本地进一步使用真实模型和 `KnowledgeBaseTool.execute()`，以进程内合成知识替换检索器与文档读取，观察了首次询价、同词重问、`raw_payload` 更新和旧格式历史四轮；模型每轮调用工具并选用了对应结果。具体计数和回答见[实施计划的本地验收记录](../plans/plan-knowledge-search-freshness.md#2026-11-04-本地多轮验收记录)。这不等于真实向量/全文检索、Web/渠道会话、真实共享授权变更或真机恢复通过；提示词仍不能保证模型每轮必定调用工具。
