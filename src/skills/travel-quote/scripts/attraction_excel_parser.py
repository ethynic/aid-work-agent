# -*- coding: utf-8 -*-
"""
景点 Excel 非结构化价格文本 LLM 批量解析器

将景点 Excel（研学项目报价总表）中各 Sheet 的原始内容直接传给 LLM，
由 LLM 负责理解表格结构（左右并列、上下分段等）并提取景点和价格信息。

设计思路：景点 Excel 的 Sheet 结构高度非结构化（左右并列双表、上下多段、
合并单元格、列名不统一等），按行结构化解析不可靠。直接把 Sheet 原始文本
交给 LLM 解析是更稳健的方案。
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger

# 解析 Prompt（单 Sheet 模式）
PARSE_PROMPT = """你是一个景点门票价格数据解析助手。以下是从 Excel 中一个 Sheet 页读取的原始数据。

## 输入数据

Sheet 名称：{sheet_name}

原始数据（按行显示，每列用 | 分隔）：

{sheet_raw_text}

## 分析要求

这个 Sheet 可能包含以下任意结构：
- **多景点 Sheet**：一个 Sheet 包含多个景点，每个景点有自己的名称、区域和价格，分别输出为独立的 JSON 对象
- **单景点 Sheet**：一个 Sheet 只有一个景点（通常是 Sheet 名称或标题行暗示的景点），Sheet 中的其余内容全部是该景点内的项目/服务/产品，必须归入该景点
- 左右并列的多个表格（用空列分隔）
- 上下分段的多个区域（用空行或标题行分隔）
- 混合结构（部分是景点汇总，部分是具体项目报价）

### 关键判断规则（必须遵守）

1. **不要遗漏数据**：Sheet 中的每一行价格数据都必须被提取，禁止因为"不是景点"而跳过
2. **单景点 Sheet 识别**：如果 Sheet 中只有一个景点名称（通常是标题或大字），则该 Sheet 内的所有价格行、课程、产品、项目都是该景点的一部分，归入同一个景点对象。例如一个"天空之桥"Sheet 中既有研学课程价格又有文创产品价格，它们都属于"天空之桥"这个景点
3. **多景点 Sheet 识别**：如果 Sheet 中明确出现多个不同的景点名称（通常在名称列中），才分别输出为多个 JSON 对象
4. **项目不是景点**：研学课程、文创产品、体验活动等是景点内的项目/服务，不要把它们当作独立景点输出

请仔细分析数据的实际结构，从中提取出所有景点及其完整的价格信息。

### 图片列处理（可选）

如果 Sheet 中有「封面图」「图集」（或同义词如「封面」「图片」）列，提取每个景点的图片文件名：
- 「封面图」列：单个文件名（如 "黄果树瀑布.jpg"）
- 「图集」列：分号分隔的多个文件名（如 "图1.jpg;图2.jpg;图3.jpg"）
- 没有这些列时省略对应字段
- 文件名是相对于 zip 包内 images/ 目录的路径

## 输出要求

对每个提取出的景点，输出三部分：

### 第1部分：景点信息摘要
景点名称：xxx
所在区域：贵州省 xx市/xx方向
景区等级：xA级/省级/其他
景点类型：自然风光/人文历史/主题乐园/研学基地/博物馆/其他
主要票种：从价格数据中提取（如：成人票、学生票、团队票等）
门票价格区间：xxx-xxx元/人
景区交通：环保车xx元/人、索道xx元/人（如有）
特殊说明：免票政策、优惠政策、团体条件等

### 第2部分：门票价格表
仅包含**进入景区的门票**价格，**同一票种按适用对象分行输出**，价格字段标注价格类型：
门票+观光车+保险 | 成人 | 挂牌价:110元 | 优惠价:50元 | 结算价:40元
门票+观光车+保险 | 老人/学生/儿童 | 40元

