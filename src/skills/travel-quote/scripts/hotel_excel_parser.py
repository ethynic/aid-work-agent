# -*- coding: utf-8 -*-
"""
酒店 Excel 非结构化价格文本 LLM 逐 Sheet 解析器

将酒店 Excel 中各 Sheet 的原始内容直接传给 LLM，
由 LLM 负责理解表格结构（左右并列、上下分段等）并提取酒店和价格信息。

设计思路：酒店 Excel 的 Sheet 结构高度非结构化（左右并列双表、上下多段、
合并单元格、列名不统一等），按行结构化解析不可靠。直接把 Sheet 原始文本
交给 LLM 解析是更稳健的方案。
"""

import json
from typing import Any, Dict, List

from loguru import logger

# 解析 Prompt（单 Sheet 模式）
PARSE_PROMPT = """你是一个酒店价格数据解析助手。以下是从 Excel 中一个 Sheet 页读取的原始数据。

## 输入数据

Sheet 名称：{sheet_name}

原始数据（按行显示，每列用 | 分隔）：

{sheet_raw_text}

## 分析要求

这个 Sheet 可能包含以下任意结构：
- 标准表格（每行一个酒店，列含名称、区域、价格等）
- 左右并列的多个表格（用空列分隔）
- 上下分段的多个区域（用空行或标题行分隔）
- 混合结构（部分是酒店汇总，部分是具体房型报价）

请仔细分析数据的实际结构，从中提取出所有**独立的酒店**及其价格信息。

## 输出要求

对每个提取出的酒店，输出两部分：

### 第1部分：酒店信息摘要
酒店名称：xxx
所在区域：贵州省 xx市 xx区
星级等级：x钻
地址：xxx
主要房型：房型A、房型B（从价格文本中提取）
房间总数：xx间（如无数据则不输出此行）
价格区间：xxx-xxx元/间（从所有月份中提取最低和最高价格）
是否含早：含双早/部分含早/不含早/未知
特殊说明：团队团散同价/执行16免1/司陪房xx元 等

### 第2部分：价格明细表
包含所有房型的价格信息，一行一个价格：
房型 | 客户类型 | 价格 | 含早 | 适用日期

规则：
1. 所有模糊日期（如"五月"、"7-8月"）转换为具体日期范围（如"5月1日-5月31日"）
2. 节假日单独列出行（五一、国庆、端午等）
3. "团散+20"表示团散价 = 团队价 + 20，请直接计算结果
4. 司陪房、免房政策等特殊规则附在价格表末尾，以"特殊政策："开头
5. 如果某月份列无数据，则跳过该月份
6. "门市价8.5折"等无法精确计算的保留原文

## 输出格式

请严格按以下 JSON 格式输出：

```json
[
  {{
    "hotel_name": "酒店名称",
    "region": "区域（从sheet名和市内区域综合）",
    "info_text": "酒店信息摘要文本（多行，一行一字段，用\\n分隔）",
    "price_table_text": "价格明细表文本（一行一个价格）",
    "metadata": {{
      "sub_region": "子区域",
      "diamond_level": "4钻",
      "address": "地址",
      "source_sheet": "{sheet_name}"
    }}
  }}
]
```

注意：只输出 JSON 数组，不要输出其他内容。如果数据中没有有效的酒店信息，输出空数组 []。

## 精简规则（重要！）

输出必须尽量精简，控制在合理长度内：
1. **相同房型+相同客户类型+相同价格+相同含早的连续月份必须合并为一行**，如"5月1日-10月31日"而非每月单独一行。例如：
   - ✅ 高级城景房型 | 团队 | 450 | 未知 | 5月1日-6月30日
   - ❌ 高级城景房型 | 团队 | 450 | 未知 | 5月1日-5月31日
   - ❌ 高级城景房型 | 团队 | 450 | 未知 | 6月1日-6月30日
2. **团散与团队价格相同时合并**：如果某房型团散价=团队价，只输出一行，客户类型标为"团队/团散"
3. 省略值为"未知"或空的字段
4. 只保留关键价格信息，去掉重复描述
5. 非连续月份但价格相同的也应尝试合并，如「5月、9月」标为「5月1日-5月31日、9月1日-9月30日」
"""


