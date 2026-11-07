"""image_render AI 出图工具 — DashScope qwen-image-3.0 系列通用入口。

有参考图（1-3 张）= 图生图/edit；无参考图 = 纯文生图。生成图按张计费
（token_cost_prices.price_per_call × image_usage_factor），生成图注册为租户
图片资产并随工具结果 images 键推送（agent_events 自动发 SSE images 事件）。
"""

import asyncio
import base64
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools._helpers import sanitize_error

# src.services / src.core.image_asset 的导入放到 execute() 内部做函数级延迟导入，
# 避免 import 本工具模块即拉起 src.services/src.core 链（包初始化副作用规范）


class ImageRenderInputModel(BaseModel):
    prompt: str = Field(..., description="出图提示词：包含空间描述、构图/机位硬约束、材质与颜色锁定（精确 HEX）等完整指令")
    negative_prompt: Optional[str] = Field(None, description="负向提示词：不希望出现的元素（如水印、文字、变形、多余隔墙等）")
    reference_images: Optional[List[str]] = Field(
        None,
        description="参考图列表（1-3 张，顺序即语义顺序：材质锚点在前、布局锚点在后）。"
                    "支持 file_id（file_xxx）/ 本地绝对路径 / http(s) URL；为空则纯文生图"
    )
    size: Optional[str] = Field(None, description="图片尺寸，宽*高（如 1280*960）；缺省用平台默认")
    n: int = Field(1, description="生成张数 1-4，按张计费；默认 1 张")
    seed: Optional[int] = Field(None, description="随机种子；同一 seed+prompt 可复现，调优对比时使用")


