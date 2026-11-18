"""
Excel 工具内部 LLM 路由器

根据 Agent 传来的 context 和 file_paths，调用 LLM 判断应执行哪些操作及参数。
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.context import resolve_llm_gateway


ROUTING_PROMPT_PREFIX = """你是 Excel 电子表格处理工具的内部路由器。根据用户的请求和上下文，决定应该执行哪些操作并提取参数。

## 可用操作

1. **read** — 读取 Excel 文件的数据内容
   - 触发：用户想看Excel里有什么数据、查看某个Sheet；**大文件（数千行以上）或用户要求分块/按范围读取时必须用 read**
   - 参数：{sheet_name: "可选", range: "可选,如A1:D10", include_formulas: false}
   - 大文件用 range 分段读取（如 A1:C2000，逐段推进），单次最多返回 500 行有效数据

2. **to_md** — 将 Excel 转为 Markdown 表格
   - 触发：用户要求查看Excel内容、将Excel转为文本
   - 参数：{sheet_name: "可选", max_rows: 100}
   - **禁止用于大文件（数千行以上）**：to_md 是整簿转换会内存超限，大文件一律 read + range 分块

3. **export** — 从数据创建新的 Excel 文件
   - 触发：用户要求导出数据为Excel、把表格数据存为Excel、创建报表
   - 参数：{data_type: "markdown|csv|json|table", file_name: "输出文件名", sheet_name: "可选", auto_format: true}

4. **modify** — 修改已有 Excel 文件内容
   - 触发：用户要求修改单元格、插入行列、删除行列、合并单元格、文本查找替换
   - 参数：{operations: [{type, ...具体参数}], output_name: "可选"}
   - **type 只能取以下值，禁止编造其它类型**（如 set_cell、replace_text 都不存在）：
     `write_cell`（写单格: cell="A1", value="新值"）、`write_range`（写区域: start="A1", data=[[...]]）、
     `insert_rows`（at, amount）、`delete_rows`（at, amount）、`insert_columns`（at, amount）、
     `delete_columns`（at, amount）、`rename_sheet`（old_name, new_name）、`add_sheet`（name, data?）、
     `delete_sheet`（name）、`merge_cells`（range="A1:D1"）、`unmerge_cells`（range）、`sort_data`
   - 示例：把A1标题改为XX → {"operations": [{"type": "write_cell", "cell": "A1", "value": "XX"}]}
   - 注意：你不知道单元格坐标时（如"把全文的X替换为Y"），先返回 read 让调用方拿到内容后再发起 modify；
     不要凭空猜测单元格位置

5. **format** — 设置 Excel 格式样式
   - 触发：用户要求设置字体、边框、颜色、列宽、数字格式
   - 参数：{format_operations: [{type, ...具体参数}], output_name: "可选"}

6. **fill_template** — 使用模板填充数据（两种模式）
   - 触发：用户要求基于模板/样例生成Excel、按某个样例版式填写数据
   - 模板来源：a) template_name=系统模板名 b) template_file=用户上传的模板路径 c) file_paths 中的样例附件
   - **模式一（智能填充，推荐）**：调用方直接传 `data`（结构化 {meta,rows,group_subtotals,totals}）+ 样例附件。
     工具会 AI 分析样例结构并按版式填入，自动处理行数多/少/相等、保留样例样式。**注意：data 由调用方在工具入参直接传递，路由器无需从 context 提取，仅需返回 task=fill_template + template_file=附件路径。**
   - **模式二（占位符替换，旧）**：样例含 `{{变量名}}` 时用 variables={key:value} 替换
   - 参数：{template_file: "样例路径", variables: "模式二用", output_name: "可选"}

7. **list_templates** — 列出可用模板
   - 触发：用户问有哪些模板可用
   - 参数：{}

8. **merge** — 合并多个文件
   - 触发：用户要求合并多个Excel/CSV文件
   - 参数：{output_name: "可选", merge_mode: "rows|sheets"}

9. **convert** — 格式转换
   - 触发：用户要求CSV转Excel、Excel转CSV、JSON转Excel
   - 参数：{source_format: "csv|json|excel", target_format: "csv|json|excel", output_name: "可选"}

## 判断规则

