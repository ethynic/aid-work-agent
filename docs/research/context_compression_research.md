# 会话内上下文压缩 — 业界方案调研报告

> 创建：2026-06-23
> 关联设计：[context_compression_design.md](../infrastructure/memory/context_compression_design.md)
> 调研方法：WebSearch + WebFetch，覆盖 LangChain/LangGraph、Claude Code、OpenAI Assistants、MemGPT/Letta、Strands、Microsoft Agent Framework、Cline、学术软压缩方案

---

## 一、核心策略分类

业界对「上下文膨胀」问题已形成相对收敛的 6 类策略，复杂系统往往是多种策略的组合。

**1. 滑动窗口截断（Sliding Window）**
最朴素的方案，只保留最近 N 条消息或 N 个 token，老消息直接丢弃。LangChain `ConversationBufferWindowMemory`、OpenAI Assistants API 的默认 `truncation_strategy: auto` 都是这一类。优点是零成本、可预测；缺点是「有损丢失」，跨多轮的语义信息（如用户偏好、早期约束）会突然消失。适合短会话或纯无状态问答场景。

**2. 全量摘要记忆（Summary Memory）**
用一个 LLM 调用把整段对话不断「再摘要」成一段运行中的总结（running summary），每次新交互后更新。代表是 LangChain `ConversationSummaryMemory`。优点是能跨越长距离保留要点；缺点是短会话反而比原文更费 token，且每次全量重摘要成本高、容易丢细节。

**3. 摘要+缓冲混合（Summary Buffer）**
LangChain `ConversationSummaryBufferMemory` 的核心思路：设定一个 token 阈值，近期消息保留原文（buffer），超阈值的老消息**滚动**合并进 summary。这是目前业界被复用最多的「工程化默认方案」，因为它同时兼顾近期细节和远期语义。Claude Code 的 auto-compact、LangGraph 的 summarization 节点都是这一家族。

**4. Token 预算缓冲（Token Buffer）**
LangChain `ConversationTokenBufferMemory`：按 token 而不是消息条数控制缓冲区，更精确匹配模型上限。区别于摘要方案，它不调用 LLM，纯靠丢弃老消息维持预算，工程实现最简单。

**5. 分层记忆 / 虚拟内存（Tiered Memory）**
MemGPT/Letta 提出的操作系统隐喻：把上下文分成 **main context（在窗口内，相当于 RAM）**、**recall memory（可搜索的对话档档）**、**archival memory（向量化的长期存储，相当于磁盘）**。关键创新是 **self-editing memory**——Agent 通过 `memory_insert`/`memory_replace` 工具**主动**改写自己的记忆，而不是被动 RAG 检索。适合超长周期、需要跨会话沉淀人格/偏好的常驻 Agent。

**6. LLM 摘要压缩 / Prompt 软压缩（Soft Compression）**
两个子方向：
- **LLM 摘要压缩**：让大模型把旧上下文「rewrite」成更紧凑的表述。Anthropic 的 `/compact`、LangGraph 的 summarize node 都属此类。Claude Code 还引入了 **microcompact**——不一次大压缩，而是在每轮对话后悄悄把过长的工具结果、thinking block 局部压缩，推迟全量 compact 的触发时机。
- **Prompt token 软压缩**：以 LLMLingua / LongLLMLingua / LLMLingua-2 为代表，用小模型（BERT 级别）按 token 重要度过滤，宣称 20× 压缩比几乎无损。属于「无语义改写的字面压缩」，可以和摘要方案叠加。

**7. 检索式记忆（Retrieval-Augmented Memory）**
把消息写入向量库，每轮只把 top-k 相关片段注入上下文。LangChain `VectorStoreRetrieverMemory`、MemGPT archival memory 都属此类。问题是「相关性」本身就难判断，且会丢掉时序连贯性，单独使用效果不稳定，通常作为分层记忆的一环。

---

## 二、关键设计决策对比

**压缩触发**
主流是 **token 阈值**（更可靠）而不是消息条数。Claude Code 触发于约 95% 上下文容量（且阈值在不断下调，从 45K 缓冲降到 33K，留更多工作内存）；OpenAI Assistants 触发于「超过模型上下文窗口」时；LangGraph 需要开发者自己挂一个 summarization 节点，由开发者决定阈值。少数系统（如 Claude Code 的 `/compact`）同时支持显式调用。

