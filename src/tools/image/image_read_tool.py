"""image_read 图片判读工具 — VL 多模态读图通用入口。

把图片交给视觉模型判读（户型结构判读、产品颜色/材质判读、生成结果择优等），
经 llm_gateway.chat_direct 指定 provider/model 通道直连，不随主链路漂移。
返回 answer + 本地 file_path：agent 拿 file_path 后可用 skill_execute 跑技能
脚本（如 interior-render 的 sample_color.py 取色），file_id 无法直接喂脚本。
"""

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools._helpers import sanitize_error

# src.services / src.llm 延迟导入，避免模块顶层拉起依赖链


class ImageReadInputModel(BaseModel):
    image: str = Field(..., description="待判读图片：file_id（file_xxx）/ 本地绝对路径 / http(s) URL")
    question: str = Field(..., description="判读问题，要求结构化输出（如：数清深色墙围合的房间数与方位；给出产品主体色的 HEX 值）")
    model: Optional[str] = Field(None, description="判读模型，provider/model 语法（如 qwen/qwen3-vl-plus）；缺省用平台默认，显式指定须在白名单内")


class ImageReadTool(BaseTool):
    name = "image_read"
    display_name = "图片判读"
    description = (
        "多模态读图：把图片交给视觉模型判读并返回文字结论"
        "（结构判读、颜色/材质判读、生成图质量评估等）。同时返回图片本地路径，"
        "供后续 skill_execute 脚本处理（取色等）。"
    )
    InputModel = ImageReadInputModel

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.tools.context import current_tool_execution_context

        ctx = current_tool_execution_context()
        tenant_id = ctx.tenant_id if ctx else None
        user_id = ctx.user_id if ctx else None
        if not tenant_id:
            return {"success": False, "error": "缺少租户上下文，无法判读图片"}

        image_input = (kwargs.get("image") or "").strip()
        question = (kwargs.get("question") or "").strip()
        if not image_input:
            return {"success": False, "error": "待判读图片（image）不能为空"}
        if not question:
            return {"success": False, "error": "判读问题（question）不能为空"}

        try:
            local_path, b64 = await self._resolve_image(image_input, tenant_id, user_id)
        except ValueError as e:
            # 自抛的参数校验消息（绝对路径/file_id 未找到），安全可透传
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"图片解析失败：{sanitize_error(str(e))}"}

        try:
            provider_name, model = self._resolve_model(kwargs.get("model"))
            answer = await self._call_vl(provider_name, model, question, b64)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            # 兜底文案进对话上下文，真实异常必须落错误日志（否则上游模型 404 等问题无法排查）
            from loguru import logger

            logger.opt(exception=True).error(
                f"[image_read] VL 判读失败: image={image_input[:80]}, model={kwargs.get('model') or 'default'}: {e}"
            )
            return {
                "success": False,
                "error": "图片判读失败，请稍后重试",
                "debug": sanitize_error(str(e))[:500],
            }

        return {
            "success": True,
            "answer": answer,
            "model": f"{provider_name}/{model}",
            "file_path": str(local_path),
        }

    # ---------------- 图片解析 ----------------

    async def _resolve_image(self, image_input: str, tenant_id: str,
                             user_id: Optional[str]):
        """把 file_id / 本地路径 / URL 统一解析为 (本地路径, base64)。"""
        from src.core.image_asset import get_image_registry

        registry = get_image_registry()
        local_path: Optional[Path] = None
        if image_input.startswith("http://") or image_input.startswith("https://"):
            ref = await registry.fetch_to_local(image_input, tenant_id, user_id=user_id)
            local_path = await registry.resolve_local_path(ref)
        elif image_input.startswith("file_"):
            ref = await registry.get_ref_by_file_id(image_input)
            if not ref:
                raise ValueError(f"图片 file_id 未找到: {image_input}")
            local_path = await registry.resolve_local_path(ref)
        else:
            path = Path(image_input)
            if not path.is_absolute():
                raise ValueError(f"图片路径必须为绝对路径: {image_input}")
            from src.tools.image._path_guard import guard_local_image_path
            local_path = guard_local_image_path(path, tenant_id)
        if not local_path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_input}")
        mime, _ = mimetypes.guess_type(str(local_path))
        mime = mime or "image/png"
        data = await asyncio.to_thread(local_path.read_bytes)
        b64 = base64.b64encode(data).decode("ascii")
        return local_path, f"data:{mime};base64,{b64}"

    # ---------------- 模型解析与 VL 调用 ----------------

    def _resolve_model(self, model_param: Optional[str]):
        """白名单校验（仿 resume_vl_service.resolve_model_spec）。

        缺省用 settings.image_gen.vl.model；显式指定等于默认的免白名单，
        其他必须命中 allowed_models（防「指定贵模型绕白名单」）。
        """
        from src.config.settings import create_settings

        vl_cfg = create_settings().image_gen.vl
        spec = (model_param or "").strip() or vl_cfg.model
        provider, sep, model = spec.partition("/")
        if not sep or not provider.strip() or not model.strip():
            raise ValueError(f"模型标识 {spec!r} 格式非法：须为 provider/model（如 qwen/qwen3-vl-plus）")
        if spec != vl_cfg.model and vl_cfg.allowed_models and spec not in vl_cfg.allowed_models:
            raise ValueError(f"判读模型 {spec!r} 不在白名单内，可用：{vl_cfg.allowed_models}")
        return provider.strip(), model.strip()

    async def _call_vl(self, provider_name: str, model: str, question: str, data_url: str) -> str:
        from src.llm.gateway import llm_gateway
        from src.services.session_record import SessionRecordManager

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }]
        # 温度 0.1：判读任务要确定性（与 resume_vl / recruiting 同取向）
        response = await llm_gateway.chat_direct(
            provider_name, model, messages=messages, temperature=0.1
        )
        # chat_direct 不经自动记账（gateway 通路③ no-op），VL token 手动累加
        usage = response.get("usage") if isinstance(response, dict) else None
        if isinstance(usage, dict) and (usage.get("prompt_tokens") or usage.get("completion_tokens")):
            try:
                record = SessionRecordManager.get_current_record()
                if record:
                    record.add_llm_usage(usage)
            except Exception as e:
                from loguru import logger

                logger.debug(f"[image_read] VL 用量记账失败: {e}")
        answer = (response.get("content") or "").strip() if isinstance(response, dict) else ""
        if not answer:
            raise ValueError("视觉模型未返回判读结果")
        return answer
