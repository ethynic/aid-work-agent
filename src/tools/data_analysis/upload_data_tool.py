"""
UploadDataFileTool — 聊天附件 Excel/CSV 自动注册到知识库

当用户在聊天中发送 Excel/CSV 附件并要求分析时，LLM 调用此工具将文件解析并注册到知识库，
然后调用 analyze_data 完成分析。
"""

import asyncio
import os
from typing import Any, Dict, List

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class UploadDataFileInput(BaseModel):
    file_path: str = Field(description="Excel/CSV 文件的完整路径")
    analysis_intent: str = Field(description="用户对文件的分析意图")


class UploadDataFileTool(BaseTool):
    name = "upload_data_file"
    description = (
        "将 Excel/CSV 数据文件注册到知识库。当用户在聊天中发送了 Excel/CSV 附件并要求分析时，"
        "必须先调用此工具注册，再调用 analyze_data 分析。支持 .xlsx、.xls、.csv。"
    )
    display_name = "注册数据文件"
    category = "data_analysis"
    InputModel = UploadDataFileInput

    def _remote_dispatch_enabled(self) -> bool:
        """远程解析开关（重工具服务设计 §4.1/§9）：HEAVY_TOOLS_MODE=remote 且本工具
        在 agent_runner.heavy_tools 白名单。local 模式回退本地解析（零代码回滚）。"""
        from src.services.heavy_tool_service.config import is_remote_mode

        if not is_remote_mode():
            return False
        try:
            from src.config.settings import settings

            return "upload_data_file" in set(settings.agent_runner.heavy_tools or [])
        except Exception:
            return False

    async def _parse_sheets(self, file_path: str) -> Dict[str, Any]:
        """解析文件：remote 模式走重工具服务（大文件 pandas 解析隔离到重容器），
        其余本地线程池解析。返回 {sheets: [...], error: str|None}，
        失败口径与本地执行一致（error 为面向用户的中性文案，细节进日志）。"""
        if self._remote_dispatch_enabled():
            from src.services.heavy_tool_service import client
            from src.services.heavy_tool_service.client import HeavyToolBusy, HeavyToolUnavailable

            try:
                result = await client.execute(
                    tool="upload_data_file", handler="parse",
                    params={"file_path": os.path.abspath(file_path)},
                    timeout_seconds=600)
                return {"sheets": result.get("sheets") or [], "error": None}
            except HeavyToolBusy as e:
                # busy 透传（§8）：message 已带「稍后重试」引导，不自动立即重试
                logger.warning(f"[UploadDataFile] 重工具服务繁忙: {e}")
                return {"sheets": [], "error": str(e)}
            except HeavyToolUnavailable as e:
                # 中性失败（§8）：不回退本地执行（大文件解析正是要隔离的重负载）
                logger.error(f"[UploadDataFile] 重工具服务不可用: {e}")
                return {"sheets": [], "error": "处理未完成，请稍后重试"}
            except Exception as e:
                logger.opt(exception=True).error(f"远程解析文件失败: {e}")
                return {"sheets": [], "error": f"解析文件失败: {str(e)}"}

        from src.services.data_analysis.sheet_parser import SheetParser
        sheet_parser = SheetParser()
        try:
            return {"sheets": await asyncio.to_thread(sheet_parser.parse_file, file_path),
                    "error": None}
        except Exception as e:
            logger.opt(exception=True).error(f"解析文件失败: {e}")
            return {"sheets": [], "error": f"解析文件失败: {str(e)}"}

    async def execute(self, **kwargs) -> Dict[str, Any]:
        file_path = kwargs["file_path"]
        analysis_intent = kwargs.get("analysis_intent", "")

        # 1. 验证文件
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in (".xlsx", ".xls", ".csv"):
            return {"success": False, "error": f"不支持的文件格式: {ext}，仅支持 .xlsx、.xls、.csv"}

        # 2. 获取 tenant_id
        tenant_id = None
        try:
            from src.saas.context import get_current_tenant_id
            tenant_id = get_current_tenant_id()
        except Exception:
            pass

        # 3. 解析文件（2026-11-13 重工具服务化 §3：解析远程（纯 pandas 计算），
        # DB 注册留本地（轻操作）；HEAVY_TOOLS_MODE=local 时原样本地解析）
        parsed = await self._parse_sheets(file_path)
        if parsed.get("error"):
            return {"success": False, "error": parsed["error"]}
        sheets = parsed.get("sheets") or []

        if not sheets:
            return {"success": False, "error": "文件中没有找到有效的数据表"}

        # 4. 推断 schema 并保存到知识库
        from src.services.data_analysis.schema_extractor import SchemaExtractor
        from src.services.data_analysis.schema_saver import save_schema_to_knowledge

        schema_extractor = SchemaExtractor()

        # 用文件名（不含扩展名）作为 table_name_hint
        file_basename = os.path.splitext(os.path.basename(file_path))[0]

        registered_tables = []
        errors = []

        for sheet_info in sheets:
            sheet_name = sheet_info["sheet_name"]

            try:
                # 推断 schema
                table_name_hint = f"{file_basename}_{sheet_name}" if len(sheets) > 1 else file_basename
                # extract_schema 是 async 函数，必须直接 await；
                # 不能用 asyncio.to_thread（只会拿到 coroutine 对象，后续 .get 必抛 AttributeError）
                schema = await schema_extractor.extract_schema(
                    sheet_info=sheet_info,
                    table_name_hint=table_name_hint,
                )

                table_name = schema.get("table_name", table_name_hint)
                description = schema.get("description", "")
                columns = schema.get("columns", [])

                # 保存到知识库
                source_info = f"file:{file_path}"
                result = await save_schema_to_knowledge(
                    tenant_id=tenant_id,
                    table_name=table_name,
                    description=description,
                    columns=columns,
                    source_info=source_info,
                    source="chat-attachment",
                )

                if result["success"]:
                    registered_tables.append({
                        "sheet_name": sheet_name,
                        "table_name": table_name,
                        "rows": sheet_info.get("rows", 0),
                        "columns": [col.get("name", "") for col in columns],
                        "doc_id": result.get("doc_id"),
                    })
                else:
                    errors.append(f"Sheet '{sheet_name}' 保存失败: {result.get('error', '')}")

            except Exception as e:
                logger.opt(exception=True).error(f"处理 sheet '{sheet_name}' 失败: {e}")
                errors.append(f"Sheet '{sheet_name}' 处理失败: {str(e)}")

        if not registered_tables:
            return {
                "success": False,
                "error": "所有 sheet 均注册失败",
                "details": errors,
            }

        tables_summary = ", ".join(t["table_name"] for t in registered_tables)
        message = f"已成功注册 {len(registered_tables)} 个数据表: {tables_summary}"
        if errors:
            message += f"；{len(errors)} 个失败"

        return {
            "success": True,
            "tables": registered_tables,
            "message": message,
            "errors": errors if errors else None,
        }