**同步 vs 异步**
绝大多数生产系统选择**同步**——压缩作为对话流的一个节点，完成后再继续 LLM 调用。原因是异步压缩会引入「压缩未完成时用户已发新消息」的竞态。MemGPT 的 self-edit 是异步风格的（Agent 主动调工具），但单次工具调用本身仍是同步阻塞。

**工具消息处理**
这是企业 Agent 最关键的细节，业界已收敛到几个原则：
- **保留工具调用结构**（tool name + arguments），因为 OpenAI/Anthropic 的消息规范要求 `tool_call` 后必须紧跟 `tool_result`，破坏结构会直接报错。
- **截断或摘要工具结果**：Strands SDK 提供「可配置的工具结果截断」，超过阈值时截断老工具结果但保留最近 N 条原文；LangGraph 的官方教程里把工具结果也作为可移除消息纳入 summary；Cline 社区（issue #4389）讨论过对大文件读取工具结果做硬截断。
- **大输出工具特殊处理**：file_read、search 这类动辄数万 token 的工具结果，常见做法是「只保留摘要 + 引用」，原文留在外部存储供 Agent 再次查询（即「文件系统抽象」模式，LangChain Deep Agents SDK 强调这一点）。

**压缩后存储位置**
四种主流：
- **替换原消息**（OpenAI Assistants 的 auto 截断直接丢弃，不可恢复）。
- **新建 summary 消息插在开头**（LangGraph `SummaryMessage` + `RemoveMessage` 组合，可保留可追溯）。
- **独立 metadata 字段**（如 `chat_sessions.summary` 字段，业务层注入 system prompt）。
- **外部表 + 引用 ID**（MemGPT archival、LangChain filesystem abstraction）。

生产环境明显偏好**可追溯方案**——LangGraph 刻意把 `RemoveMessage` 设计成 checkpointer 级别的状态修改，就是为了避免直接删数据库导致无法回滚。

**用户感知**
分两派。C 端聊天产品（ChatGPT、Claude.ai）倾向**透明显示**——会提示「已压缩早期对话」或显示「Context left until auto-compact: 0%」；B 端 API 框架（LangChain、OpenAI Assistants）倾向**完全透明**，由开发者决定是否暴露给最终用户。Claude Code 的 microcompact 是典型的「对用户隐式」——在用户察觉不到的情况下局部清理。

**摘要质量保障**
- **一次全量摘要**：简单但随对话变长会越来越慢，且摘要的摘要会加速信息损失。
- **增量摘要**：只把「新滚出窗口的消息」合并进现有 summary，主流生产方案几乎都用这个。
- **结构化提取**：把 summary 拆成「用户事实」「已做决策」「待办事项」等字段（CompAct 论文、Microsoft Agent Framework 的 compaction 都强调结构化提取，避免叙事式摘要的语义漂移）。

---

## 三、推荐方案要素（针对企业 Agent）

不写实现，只列**应该具备的机制**：

1. **token 预算 + 百分比双触发**：以 token 为主，配合「上下文剩余 X% 时触发」的缓冲，避免临界抖动；阈值应可配置（不同模型、不同租户可调）。
2. **分层保留**：最近 N 轮原文不动；中间层走增量摘要；摘要之外的历史归档到外部表，可二次检索。
3. **工具消息差异化处理**：工具名+参数全保留；工具结果按类型策略——大输出工具（文件、搜索、报表）硬截断或转存外部 + 引用；状态类工具结果（如已读邮件列表）可整体摘要。
4. **结构化 summary 字段**：避免一段流水账，按「用户画像/已确认事实/已完成任务/待办/关键决策」分槽位存储，便于审计和检索。
5. **压缩元数据持久化**：每条 summary 应记录「由哪些消息合并而来」「合并时间」「原消息是否仍可恢复」，写入数据库独立表。
6. **压缩可回滚**：原始消息不物理删除，只标记 `compacted=true`，需要时可回放。
7. **压缩失败降级**：LLM 摘要调用失败时降级为「截断保留首尾 N 条」+告警，不能阻塞对话。
8. **可观测指标**：压缩前后 token 数、压缩比、摘要耗时、压缩失败率、压缩后用户是否触发「重复问老问题」（这是衡量摘要质量的反向信号）。
9. **显式 API 入口**：除自动触发外，提供「手动 compact」「查看当前上下文构成」「回滚到某次压缩前」三个接口，方便运维和调试。
10. **租户/会话级配置**：不同业务场景（客服 vs 数据分析）对摘要颗粒度需求不同，策略应可按 subagent 或租户覆盖。

