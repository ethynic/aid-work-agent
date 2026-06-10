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

        # 3. 解析文件
        from src.services.data_analysis.sheet_parser import SheetParser
        sheet_parser = SheetParser()

        try:
            sheets = await asyncio.to_thread(sheet_parser.parse_file, file_path)
        except Exception as e:
            logger.error(f"解析文件失败: {e}", exc_info=True)
            return {"success": False, "error": f"解析文件失败: {str(e)}"}

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
                schema = await asyncio.to_thread(
                    schema_extractor.extract_schema,
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
                logger.error(f"处理 sheet '{sheet_name}' 失败: {e}", exc_info=True)
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
