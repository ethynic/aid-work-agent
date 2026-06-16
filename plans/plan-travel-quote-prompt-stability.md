# 开发计划：研学报价技能 Prompt 稳定性优化（方案 A）

> **关联设计文档**：[design-travel-quote-prompt-stability.md](../docs/system/design-travel-quote-prompt-stability.md)
> **登记**：[docs/ideas.md](../docs/ideas.md)

## 任务清单

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 1 | 编写设计文档 | ✅ 已完成 | `docs/system/design-travel-quote-prompt-stability.md` |
| 2 | 编写开发计划 | ✅ 已完成 | 本文件 |
| 3 | 登记到 ideas.md | ✅ 已完成 | docs/ideas.md |
| 4 | 改写 itinerary_parser.py 的 prompt 规则 | ✅ 已完成 | 加固用车段、可选项目、POI 归一、activities 切分、营地活动过滤、不同天景区分开 6 处口径 |
| 5 | 实测验证（同输入连续 5 次） | ✅ 已完成 | 用贵州天眼+荔波行程连续 5 次解析，hash 完全一致，**5/5 全部稳定**。原用车费用 53% 方差、门票项漂移问题全部消除 |
| 6 | 重构 generate.py 返回结构（Excel 视图） | ✅ 已完成 | items→rows（中文字段对齐 Excel 表头），新增合计_费用小计/合计_随队老师，删除 region_name/teacher_total，计算逻辑不变 |
| 7 | 更新 SKILL.md 输出示例 + 操作指引 | ✅ 已完成 | 输出格式加字段含义对照表，"收到结果后的操作"改为严格按 rows 字段复述、不要自行换算 |
| 8 | 单元测试验证返回结构 | ✅ 已完成 | 模拟 5 行 items 走 return 重构，字段映射、合计累加、旧字段清除全部正确 |

## 实测结果（2026-06-14）

测试输入：用户提供实际行程（贵州天眼+荔波6天5晚研学，30名学生+4位老师）

```
Run 1: hash=b82535a6, destination=荔波
Run 2: hash=b82535a6, destination=荔波
Run 3: hash=b82535a6, destination=荔波
Run 4: hash=b82535a6, destination=荔波
Run 5: hash=b82535a6, destination=荔波

5 次完全一致: True
```

修复前 vs 修复后：

| 字段 | 修复前 | 修复后 |
|------|--------|--------|
| 用车里程 | 529km vs 810km（差 53%） | 一致：D1/D3/D6 三段 |
| 用车费用 | 79.39 vs 121.53 元/人 | 一致 |
| 门票项 | 第二次多出"水族马尾绣"3540元 | 一致（可选项目被过滤） |
| daily_attractions | 字面切分不一 | 一致（每项一活动、引号去除） |
| hotel_stays | city 带/不带"县"后缀 | 一致（统一不带后缀） |
| destination | "平塘、荔波" vs "贵阳" | 一致：荔波 |


## 改动范围

只改一个文件：

- `src/skills/travel-quote/scripts/itinerary_parser.py`：重写 prompt 的第 2、7、8 条规则。

不动其他文件，不动主流程，不动接口。

## 验收标准

1. 用贵州天眼+荔波 6 天 5 晚行程（用户提供）连续调用 3 次：
   - 三次 `parsed.daily_routes` 段数、起止点完全一致
   - 三次 `parsed.daily_attractions[].activities` 完全一致
   - 三次人均报价差额 ≤ 0.1 元
2. 不引入新的运行时异常（LLM 返回 JSON 仍可正常解析）
