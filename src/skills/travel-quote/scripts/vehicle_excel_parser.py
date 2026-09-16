# -*- coding: utf-8 -*-
"""
车辆 Excel 非结构化价格数据 LLM 解析器

将车辆 Excel 中各 Sheet 的原始内容传给 LLM，
由 LLM 负责理解表格结构（中文列头、合并单元格、非标准日期等）
并提取为结构化的车辆价格数据，直接对齐 bs_travel_quote_vehicles 表字段。
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger

PARSE_PROMPT = """你是一个车辆价格数据解析助手。以下是从 Excel 中一个 Sheet 页读取的原始数据。

## 输入数据

Sheet 名称：{sheet_name}

原始数据（按行显示，每列用 | 分隔）：

{sheet_raw_text}

## 分析要求

请仔细分析数据的实际结构，从中提取出所有独立的车辆价格记录。

常见数据格式：
- 中文列头（如"时间"、"座位"、"单价"、"车型"、"日租金"等）
- 座位数可能写为"7座"、"7-9座"、"17座（元/公里）"等格式
- 日期/季节可能写为"6月30日前"、"7月1-10日"、"旺季"、"暑假"等
- 计价方式可能从列头或括号注释中推断（如"元/公里"=按公里计价，"元/天"=按天计价）
- 数据可能按时间分组（不同时间段不同价格）

## 车型枚举映射

将中文车型描述映射到以下 vehicle_type 枚举值：
- `business`：商务车（别克GL8、瑞风等，7座左右）
- `coaster`：考斯特（丰田考斯特等，17-23座）
- `minibus`：中巴（25-35座）
- `bus`：大巴（37-45座）
- `large_bus`：大型大巴（49-55座）

## 输出格式

请严格按以下 JSON 格式输出，每条记录对应一种车型在一个时间段的价格：

```json
[
  {{
    "vehicle_type": "枚举值(business/coaster/minibus/bus/large_bus)",
    "vehicle_type_label": "中文显示名，如'7座商务车'",
    "seats_max": 7,
    "pricing_mode": "per_km 或 daily",
    "per_km_rate": 3.4,
    "driver_meal_allowance": null,
    "driver_accommodation": null,
    "region_name": null,
    "effective_from": "2026-06-01",
    "effective_to": "2026-06-30",
    "remark": null
  }}
]
```

### 字段填写规则

1. **vehicle_type**：必须使用上述枚举值之一，根据座位数和车型描述判断
2. **vehicle_type_label**：简洁的中文描述，如"7座商务车"、"38座大巴"
3. **seats_max**：从"X座"中提取座位数，只取最大值。如"7座"则填7，"7-9座"则填9
4. **pricing_mode**：
   - 如果原始数据是"元/公里"，设为 `"per_km"`，将公里单价填入 `per_km_rate`
   - 如果原始数据是"元/天"或"日租金"，设为 `"daily"`，将日租金填入 `per_km_rate`
   - 如果无法判断计价方式但有单价，默认设为 `"per_km"`
5. **per_km_rate**：每公里费用（按公里计价）或日租金（按天计价），从"单价"列提取数值
6. **effective_from / effective_to**：从"6月30日前"、"7月1-10日"等描述中提取具体日期范围（格式 YYYY-MM-DD）。年份使用当前年份2026。如果无法确定具体日期则设为 null
7. **region_name**：如果数据中有区域信息则填写，否则为 null
8. 其他没有数据的字段设为 null
9. 如果"元/公里"出现在座位列的括号中，说明计价方式是按公里