按优先级从高到低匹配：
- 有附件且上下文提到"模板"、"按这个格式"、"照着这个填"等 → fill_template（template_file = 附件路径）
- 要求基于系统模板生成（无附件模板，提到模板名或要求选择）→ fill_template（template_name）或先 list_templates
- 有附件且要求转为Markdown理解 → to_md（大文件除外，见下）
- 大文件（数千行以上）或要求分块/按范围/前N行读取 → read + range 分段，禁止 to_md
- 要求修改已有Excel → modify
- 要求设置样式格式 → format
- 要求创建新Excel / 导出数据 → export（但 context 中必须包含实际数据，如果只有用户意图没有数据，则 params 中标注 needs_data: true）
- 要求格式转换 → convert
- 要求合并文件 → merge
- 要求查看数据内容 → read
- 仅有附件无明确指令 → to_md（默认将内容转为可理解格式）

## export 操作的数据检查

export 操作必须检查 context 中是否包含实际表格数据（Markdown表格有 | 分隔符，JSON有 [] 或 {}）：
- 如果 context 包含实际数据 → 正常返回 export，params 中包含 data_type
- 如果 context 只有用户意图描述（如"帮我生成Excel"、"导出数据"等），没有实际数据 → 仍然返回 export，但在 params 中加 "needs_data": true
- 路由器不要返回 to_md 等其他操作来替代 export，即使数据不完整也优先返回 export

## 输出格式

严格输出 JSON，不要输出其他内容：
{
  "task": "操作名（可逗号分隔多个，如 read,export）",
  "params": { ... 操作对应参数 ... },
  "reason": "判断依据"
}

## fill_template 参数示例

场景一：用户选择系统模板
用户："用月度报告模板生成4月份报告"
→ {"task": "fill_template", "params": {"template_name": "monthly_report", "variables": {"month": "4月"}}, "reason": "用户指定系统模板名"}

场景二：用户上传临时模板
用户上传 attachment.xlsx 后说："按这个模板填一下，公司名是XX科技"
→ {"task": "fill_template", "params": {"template_file": "/path/to/attachment.xlsx", "variables": {"公司名": "XX科技"}}, "reason": "用户上传模板并要求填充"}

场景三：用户不确定有什么模板
用户："有哪些报告模板？"
→ {"task": "list_templates", "params": {}, "reason": "用户要求查看模板列表"}

## 用户上下文
"""

ROUTING_PROMPT_SUFFIX = """