class ImageRenderTool(BaseTool):
    name = "image_render"
    display_name = "AI图片生成"
    description = (
        "AI 图像生成：按提示词生成图片；提供 1-3 张参考图时做图生图（如产品图叠加户型图渲染效果图）"
        "（产品材质/造型还原、户型空间还原等）。生成图自动交付到会话并按张计费。"
    )
    InputModel = ImageRenderInputModel

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.tools.context import current_tool_execution_context
        from src.services.image_gen import get_image_gen_service
        from src.services.image_gen.base import ImageGenRequest, ImageGenProviderError

        ctx = current_tool_execution_context()
        tenant_id = ctx.tenant_id if ctx else None
        user_id = ctx.user_id if ctx else None
        session_id = ctx.session_id if ctx else None
        if not tenant_id:
            return {"success": False, "error": "缺少租户上下文，无法生成图片"}

        prompt = (kwargs.get("prompt") or "").strip()
        if not prompt:
            return {"success": False, "error": "提示词（prompt）不能为空"}
        ref_inputs = kwargs.get("reference_images") or []
        if len(ref_inputs) > 3:
            return {"success": False, "error": "参考图最多 3 张，请精简后重试"}
        n = int(kwargs.get("n") or 1)
        if not 1 <= n <= 4:
            return {"success": False, "error": "生成张数 n 仅支持 1-4"}

        try:
            data_urls = await self._resolve_reference_images(ref_inputs, tenant_id, user_id)
        except ValueError as e:
            # 自抛的参数校验消息（绝对路径/文件不存在/file_id 未找到），安全可透传
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"参考图解析失败：{sanitize_error(str(e))}"}

        req = ImageGenRequest(
            prompt=prompt,
            negative_prompt=kwargs.get("negative_prompt"),
            reference_images_data_urls=data_urls,
            size=kwargs.get("size"),
            n=n,
            seed=kwargs.get("seed"),
        )

        try:
            service = get_image_gen_service()
            image_refs, meta = await service.render(
                req, tenant_id=tenant_id, user_id=user_id, session_id=session_id
            )
        except ImageGenProviderError as e:
            # provider 错误消息是本项目构造的受控中文文本（含 task_id/超时上限/最后状态等
            # 诊断信息），直接透传；经 sanitize_error 无白名单兜底会把真实原因全部掩盖
            # （2026-11-06 产品效果图事故：轮询超时在 trace 中只显示「操作失败」）
            logger.opt(exception=True).warning(f"[image_render] provider 返回失败: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.opt(exception=True).error(f"[image_render] 生成异常: {e}")
            return {
                "success": False,
                "error": "图片生成失败，请稍后重试",
                "debug": sanitize_error(str(e))[:500],
            }

        succeeded = len(image_refs)
        await self._record_billing(images=succeeded, model=meta.get("model"), tenant_id=tenant_id,
                                   user_id=user_id, session_id=session_id)

        return {
            "success": True,
            "images": [ref.model_dump(exclude_none=True) for ref in image_refs],
            "images_count": succeeded,
            "requested_n": meta.get("requested_n"),
            "model": meta.get("model"),
            "task_id": meta.get("task_id"),
        }

    # ---------------- 参考图解析 ----------------

    async def _resolve_reference_images(self, ref_inputs: List[str], tenant_id: str,
                                        user_id: Optional[str]) -> List[str]:
        """把 file_id / 本地路径 / URL 统一解析为 base64 data URL（顺序保持）。"""
        from src.core.image_asset import get_image_registry

        registry = get_image_registry()
        data_urls: List[str] = []
        for item in ref_inputs:
            item = (item or "").strip()
            if not item:
                continue
            local_path: Optional[Path] = None
            if item.startswith("http://") or item.startswith("https://"):
                ref = await registry.fetch_to_local(item, tenant_id, user_id=user_id)
                local_path = await registry.resolve_local_path(ref)
            elif item.startswith("file_"):
                ref = await registry.get_ref_by_file_id(item)
                if not ref:
                    raise ValueError(f"参考图 file_id 未找到: {item}")
                local_path = await registry.resolve_local_path(ref)
            else:
                path = Path(item)
                if not path.is_absolute():
                    raise ValueError(f"参考图路径必须为绝对路径: {item}")
                from src.tools.image._path_guard import guard_local_image_path
                local_path = guard_local_image_path(path, tenant_id)
            if not local_path or not local_path.exists():
                raise ValueError(f"参考图文件不存在: {item}")
            data_urls.append(await asyncio.to_thread(self._to_data_url, local_path))
        return data_urls

    @staticmethod
    def _to_data_url(path: Path) -> str:
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/png"
        data = path.read_bytes()
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    # ---------------- 计费（billing_audit.md §4.4 模式） ----------------

    async def _record_billing(self, images: int, model: Optional[str], tenant_id: str,
                              user_id: Optional[str], session_id: Optional[str]):
        """按成功张数计费：有会话记录累加；无记录（后台/无会话上下文）独立落账。"""
        if not images or images <= 0:
            return
        model = model or "qwen-image-3.0-pro"
        try:
            from src.services.session_record import SessionRecordManager
            record = SessionRecordManager.get_current_record()
            if record:
                record.add_image_usage(images=images, model=model)
                return
        except Exception:
            pass
        # 独立落账含 psycopg2 查询/写入，to_thread 防阻塞事件循环（假异步规范）
        await asyncio.to_thread(
            self._record_billing_standalone, images, model, tenant_id, user_id, session_id
        )

    def _record_billing_standalone(self, images: int, model: str, tenant_id: str,
                                   user_id: Optional[str], session_id: Optional[str]):
        try:
            from src.db.models import ChatRecordDB
            from src.services.billing import calculate_image_credit_cost_with_breakdown
            credit_cost, breakdown = calculate_image_credit_cost_with_breakdown(
                images=images, model=model)
            ChatRecordDB.create(
                session_id=session_id or f"tool_billing_{os.urandom(6).hex()}",
                tenant_id=tenant_id,
                user_id=user_id,
                user_message="[工具计费] image_render 图像生成",
                source_type="tool_billing",
                credit_cost=float(credit_cost),
                usage_breakdown={"image": {"images": images, "model": model, **breakdown}},
            )
        except Exception as e:
            # 计费落库失败不阻断交付，只记错误日志（出图已无法撤回）
            from loguru import logger
            logger.opt(exception=True).error(f"[image_render] 独立计费落库失败: {e}")