def sheet_to_raw_text(file_path: str, sheet_name: str, max_rows: int = 300, max_cols: int = 20) -> str:
    """
    直接用 openpyxl 读取 Sheet 原始单元格内容，转为文本。

    不依赖 read_all_sheets 的 headers→rows 映射（那个假设第一行是表头，会丢失数据）。
    直接按行列坐标读取，原样呈现给 LLM。
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    ws = wb[sheet_name]

    lines = []
    row_count = 0
    for row in ws.iter_rows(values_only=True):
        row_count += 1
        if row_count > max_rows:
            lines.append(f"... (超出 {max_rows} 行限制，已截断)")
            break
        vals = []
        for i, v in enumerate(row):
            if i >= max_cols:
                break
            v = str(v).strip() if v is not None else ""
            if v == "nan":
                v = ""
            vals.append(v)
        lines.append(" | ".join(vals))

    wb.close()
    return "\n".join(lines)


class HotelExcelParser:
    """酒店 Excel 非结构化价格文本 LLM 逐 Sheet 解析器"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        return self._gateway

    async def parse_sheet_by_name(self, file_path: str, sheet_name: str) -> List[Dict]:
        """读取指定 Sheet 的原始内容并解析。"""
        raw_text = sheet_to_raw_text(file_path, sheet_name)
        return await self.parse_sheet(sheet_name, raw_text)

    async def parse_sheet(self, sheet_name: str, raw_text: str) -> List[Dict]:
        """
        用 LLM 解析单个 Sheet 的原始文本。

        Returns:
            [{ hotel_name, region, info_text, price_table_text, metadata }, ...]
        """
        if not raw_text or len(raw_text.strip()) < 20:
            return []

        prompt = PARSE_PROMPT.format(sheet_name=sheet_name, sheet_raw_text=raw_text)

        gateway = self._get_gateway()
        try:
            # DeepSeek V4 thinking 模型：reasoning_tokens 和 output_tokens 共享 max_tokens 配额
            # 思考过程可能消耗大量 token，需要给实际输出留足空间
            response = await gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=65536,
            )

            content = response.get("content", "")
            if not content:
                logger.warning(f"[HotelExcelParser] Sheet '{sheet_name}' LLM 返回空内容, finish_reason={response.get('finish_reason')}")
                return []

            return self._parse_llm_output(content, sheet_name)

        except Exception as e:
            logger.error(f"[HotelExcelParser] Sheet '{sheet_name}' LLM 调用失败: {e}", exc_info=True)
            return []

    def _parse_llm_output(self, content: str, sheet_name: str) -> List[Dict]:
        """解析 LLM 输出的 JSON"""
        json_str = content.strip()

        if "```json" in json_str:
            json_str = json_str.split("```json", 1)[1]
            json_str = json_str.rsplit("```", 1)[0]
        elif "```" in json_str:
            json_str = json_str.split("```", 1)[1]
            json_str = json_str.rsplit("```", 1)[0]

        json_str = json_str.strip()

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"[HotelExcelParser] Sheet '{sheet_name}' JSON 解析失败: {e}, 内容前200字: {json_str[:200]}")
            return []

        if not isinstance(parsed, list):
            logger.warning(f"[HotelExcelParser] Sheet '{sheet_name}' LLM 输出不是数组: {type(parsed)}")
            return []

        results = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            hotel_name = item.get("hotel_name", "").strip()
            if not hotel_name:
                continue

            info_text = item.get("info_text", "").strip()
            price_table = item.get("price_table_text", "").strip()

            if not info_text and not price_table:
                continue

            # 确保 info_text 含"酒店名称：xxx"行。
            # info_text 是唯一被向量化（chunk0 embedding）和 ILIKE 名称检索的字段，
            # 酒店名只进 documents.title 时不参与检索。LLM 偶尔会漏输出该行，
            # 此时按酒店名搜索既命中不了名称也匹配不上语义，故强制以"酒店名称：{hotel_name}"开头。
            if not any(line.strip().startswith("酒店名称") for line in info_text.splitlines()):
                info_text = f"酒店名称：{hotel_name}\n{info_text}"

            region = item.get("region", "").strip()
            metadata = item.get("metadata", {}) or {}
            metadata["source_sheet"] = sheet_name

            results.append({
                "hotel_name": hotel_name,
                "region": region,
                "info_text": info_text,
                "price_table_text": price_table,
                "metadata": metadata,
            })

        return results

    def _fallback_sheet(self, sheet_name: str, raw_text: str) -> List[Dict]:
        """LLM 解析失败时的降级处理"""
        return [{
            "hotel_name": sheet_name.strip(),
            "region": sheet_name.replace("酒店", "").replace("汇总", ""),
            "info_text": raw_text[:1000] if raw_text else f"酒店名称：{sheet_name}",
            "price_table_text": "",
            "metadata": {
                "source_sheet": sheet_name,
                "fallback": True,
            },
        }]

    async def parse_excel_sheets(self, file_path: str) -> List[Dict]:
        """
        处理 Excel 文件：逐 Sheet 读取原始内容，传给 LLM 解析。

        直接用 openpyxl 读原始单元格，不经过 read_all_sheets（那个
        假设第一行是表头，对非结构化 Sheet 会丢失数据）。

        每个有数据的 Sheet 独立作为一个 LLM 调用单元。
        """
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        all_parsed = []
        sheet_names = [sn for sn in wb.sheetnames if not sn.startswith("WpsReserved")]

        # 先扫描各 Sheet 的有效数据量
        valid_sheets = []
        for sn in sheet_names:
            ws = wb[sn]
            non_empty_rows = 0
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    non_empty_rows += 1
            if non_empty_rows >= 2:
                valid_sheets.append(sn)
        wb.close()

        logger.info(f"[HotelExcelParser] 共 {len(valid_sheets)} 个有效 Sheet，开始逐个解析")

        for idx, sheet_name in enumerate(valid_sheets, 1):
            raw_text = sheet_to_raw_text(file_path, sheet_name)

            if not raw_text or len(raw_text.strip()) < 20:
                continue

            logger.info(f"[HotelExcelParser] 解析 Sheet {idx}/{len(valid_sheets)}: '{sheet_name}' ({len(raw_text)} 字符)...")

            try:
                parsed = await self.parse_sheet(sheet_name, raw_text)
                if parsed:
                    all_parsed.extend(parsed)
                    logger.info(f"[HotelExcelParser] Sheet '{sheet_name}' 解析完成，得到 {len(parsed)} 家酒店")
                else:
                    logger.info(f"[HotelExcelParser] Sheet '{sheet_name}' 无有效酒店数据")
            except Exception as e:
                logger.error(f"[HotelExcelParser] Sheet '{sheet_name}' 解析失败: {e}")
                all_parsed.extend(self._fallback_sheet(sheet_name, raw_text))

        logger.info(f"[HotelExcelParser] 全部解析完成，共 {len(all_parsed)} 家酒店")
        return all_parsed