## 附件文件
"""

# 修改类意图触发词：命中即跳过规则路由的 to_md 短路，交给 LLM 路由判定
# （修改/查找替换/删行等指令常含"内容""文本"等词，曾被误劫持为 to_md 只读操作）
# "modify"/"填写"/"填入"/"设置"：2026-11-10 报价单事故（tr_runner_96fc4bec）回归——
# LLM 照 fill_template 引导措辞写 "modify：…B4 单元格 = …"，旧词表全未命中，
# 指令又含"内容"，连续两次被劫持成 to_md 只读却报 success，Agent 误判已写入后
# 放弃模板用 markdown 重建文件，最终交付物丢失模板样式
_MODIFY_INTENT_TOKENS = (
    "修改", "替换", "改为", "改成", "改一下", "改下", "删除", "去掉", "清除",
    "插入", "新增一行", "加一行", "写入", "更新", "重命名", "查找替换",
    "modify", "填写", "填入", "设置", "调整", "修正", "补全", "改写", "设为", "设置成",
)

# 单元格坐标引用（如 B4、AA12、R1C1）；前后不得紧跟字母/数字，避免命中
# file_90ec1c11ef49 这类文件 ID 内部的字母数字段
_CELL_REF_RE = re.compile(r"(?<![a-z0-9])[a-z]{1,3}\d{1,7}(?![a-z0-9])")


def _is_modify_intent(lower_ctx: str) -> bool:
    if any(token in lower_ctx for token in _MODIFY_INTENT_TOKENS):
        return True
    # 坐标兜底："B4 单元格填写…" 这类表述词表永远追不上——凡"单元格/cell"与
    # 坐标引用同现即按修改意图处理。误判方向安全：只是跳过 to_md 短路多走一轮
    # LLM 路由（LLM 路由仍可判 read/to_md），不会把只读指令劫持成写操作
    if ("单元格" in lower_ctx or "cell" in lower_ctx) and _CELL_REF_RE.search(lower_ctx):
        return True
    return False


# 分块读取意图：命中即跳过规则路由的 to_md 短路，交给 LLM 路由判 read+range
# （2026-11-12 agent2 压测：1M 行 Excel 被 to_md 短路成整簿转换，MarkItDown 全量
# 加载顶死 1GiB cgroup、事件循环停跳 8.6 分钟。read 路径是 read_only 流式+500 行
# 截断，大文件安全；to_md 无规模守门时整簿转换是禁手——见 excel_to_md._guard_too_large）
_CHUNKED_READ_TOKENS = (
    "分块", "分段", "分批", "分次", "每次读", "逐行读", "逐段",
    "一部分一部分", "部分读取", "读取部分",
)

# 区域引用（如 A1:D10）；前后不得紧跟字母/数字，避免命中文件 ID 内部字母数字段
_RANGE_RE = re.compile(r"(?<![a-z0-9])[a-z]{1,3}\d{1,7}:[a-z]{1,3}\d{1,7}(?![a-z0-9])")


def _is_chunked_read_intent(lower_ctx: str) -> bool:
    if any(token in lower_ctx for token in _CHUNKED_READ_TOKENS):
        return True
    # "前 N 行"/"前N行" 词表不好覆盖，用正则；误判方向安全：只多走一轮 LLM 路由
    if re.search(r"前\s*\d+\s*行", lower_ctx):
        return True
    if _RANGE_RE.search(lower_ctx):
        return True
    return False


# 纯读取判定（白名单残差法，2026-11-18 三轮评审收敛）：
# 动作动词是开放集合（复制/填充/标红/居中/边框/拆成/对调/冻结…列举不完），
# 黑名单永远会漏——第三轮实测「读取表格并复制A列到B列」仍被 to_md 短路成只读，
# 返回 success 但无产物（与 2026-09-15 to_md 劫持同型）。
# 因此改为：把「读取动词 + 只读名词/虚词 + 名词化短语」全部剥离后，指令必须无剩余
# 内容才判纯读；任何剩余内容（已知或未知动作词）都交 LLM 路由。误判方向安全：
# 最多多走一轮 LLM 路由，绝不会把写操作降级为只读。
_READ_VERB_PREFIXES = ("读取", "查看", "检查", "预览", "打开", "看看", "读一下", "看一下")
_POLITE_PREFIXES = ("请帮我", "麻烦", "帮我", "请", "能否", "可以")
# 名词化短语：出现在读取指令里描述"读取目的"，不代表写操作（如"检查修改结果"）
_READ_RESULT_PHRASES = (
    "修改结果", "修改情况", "修改后", "修改的", "修改效果", "修改前",
    "调整结果", "调整后", "更新结果", "更新后", "编辑结果",
)
# 只读填充词：剥离后若无剩余内容，说明指令只表达"读取/查看某对象"本身
_READ_FILLER_TOKENS = (
    "读取", "查看", "检查", "预览", "打开", "看看", "读一下", "看一下", "核对", "确认", "审阅",
    "文件", "表格", "表头", "表", "excel", "xlsx", "csv", "模板", "内容", "数据", "结构", "版式",
    "格式", "结果", "情况", "信息", "明细", "单元格", "附件", "文档", "工作表", "sheet",
    "是否", "正确", "完整", "有无", "重点", "主要", "整体", "初步", "仔细", "当前", "这个", "该",
    "一下", "的", "了", "中", "里", "内", "和", "并", "再", "后", "请", "帮我", "我", "要", "把",
    "吗", "呢", "吧", "啊", "这", "那", "下", "上", "有", "没", "无", "多少", "哪些", "几个",
    "什么", "以及", "然后", "先", "帮", "看", "这个", "这份", "那份",
)
_READ_FILLER_RE = re.compile(
    "|".join(sorted((re.escape(t) for t in _READ_FILLER_TOKENS), key=len, reverse=True))
)
_READ_RESIDUE_PUNCT_RE = re.compile(r"[\s，。、；：！？,.;:!?()（）\[\]【】《》\"'`~\-—_/+]+")
# 已知动作词：仅用于快速否决（非穷尽——兜底靠上面的残差白名单）
_WRITE_ACTION_TOKENS = (
    "改为", "改成", "替换", "写入", "填入", "填写", "设置", "删除", "去掉", "清除",
    "插入", "合并", "重命名", "更新", "调整", "修正", "补全", "改写", "保存", "导出",
    "生成", "新建", "创建", "覆盖", "粘贴", "追加", "加粗", "改正", "修复", "回填",
    "拆分", "排序", "去重", "复制", "填充", "标红", "居中", "边框", "对调", "冻结",
    "转置", "改名", "排列", "移到", "乘以", "除以",
)


def _read_residue(lower_ctx: str) -> str:
    """剥离只读动词/名词/虚词与名词化短语后的剩余内容（空串 = 纯读取）。"""
    s = lower_ctx
    for phrase in _READ_RESULT_PHRASES:
        s = s.replace(phrase, "")
    s = _READ_FILLER_RE.sub("", s)
    s = _READ_RESIDUE_PUNCT_RE.sub("", s)
    return s


def _is_pure_read_instruction(lower_ctx: str) -> bool:
    """纯读取指令判定：读取动词开头、无坐标，且剥离只读词后无任何剩余内容。"""
    s = (lower_ctx or "").strip()
    if not s:
        return False
    if _CELL_REF_RE.search(s):
        return False
    # 分块读取（前 N 行 / 区域 / 逐段）必须走 LLM 路由生成 read+range 参数
    if _is_chunked_read_intent(s):
        return False
    if any(token in s for token in _WRITE_ACTION_TOKENS):
        return False  # 快速否决（非穷尽，兜底是下面的残差判定）
    head = s
    for prefix in _POLITE_PREFIXES:
        if head.startswith(prefix):
            head = head[len(prefix):].lstrip()
            break
    if not head.startswith(_READ_VERB_PREFIXES):
        return False
    return _read_residue(s) == ""


class ExcelRouter:
    """Excel 工具内部 LLM 路由器"""

    VALID_TASKS = {
        "read", "to_md", "export", "modify", "format",
        "fill_template", "list_templates", "merge", "convert",
    }

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import llm_gateway
            self._gateway = llm_gateway
        # 优先用执行上下文中的 agent gateway（含子智能体 model_code 覆盖，与计费模型同源）
        return resolve_llm_gateway(self._gateway)

    async def route(self, context: Optional[str], file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        根据 context 和 file_paths 决定操作类型和参数。

        Returns:
            {"task": "export", "params": {...}} 或 {"task": "", "params": {}, "error": "..."}
        """
        rule_result = self._rule_based_route(context, file_paths)
        if rule_result.get("task"):
            logger.info(
                f"[ExcelRouter] 规则路由结果: task={rule_result['task']}, "
                f"reason={rule_result.get('reason', '')}"
            )
            return rule_result

        prompt = self._build_prompt(context, file_paths)
        gateway = self._get_gateway()

        try:
            response = await gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                # 预算给足（4096→16384）：思考 token 计入 max_tokens，小预算会被吃光
                # 致 content 空截断；路由是微判定任务，显式关思考（提速+省token），
                # 预算只是保险丝
                max_tokens=16384,
                enable_thinking=False,
            )

            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(
                response.get("usage") if isinstance(response, dict) else None,
                source="excel_router",
                model=gateway.get_model_name(),
            )

            content = response.get("content", "")
            if not content:
                logger.warning("[ExcelRouter] LLM 返回空内容")
                if rule_result.get("task"):
                    return rule_result
                return {"task": "", "params": {}, "error": "LLM 返回空内容"}

            result = self._parse_response(content)
            if result.get("task"):
                logger.info(f"[ExcelRouter] 路由结果: task={result['task']}, reason={result.get('reason', '')}")
                return result

            logger.warning(f"[ExcelRouter] 无法确定操作: {content[:200]}")
            if rule_result.get("task"):
                return rule_result
            return {"task": "", "params": {}, "error": "LLM 无法确定操作类型"}

        except Exception as e:
            logger.error(f"[ExcelRouter] LLM 调用失败: {e}")
            if rule_result.get("task"):
                return rule_result
            return {"task": "", "params": {}, "error": str(e)}

    def _build_prompt(self, context: Optional[str], file_paths: Optional[List[str]]) -> str:
        ctx = context or "(无上下文)"
        files = json.dumps(file_paths, ensure_ascii=False) if file_paths else "(无附件)"
        return ROUTING_PROMPT_PREFIX + ctx + ROUTING_PROMPT_SUFFIX + files

    def _parse_response(self, content: str) -> Dict[str, Any]:
        """解析 LLM 输出为结构化 JSON"""
        json_str = content.strip()

        if json_str.startswith("```"):
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()

        try:
            data = json.loads(json_str)
            task = data.get("task", "")
            params = data.get("params", {})

            tasks = [t.strip() for t in task.split(",") if t.strip()]
            invalid = [t for t in tasks if t not in self.VALID_TASKS]
            if invalid:
                logger.warning(f"[ExcelRouter] 无效操作: {invalid}")
                return {"task": "", "params": {}, "error": f"无效操作: {invalid}"}

            return {
                "task": task,
                "params": params,
                "reason": data.get("reason", ""),
            }
        except json.JSONDecodeError:
            logger.warning(f"[ExcelRouter] JSON 解析失败: {content[:200]}")
            return {"task": "", "params": {}, "error": "JSON 解析失败"}

    def _rule_based_route(
        self,
        context: Optional[str],
        file_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Handle deterministic spreadsheet tasks without depending on LLM routing."""
        ctx = context or ""
        paths = file_paths or []
        lower_ctx = ctx.lower()

        first_path = Path(paths[0]) if paths else None
        first_ext = first_path.suffix.lower() if first_path else ""

        wants_excel = any(
            token in lower_ctx
            for token in ("excel", "xlsx", "电子表格", "转为excel", "转成excel", "导出", "创建")
        )

        if paths and first_ext == ".csv" and wants_excel:
            return {
                "task": "convert",
                "params": {
                    "source_format": "csv",
                    "target_format": "excel",
                    "output_name": first_path.with_suffix(".xlsx").name,
                },
                "reason": "规则识别：CSV附件转Excel",
            }

        if paths and first_ext == ".json" and wants_excel:
            return {
                "task": "convert",
                "params": {
                    "source_format": "json",
                    "target_format": "excel",
                    "output_name": first_path.with_suffix(".xlsx").name,
                },
                "reason": "规则识别：JSON附件转Excel",
            }

        data_type = self._detect_context_data_type(ctx)
        if data_type and wants_excel:
            return {
                "task": "export",
                "params": {
                    "data_type": data_type,
                    "file_name": self._extract_output_name(ctx),
                    "sheet_name": "Sheet1",
                    "auto_format": True,
                },
                "reason": f"规则识别：context包含{data_type}表格数据并要求导出Excel",
            }

        # 纯读取快路径（白名单残差法，见 _is_pure_read_instruction 注释）：
        # 只有剥离读取动词/只读名词后无任何剩余内容的指令才短路；含修改类名词化语境
        # （"检查修改结果"）走 read，其余纯读取走 to_md。任何无法确认为纯读取的指令
        # 一律交给 LLM 路由——写操作绝不能被降级为只读（2026-09-15 to_md 劫持教训）。
        if paths and _is_pure_read_instruction(lower_ctx):
            task = "read" if _is_modify_intent(lower_ctx) else "to_md"
            return {
                "task": task,
                "params": {},
                "reason": f"规则识别：纯读取指令（无写动作残留），走 {task}",
            }

        # 仅附件无指令：维持既有默认（转可读文本）；有指令但无法确认纯读取的一律交 LLM
        if paths and not ctx.strip():
            return {
                "task": "to_md",
                "params": {},
                "reason": "规则识别：仅附件无指令，默认转为可读文本",
            }

        if len(paths) >= 2 and any(token in lower_ctx for token in ("合并", "merge")):
            return {
                "task": "merge",
                "params": {"merge_mode": "sheets"},
                "reason": "规则识别：合并多个表格文件",
            }

        return {"task": "", "params": {}, "error": "规则路由未命中"}

    def _detect_context_data_type(self, context: str) -> str:
        stripped = (context or "").strip()
        if not stripped:
            return ""
        if self._has_markdown_table(stripped):
            return "markdown"
        if stripped.startswith("[") or stripped.startswith("{"):
            return "json"
        if "," in stripped and "\n" in stripped:
            return "csv"
        return ""

    def _has_markdown_table(self, text: str) -> bool:
        table_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("|") and line.strip().endswith("|")
        ]
        if len(table_lines) < 2:
            return False
        return any(re.match(r"^\|[\s\-:|]+\|$", line) for line in table_lines)

    def _extract_output_name(self, context: str) -> Optional[str]:
        match = re.search(r"([\w\u4e00-\u9fff（）()《》+_\- ]+\.xlsx)", context or "")
        if match:
            return match.group(1).strip()
        return None
