# 分阶段交互工作流（完整示例）

本文件说明 spec-to-quotation-list 技能「先扫描→确认空间→确认大类→再生成」的分阶段交互流程，
配合 `scripts/discover_structure.py` 与生成器的 `--structure` 约束。

---

## 阶段 1：扫描发现「空间」（第 1 个确认门）

运行：

```bash
python scripts/discover_structure.py --pdf "Design Specs Book.pdf" --json structure_proposal.json
```

脚本输出（节选）：

```
文档：Design Specs Book.pdf  （85 页，类型 spec_book）

【阶段1】候选空间（共 14 个）—— 请用户确认或调整：
  1. The HappyRoom            [空间]  TOC L2 «The HappyRoom» P5         条目38/38
  2. Bathroom Pod             [空间]  TOC L2 «Bathroom Pod» P40
  3. Lobby & Reception        [空间]  TOC L3 «Lobby & Reception» P12
  4. Room Corridor            [空间]  TOC L3 «Room Corridor» P14
  5. Gym                      [空间]  TOC L3 «Gym» P22
  ...
  —— 以下更像「非空间章节」（通常只进 Notes，不生成文件）：
      · Brand Overview        TOC L1 «01 Brand Overview» P6
      · Security             TOC L1 «07 Security» P60
      · Approved Suppliers    TOC L1 «11 Approved Suppliers» P70

【阶段2】候选大类（✓=建议保留 ✗=建议暂不含）：
  ✓ 外立面与室外   文档关键词命中
  ✓ 硬装材料       文档关键词命中
  ✓ 家具软装       文档关键词命中
  ✓ 机电与设备     文档关键词命中
  ✗ 艺术品         文档未明显提及，建议暂不包含
```

**▶ 暂停，把清单交给用户。** 示例对话：

> 用户：空间里把 "Lobby & Reception / Room Corridor / Gym / Pool" 合并成一个 "Public Areas"；
> 大类确认要 外立面与室外 / 硬装材料 / 家具软装 / 机电与设备 这 4 个，不要艺术品。

代理据此记录确认结果（见阶段 2 的 confirmed_structure.json）。

---

## 阶段 2：确认「大类」（第 2 个确认门）+ 落盘确认结构

把用户确认后的空间与大类写成 `confirmed_structure.json`：

```json
{
  "spaces": [
    "HappyRoom", "Bathroom Pod", "Public Areas", "F&B",
    "Façade", "Entrance & Signage", "Landscape & Parking",
    "BOH Areas", "Security", "Voice & Data", "Plant",
    "Standards & Suppliers"
  ],
  "themes": ["外立面与室外", "硬装材料", "家具软装", "机电与设备"],
  "renames": {}
}
```

> 若用户把扫描到的 "Public Areas" 改成了别的名，或在 items.json 里用的还是拆分前的子名，
> 则在 `renames` 里写 `{"Lobby & Reception":"Public Areas", "Room Corridor":"Public Areas", ...}`，
> 生成器会自动合并并重命名。

**▶ 再次暂停，确认大类清单无误后再继续。**

---

## 阶段 3：编/修 items.json

按确认后的空间名与大类名编写 `items.json`（每条目带 `space`/`theme`/`category` 三维度）。
可再跑一次核对矩阵：

```bash
python scripts/discover_structure.py --pdf "Design Specs Book.pdf" --items items.json --json proposal2.json
```

看 `matrix` 是否正好是确认的空间×大类组合。

---

## 阶段 4：生成（受确认结构约束）

```bash
python scripts/generate_quotation_lists.py --pdf "Design Specs Book.pdf" \
       --items items.json --out output/ --structure confirmed_structure.json \
       --issue-date "22 SEP 2026"
```

生成器打印：

```
【动态发现】空间 14 个: [...]
【动态发现】大类 4 个: [...]
【结构约束】仅生成确认的空间(12)×大类(4)；应用重命名 {}；条目 141→141
【动态确定】将生成 17 个文件（仅含有真实产品的 空间×大类 组合）:
   - HappyRoom_硬装材料.xlsx   (5 类 / 38 条)
   - HappyRoom_家具软装.xlsx   (3 类 / 12 条)
   ...
【完成】共 141 条，数量已计算 99 条；文件 17 个。
```

**关键验证点**：文件数 = 确认的空间×大类组合中"含有真实产品"的那些；
若用户删掉的大类（如艺术品）或一个空间在某大类下无真实产品，都不会生成文件。

---

## 常见用户调整与处理

| 用户调整 | 处理方式 |
|---|---|
| 重命名空间/大类 | 直接改 items.json 的 `space`/`theme`，或在 `confirmed_structure.json` 写 `renames` |
| 合并多个子空间为一个 | 把子空间的条目 `space` 统一改成合并后的名；或在 `renames` 映射 |
| 拆分一个大空间为多个 | 把该空间条目按内容重新分配 `space` 为各真实子空间名 |
| 新增文档没有的空间 | 在 `spaces` 加入该名，并补相应条目（无条目则该组合不生成文件） |
| 删除某空间/大类 | 从 `spaces`/`themes` 移除；生成器自动跳过 |
| 文档新增/砍掉空间 | 改 `confirmed_structure.json` 重跑即可，**无需改代码** |

---

## 校验重点（阶段 5）

- 自动：`python scripts/validate.py output/`
- 人工：参数页是否齐全、数量公式是否指向正确参数单元格、合计公式、图片数、体积。
- 如实告知用户：哪些条目数量仍为空、缺什么数据才能算出；哪些大类/空间因无真实产品未生成。