### 第3部分：项目/服务价格表
包含景区内**除门票外的所有收费项目**，如：
- 研学课程/科普活动（如"玩转贵地博 139元/孩子"）
- 文创产品/纪念品（如"天空之桥模型 导游价:40元/盒"）
- 设备租用（如语音导览、AR导览、耳机）
- 交通费用（如电瓶车、索道、观光车）
- 体验项目（如蜡染体验、化石修复、夜探博物馆）
- 讲解服务
- 餐饮、住宿等其他收费

**一行一个项目/服务**，格式为：
项目名称 | 说明（适用对象/规格等） | 价格（该项目的所有可提取价格及适用条件）

示例：
超级桥梁 | 1-3年级项目化课程 | 158元/人（10-500人）、148元/人（500-1000人）、138元/人（1000人以上） | 含教学材料包、学生手册
天空之桥模型 | 文创纪念品 | 扫码价:168元/盒、最低零售价:80元/盒、导游价:40元/盒、旅行社价:35元/盒 | 含运费及增值税发票

规则：
1. **所有价格信息必须提取为具体数字，不要遗漏任何价格**
2. **一个项目的所有价格情况和适用条件写在同一行**，用顿号或逗号分隔不同价格
3. "挂牌价"和"结算价"是两个不同价格，都要提取
4. 团体优惠条件（如"16免1"、"10人成团"）必须保留
5. 第2部分只放门票，第3部分放其他所有收费项目
6. 如果原始数据不完整，尽量推断，无法推断的标注"未知"
7. **第2部分的价格字段必须标注价格类型**（如"挂牌价:110元 | 优惠价:50元 | 结算价:40元"），不要只输出裸数字
8. **不要因为项目多就省略**：即使一个景点有几十个项目，也要全部列出
9. **Excel 列名中可能包含特定人群票价**（如列名"老人票、学生票、儿童票（元）"），这种列名本身就是独立的人群票型，必须按适用对象分行输出，不能合并到成人票价行中

## 输出格式

请严格按以下 JSON 格式输出：

```json
[
  {{
    "attraction_name": "景点名称",
    "region": "区域/方向",
    "info_text": "景点信息摘要文本（多行，一行一字段，用\\n分隔）",
    "ticket_table_text": "门票价格表文本（一行一个价格）",
    "project_table_text": "项目/服务价格表文本（一行一个项目或者服务）",
    "cover_image_filename": "黄果树瀑布.jpg（从「封面图」列提取的文件名，相对于 images/ 目录；无此列则省略）",
    "gallery_image_filenames": ["图1.jpg", "图2.jpg"],
    "metadata": {{
      "region": "区域",
      "category_cn": "中文景点类型",
      "category": "natural/cultural/theme_park/museum/research/other",
      "source_sheet": "{sheet_name}"
    }}
  }}
]
```

说明：
- `cover_image_filename` / `gallery_image_filenames` 是可选字段，仅在 Sheet 含图片列时输出
- `gallery_image_filenames` 中 `图1.jpg;图2.jpg` 这种分号分隔的内容应输出为 JSON 数组 `["图1.jpg", "图2.jpg"]`