注意：只输出 JSON 数组，不要输出其他内容。如果数据中没有有效的车辆价格信息，输出空数组 []。"""


def sheet_to_raw_text(file_path: str, sheet_name: str, max_rows: int = 300, max_cols: int = 20) -> str:
    """用 openpyxl 读取 Sheet 原始单元格内容，转为 pipe-delimited 文本。"""
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


# 数据库列名与默认值的映射，用于校验和填充
VEHICLE_DB_COLUMNS = [
    "vehicle_type", "vehicle_type_label", "seats_max",
    "pricing_mode", "per_km_rate",
    "driver_meal_allowance", "driver_accommodation",
    "region_name", "effective_from", "effective_to", "remark",
]

VALID_VEHICLE_TYPES = {"business", "coaster", "minibus", "bus", "large_bus"}
VALID_PRICING_MODES = {"daily", "per_km"}


class VehicleExcelParser:
    """车辆 Excel 非结构化价格数据 LLM 解析器"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        return self._gateway

    async def parse_sheet_by_name(self, file_path: str, sheet_name: str, tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict]:
        """读取指定 Sheet 的原始内容并解析。"""
        raw_text = sheet_to_raw_text(file_path, sheet_name)
        return await self.parse_sheet(sheet_name, raw_text, tenant_id=tenant_id, user_id=user_id)

    async def parse_sheet(self, sheet_name: str, raw_text: str, tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict]:
        """用 LLM 解析单个 Sheet 的原始文本，返回结构化车辆数据。"""
        if not raw_text or len(raw_text.strip()) < 10:
            return []

        prompt = PARSE_PROMPT.format(sheet_name=sheet_name, sheet_raw_text=raw_text)

        gateway = self._get_gateway()
        try:
            response = await gateway.chat_no_thinking(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=8192,
            )

            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(
                response.get("usage") if isinstance(response, dict) else None,
                source="vehicle_excel_parser",
                model=gateway.get_model_name(),
                tenant_id=tenant_id,
                user_id=user_id,
            )

            content = response.get("content", "")
            if not content:
                logger.warning(f"[VehicleExcelParser] Sheet '{sheet_name}' LLM 返回空内容")
                return []

            return self._parse_llm_output(content, sheet_name)

        except Exception as e:
            logger.opt(exception=True).error(f"[VehicleExcelParser] Sheet '{sheet_name}' LLM 调用失败: {e}")
            return []

    def _parse_llm_output(self, content: str, sheet_name: str) -> List[Dict]:
        """解析 LLM 输出的 JSON，校验并规范化字段。"""
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
            logger.warning(f"[VehicleExcelParser] Sheet '{sheet_name}' JSON 解析失败: {e}, 内容前200字: {json_str[:200]}")
            return []

        if not isinstance(parsed, list):
            logger.warning(f"[VehicleExcelParser] Sheet '{sheet_name}' LLM 输出不是数组: {type(parsed)}")
            return []

        results = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            vehicle_type = item.get("vehicle_type", "").strip()
            if not vehicle_type or vehicle_type not in VALID_VEHICLE_TYPES:
                continue

            seats_max = item.get("seats_max")
            if seats_max is None:
                continue

            record = {
                "vehicle_type": vehicle_type,
                "vehicle_type_label": item.get("vehicle_type_label", "") or "",
                "seats_max": int(seats_max),
                "pricing_mode": item.get("pricing_mode", "per_km") or "per_km",
                "per_km_rate": self._to_float(item.get("per_km_rate")),
                "driver_meal_allowance": self._to_float(item.get("driver_meal_allowance")),
                "driver_accommodation": self._to_float(item.get("driver_accommodation")),
                "region_name": item.get("region_name") or None,
                "effective_from": item.get("effective_from") or None,
                "effective_to": item.get("effective_to") or None,
                "remark": item.get("remark") or None,
            }

            # 校验枚举值
            if record["pricing_mode"] not in VALID_PRICING_MODES:
                record["pricing_mode"] = "per_km"

            results.append(record)

        return results

    @staticmethod
    def _to_float(value: Any) -> float | None:
        """将值转为 float，失败返回 None。"""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def parse_excel_sheets(self, file_path: str, tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict]:
        """处理 Excel 文件：逐 Sheet 解析车辆价格数据。"""
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        all_parsed = []
        sheet_names = [sn for sn in wb.sheetnames if not sn.startswith("WpsReserved")]

        # 扫描有效 Sheet
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

        logger.info(f"[VehicleExcelParser] 共 {len(valid_sheets)} 个有效 Sheet，开始逐个解析")

        for idx, sheet_name in enumerate(valid_sheets, 1):
            raw_text = sheet_to_raw_text(file_path, sheet_name)

            if not raw_text or len(raw_text.strip()) < 10:
                continue

            logger.info(f"[VehicleExcelParser] 解析 Sheet {idx}/{len(valid_sheets)}: '{sheet_name}' ({len(raw_text)} 字符)...")

            try:
                parsed = await self.parse_sheet(sheet_name, raw_text, tenant_id=tenant_id, user_id=user_id)
                if parsed:
                    all_parsed.extend(parsed)
                    logger.info(f"[VehicleExcelParser] Sheet '{sheet_name}' 解析完成，得到 {len(parsed)} 条车辆记录")
                else:
                    logger.info(f"[VehicleExcelParser] Sheet '{sheet_name}' 无有效车辆数据")
            except Exception as e:
                logger.error(f"[VehicleExcelParser] Sheet '{sheet_name}' 解析失败: {e}")

        logger.info(f"[VehicleExcelParser] 全部解析完成，共 {len(all_parsed)} 条车辆记录")
        return all_parsed
