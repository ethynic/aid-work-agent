# Excel 模板填充耗时优化（2026-11-18）

> 触发：聊易生产 trace `tr_runner_2f6d4b579c2f4811813b287e053fbb2d` 单轮 317.5s。

## 1. 耗时结论（生产实测）

| 类别 | 次数 | 合计 | 占比 |
|---|---|---|---|
| Excel 结构分析（内部思考型 LLM） | 2 | 171.1s | 54% |
| 顶层 LLM 调用（13 轮，含思考） | 13 | 119.8s | 38% |
| Excel 内部路由器 LLM | 4 | 22.4s | 7% |
| openpyxl 渲染/读取（重工具容器） | 9 | ~0.9s | <1% |

关键事实：

- `excel_process` 的 79s / 92s 全部来自 `excel_template_ai.analyze_structure` 的思考型 LLM（`_default_llm(enable_thinking=True)`）；重容器渲染段每次 0.1s。
- 同一模板被完整结构分析两次：第一次填充的成品残留模板 B/C 档示例数据，模型 read 回才发现并整表重填。
- 「读取该文件内容检查修改结果」被 `_is_modify_intent` 的"修改"命中，落到 LLM 路由多耗 ~6.7s。

用户裁决：**不做**关闭思考（质量下降不可接受），其余实施。

## 2. 改动

| # | 改动 | 位置 |
|---|---|---|
| 1 | 结构分析缓存：键 = 模板内容 sha256 + data 键签名，Redis TTL 3600s；命中跳过 LLM；Redis 不可用降级直连 | `src/tools/excel/excel_template_ai.py` `analyze_structure_safe`、`src/core/cache_utils.py` `CacheKeys.EXCEL_STRUCTURE` |
| 2 | 未映射数值行提示：渲染后检测「非绑定行 + ≥2 个数值」，返回 `unmapped_numeric_rows` 并在 message 提示；`_merge_results` 透传给 Agent | `excel_template_ai.render_with_structure`、`excel_process_tool._merge_results` |
| 3 | 读取优先规则快路径：指令以读取动词开头、无写入动作/坐标且原会被修改意图阻断时判 `read` | `src/tools/excel/excel_router.py` `_rule_based_route` |
| 4 | 提示词引导一次给全 data（多档位/多区块模板） | `excel_process_tool` 的 `data` 字段描述、`FILL_TEMPLATE_SHAPE_HINT` |

## 3. 预期收益

- 改动 1：同模板重复填充省一次 79~92s（本 case 可直接省 ~90s）。
- 改动 2：省掉「填充 → read 回 → 二次决策」约 21s，多数场景避免整次重填（再省 ~79s）。
- 改动 3：省 ~6s/次读取确认。
- 合计本类会话预计从 ~317s 降到 ~150~200s，模型质量不变。

## 4. 验证

- 新增单测 `tests/unit/tools/test_excel_structure_cache_and_unmapped.py`（24 例）：
  缓存命中/键变化不命中/注入 LLM 不写缓存/Redis 不可用降级/JSON 往返/显式 columns 不入缓存/主动失效；
  未映射行报告、M<K 收缩坐标换算、非数字 title 不崩、不误报；
  路由读快路径与「读取并更新/替换/设置/调整/修正/补全」不降级、名词化短语不掩盖写动作；
  `_merge_results` 透传。
- Excel 相关既有单测全量：284 passed。
- 独立验证智能体两轮以上，共 17 条发现全部处置：
  1. 读快路径写意图绕过（P1）→ `_is_pure_read_instruction` 完整闸门 + 名词化短语白名单；
  2. 未映射提示可能诱导误改模板（P2）→ message 改中性两选一，明确保留选项；
  3. 漏报单数值行/扫描上限（P2）→ 保持 ≥2 数值启发式（文档注明不穷尽），上限提到 5000；
  4. title.row 非数字致填充失败（P2）→ 检测整体 try + 安全转换；
  5. 缓存存了叠加显式 columns 的结构（P2）→ 先存 LLM 原始结构；
  6. 缓存键未走 make_key（P2）→ 改 `redis_client.make_key`，前缀隔离 + clear_all 可清；
  7. 测试覆盖不足（P2）→ 补序列化往返/收缩换算/写意图回归等；
  8. 文案诱导模型编造缺失档位（P2）→ 限定「用户要求的区块」，显式给出「其它区块保留无需补 data」；
  9. 「修改后保存/导出/改正」被短语剥离吞掉（P1）→ 动作词黑名单补齐（保存/另存/导出/生成/新建/覆盖/粘贴/追加/乘以/加粗/改正/修复/回填/合并/拆分/排序/去重）；
  10. 「加粗/粘贴/追加/合并」被 to_md 短路劫持（P1）→ to_md 规则同时排除 `_WRITE_ACTION_TOKENS`；
  11. 检测遍历列数受 `ws.max_column` 病态撑大（P2）→ 列范围按结构跨度+余量封顶 256，改用 values_only 遍历；
  12. 坏结构缓存 1h 无自愈（P2）→ 新增 `invalidate_structure_cache`，本地/远程渲染失败均主动失效，`fill_with_sample` 支持 `use_cache=False`；
  13. ideas.md 登记数字不一致（P2）→ 已同步；
  14. 通用工具文案混入旅游域示例（P2）→ 改为中性表述并明确「不得虚构用户未提供的行或数值」。
  15. 动作词黑名单穷不尽（P1，第三轮）：复制/填充/标红/居中/边框/拆成/对调/冻结/改名/排列等仍被 to_md/read 短路 → **收敛为白名单残差法**：剥离读取动词/只读名词/名词化短语后必须无剩余内容才判纯读，任何剩余一律交 LLM；to_md 只保留「仅附件无指令」默认分支；分块读取显式不短路。
  16. Redis 抖动时缓存失效被跳过（P2）→ `_delete_cached_structure` 不再因不可用提前返回，另加本进程坏结构黑名单 `_BAD_STRUCTURE_KEYS`（重新分析写入时解除）。
  17. data 描述残留旅游域示例（P2）→ 全部中性化（成人人均→人均、合计总价_40人→合计_档位A），同步更新既有断言。

- 第四轮反例抽查（白名单残差法重构后）：指定 24 类写操作 × 2 种前缀 + 自构造 30 条新反例 + 40 条刁钻反例共 **118 条全部走 LLM，零降级**；纯读取表达正常命中规则。
  已知「过度保守」：`核对…`/`审阅…`/`读取表格的行数` 因读取动词或填充词未覆盖会多走一轮 LLM 路由——只增加一次调用，不会降级写操作，符合误判方向安全设计，不修。

## 5. 遗留

- 顶层 LLM 思考开销（119.8s）不优化（用户裁决）。
- Excel 内部路由器对 modify 仍走 LLM（生成具体 operations，规则化风险高）。
- 生产效果需下次部署后在聊易/agent2 观察同模板多轮填充的缓存命中率与耗时。