注意：只输出 JSON 数组，不要输出其他内容。如果数据中没有有效的景点/项目信息，输出空数组 []。"""


def sheet_to_raw_text(file_path: str, sheet_name: str, max_rows: int = 200, max_cols: int = 20) -> str:
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


def has_valid_data(sheet_data: Dict) -> bool:
    """检查 Sheet 是否有有效数据（至少含一些非空内容）"""
    rows = sheet_data.get("rows", [])
    if not rows:
        return False

    non_empty_count = 0
    for row in rows:
        vals = [str(v).strip() for v in row.values() if v]
        if vals:
            non_empty_count += 1

    return non_empty_count >= 2


class AttractionExcelParser:
    """景点 Excel 非结构化价格文本 LLM 批量解析器"""

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
            [{ attraction_name, region, info_text, ticket_table_text, metadata }, ...]
        """
        if not raw_text or len(raw_text.strip()) < 20:
            return []

        prompt = PARSE_PROMPT.format(sheet_name=sheet_name, sheet_raw_text=raw_text)

        gateway = self._get_gateway()
        try:
            response = await gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=16384,
            )

            content = response.get("content", "")
            if not content:
                logger.warning(f"[AttractionExcelParser] Sheet '{sheet_name}' LLM 返回空内容")
                return []

            return self._parse_llm_output(content, sheet_name)

        except Exception as e:
            logger.error(f"[AttractionExcelParser] Sheet '{sheet_name}' LLM 调用失败: {e}", exc_info=True)
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
            logger.warning(f"[AttractionExcelParser] Sheet '{sheet_name}' JSON 解析失败: {e}, 内容前200字: {json_str[:200]}")
            return []

        if not isinstance(parsed, list):
            logger.warning(f"[AttractionExcelParser] Sheet '{sheet_name}' LLM 输出不是数组: {type(parsed)}")
            return []

        results = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            attraction_name = item.get("attraction_name", "").strip()
            if not attraction_name:
                continue

            info_text = item.get("info_text", "").strip()
            ticket_table = item.get("ticket_table_text", "").strip()
            project_table = item.get("project_table_text", "").strip()

            if not info_text and not ticket_table and not project_table:
                continue

            region = item.get("region", "").strip()
            metadata = item.get("metadata", {}) or {}
            metadata["source_sheet"] = sheet_name

            # 提取图片文件名字段（可选，zip 包导入时使用）
            cover_filename = item.get("cover_image_filename", "")
            if isinstance(cover_filename, str):
                cover_filename = cover_filename.strip() or None
            else:
                cover_filename = None

            gallery_raw = item.get("gallery_image_filenames")
            if isinstance(gallery_raw, str):
                # 兼容 LLM 可能输出字符串 "a.jpg;b.jpg"
                gallery_filenames = [s.strip() for s in gallery_raw.split(";") if s.strip()] or None
            elif isinstance(gallery_raw, list):
                gallery_filenames = [str(s).strip() for s in gallery_raw if str(s).strip()] or None
            else:
                gallery_filenames = None

            results.append({
                "attraction_name": attraction_name,
                "region": region,
                "info_text": info_text,
                "ticket_table_text": ticket_table,
                "project_table_text": project_table,
                "cover_image_filename": cover_filename,
                "gallery_image_filenames": gallery_filenames,
                "metadata": metadata,
            })

        return results

    def _fallback_sheet(self, sheet_name: str, raw_text: str) -> List[Dict]:
        """LLM 解析失败时的降级处理"""
        return [{
            "attraction_name": sheet_name.strip(),
            "region": sheet_name.replace("方向", "").replace("汇总", ""),
            "info_text": raw_text[:1000] if raw_text else f"景点名称：{sheet_name}",
            "ticket_table_text": "",
            "project_table_text": "",
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

        logger.info(f"[AttractionExcelParser] 共 {len(valid_sheets)} 个有效 Sheet，开始逐个解析")

        for idx, sheet_name in enumerate(valid_sheets, 1):
            raw_text = sheet_to_raw_text(file_path, sheet_name)

            if not raw_text or len(raw_text.strip()) < 20:
                continue

            logger.info(f"[AttractionExcelParser] 解析 Sheet {idx}/{len(valid_sheets)}: '{sheet_name}' ({len(raw_text)} 字符)...")

            try:
                parsed = await self.parse_sheet(sheet_name, raw_text)
                if parsed:
                    all_parsed.extend(parsed)
                    logger.info(f"[AttractionExcelParser] Sheet '{sheet_name}' 解析完成，得到 {len(parsed)} 个景点")
                else:
                    logger.info(f"[AttractionExcelParser] Sheet '{sheet_name}' 无有效景点数据")
            except Exception as e:
                logger.error(f"[AttractionExcelParser] Sheet '{sheet_name}' 解析失败: {e}")
                all_parsed.extend(self._fallback_sheet(sheet_name, raw_text))

        logger.info(f"[AttractionExcelParser] 全部解析完成，共 {len(all_parsed)} 个景点")
        return all_parsed