---

## 四、参考链接

**LangChain / LangGraph**
- [LangChain Memory 概念文档](https://docs.langchain.com/oss/javascript/concepts/memory)
- [ConversationSummaryBufferMemory 官方文档](https://langchain-doc.readthedocs.io/en/latest/modules/memory/types/summary_buffer.html)
- [ConversationTokenBufferMemory 官方文档](https://reference.langchain.com/python/langchain-classic/memory/token_buffer/ConversationTokenBufferMemory)
- [LangChain Context Engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)
- [LangChain Deep Agents Context Management](https://www.langchain.com/blog/context-management-for-deepagents)
- [Pinecone: Conversational Memory in LangChain 对比](https://www.pinecone.io/learn/series/langchain/langchain-conversational-memory/)

**Claude / Anthropic**
- [Claude API Compaction 官方文档](https://platform.claude.com/docs/en/build-with-claude/compaction)
- [Claude Cookbook: Automatic Context Compaction](https://platform.claude.com/cookbook/tool-use-automatic-context-compaction)
- [ClaudeFast: Claude Code 33K-45K Token Buffer 分析](https://claudefa.st/blog/guide/mechanics/context-buffer-management)
- [Hyperdev: How Claude Code Got Better by Protecting More Context](https://hyperdev.matsuoka.com/p/how-claude-code-got-better-by-protecting)

**OpenAI**
- [Azure OpenAI Assistants API truncation_strategy（微软文档）](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/concepts/assistants)
- [OpenAI 社区：truncation 策略讨论](https://community.openai.com/t/add-smarter-controls-to-truncate-thread-chat-history-assistant-api-runs-api/844919)

**MemGPT / Letta**
- [MemGPT/Letta 三层记忆 Notebook 教程](https://github.com/NirDiamant/Agent_Memory_Techniques/blob/main/all_techniques/26_letta_memgpt_patterns/letta_memgpt_patterns.ipynb)
- [Virtual Context Management with MemGPT and Letta](https://www.leoniemonigatti.com/blog/memgpt.html)

**其他框架**
- [Strands Agents: 可配置工具结果截断](https://strandsagents.com/docs/user-guide/concepts/agents/conversation-management/)
- [Microsoft Agent Framework: Compaction](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/compaction)
- [Cline Issue #4389: Context Window Management](https://github.com/cline/cline/issues/4389)

**学术 / 软压缩**
- [LLMLingua Series 项目主页（微软研究院）](https://www.microsoft.com/en-us/research/project/llmlingua/)
- [microsoft/LLMLingua GitHub](https://github.com/microsoft/LLMLingua)
- [LongLLMLingua 论文（arXiv）](https://arxiv.org/html/2310.06839v2)
- [Automatic Context Compression in LLM Agents（综述）](https://medium.com/the-ai-forum/automatic-context-compression-in-llm-agents-why-agents-need-to-forget-and-how-to-help-them-do-it-43bff14c341d)

---

## 核心结论

业界对长会话压缩的共识已经从「单一策略」进化到「Summary Buffer + 结构化摘要 + 工具结果差异化处理 + 可回滚归档」的组合方案。Claude Code 的 microcompact、MemGPT 的分层记忆、LangGraph 的 summarize 节点都指向同一个方向——**把上下文管理当作 Agent 的一等公民设计，而不是事后补救**。

企业 Agent 应重点借鉴：
- LangGraph 的「summary message + 原消息可追溯」模式
- Strands 的「工具结果可配置截断」细节
- CompAct 论文的结构化摘要思路
