"""视频创作智能体服务层

设计依据：docs/plans/plan-video-agent-phase1.md §3.2 / §3.4 / §3.5
关联设计文档：§5.3.4 全节

核心方法：
- create_video_chat_session: 创建 chat_sessions 记录（subagent_id='video-agent'，metadata 含 video_gen_params）
- handle_user_message: 处理用户消息（精修/敏捷双模 -> 提示词引擎 -> 视频生成）
- keep_video: 留用视频（写 prompt_library.kept + work_outcomes.file）
- dislike_video: 不喜欢（写 prompt_library.blacklist）
- continue_with_video: 基于已有视频微调

计费：每次视频生成 API 调用写 chat_records（source_type=video_gen）
"""
from __future__ import annotations

import json
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.config.settings import settings
from src.db.database import get_db_connection
from src.db.models import ChatRecordDB, SessionDB, TokenCostPriceDB
from src.reports.work_outcome_db import WorkOutcomeDB
from src.services.billing import calculate_video_credit_cost
from src.video_agent.prompt_engine import PromptEngine, PromptResult, get_prompt_engine
from src.video_gen.base import VideoGenRequest
from src.video_gen.factory import build_provider


# 视频创作智能体 ID（与 subagents/video-agent/ 目录名一致）
VIDEO_AGENT_ID = "video-agent"

# 默认视频生成参数（前端未传时兜底）
DEFAULT_DURATION_SEC = 5
DEFAULT_RATIO = "9:16"
DEFAULT_RESOLUTION = "720P"
DEFAULT_AGILE_COUNT = 3

# 计费状态
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_REFUNDED = "refunded"


@dataclass
class VideoGenParams:
    """视频生成参数（存 chat_sessions.metadata.video_gen_params）"""
    mode: str = "refine"              # refine（精修）/ agile（敏捷）
    duration_sec: int = DEFAULT_DURATION_SEC
    ratio: str = DEFAULT_RATIO
    resolution: str = DEFAULT_RESOLUTION
    card_count: int = 1               # 精修固定 1；敏捷 1/2/3，默认 3

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "VideoGenParams":
        if not data:
            return cls()
        return cls(
            mode=data.get("mode", "refine"),
            duration_sec=int(data.get("duration_sec", DEFAULT_DURATION_SEC)),
            ratio=data.get("ratio", DEFAULT_RATIO),
            resolution=data.get("resolution", DEFAULT_RESOLUTION),
            card_count=int(data.get("card_count", 1)),
        )


@dataclass
class VideoCard:
    """单条视频生成结果（敏捷模式 N 条，精修模式 1 条）"""
    card_id: str                      # 卡片 ID（前端展示用）
    seed: int                         # 本次生成种子
    business_prompt: str              # 业务层提示词（中文）
    craft_prompt: str                 # 工艺层提示词
    model_params: Dict[str, Any] = field(default_factory=dict)
    provider_task_id: Optional[str] = None
    provider_status: Optional[str] = None  # PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN
    video_file_id: Optional[str] = None    # 成片 file_id（成功后回填，前端通过 /api/files/{file_id}/download 下载）
    video_url: Optional[str] = None        # provider 返回的临时 URL（24h 有效）
    duration_sec: Optional[int] = None     # 实际成片时长
    credit_cost: float = 0.0               # 本卡片消耗积分
    error: Optional[str] = None


class VideoChatService:
    """视频创作智能体会话化服务

    与 src/video_gen/service.py（MVP 抽卡式）并列：
    - video_gen：表单式工作台，gen_sessions / gen_cards 表
    - video_agent：会话化聊天，chat_sessions / work_outcomes / asset_library / prompt_library 表
    复用 video_gen 的 provider 实现（wanx_provider / minimax_provider）。
    """

    def __init__(self, prompt_engine: Optional[PromptEngine] = None) -> None:
        self._prompt_engine = prompt_engine or get_prompt_engine()
        # 复用 video_gen 的 provider 工厂（wanx / minimax）
        try:
            self._provider = build_provider(settings.video_gen, settings.llm.qwen.api_keys)
        except Exception as e:
            logger.warning(f"video_agent provider 构造失败（视频生成 API 调用将不可用）: {e}")
            self._provider = None

    # ------------------------------------------------------------------
    # 会话创建
    # ------------------------------------------------------------------
    def create_video_chat_session(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        title: str = "视频创作",
        params: Optional[VideoGenParams] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建视频创作会话（chat_sessions 记录）

        Args:
            tenant_id: 租户 ID
            user_id: 用户 ID
            title: 会话标题
            params: 视频生成参数（存 metadata.video_gen_params）

        Returns:
            会话记录 dict，失败返回 None
        """
        params = params or VideoGenParams()
        # metadata JSONB 存 video_gen_params
        metadata = {"video_gen_params": params.to_dict()}
        session = SessionDB.create(
            user_id=user_id,
            title=title,
            tenant_id=tenant_id,
            subagent_id=VIDEO_AGENT_ID,
        )
        if not session:
            logger.error(f"创建视频创作会话失败: tenant={tenant_id}, user={user_id}")
            return None
        # 写 metadata（SessionDB.create 未支持 metadata 字段，单独 UPDATE）
        self._update_session_metadata(session["session_id"], metadata)
        session["metadata"] = metadata
        logger.info(
            f"视频创作会话已创建: session_id={session['session_id']}, "
            f"mode={params.mode}, tenant={tenant_id}, user={user_id}"
        )
        return session

    def get_session_params(self, session_id: str) -> VideoGenParams:
        """读取会话的视频生成参数"""
        metadata = self._get_session_metadata(session_id)
        return VideoGenParams.from_dict((metadata or {}).get("video_gen_params"))

    # ------------------------------------------------------------------
    # 用户消息处理（精修/敏捷双模）
    # ------------------------------------------------------------------
    async def handle_user_message(
        self,
        session_id: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        user_input: str,
        image_file_ids: Optional[List[str]] = None,
        params: Optional[VideoGenParams] = None,
    ) -> Dict[str, Any]:
        """处理用户消息：识别意图（精修/敏捷）-> 调用提示词引擎 -> 提交视频生成

        Args:
            session_id: 会话 ID
            tenant_id: 租户 ID
            user_id: 用户 ID
            user_input: 用户的中文需求描述
            image_file_ids: 用户上传的参考图片 file_id 列表
            params: 视频生成参数（None 时读会话 metadata）

        Returns:
            {
                "mode": "refine" | "agile",
                "cards": List[VideoCard],
                "prompt_draft": Optional[str],  # 精修模式的提示词草稿 md（等用户确认）
                "waiting": bool,                 # 是否在等待视频生成
                "error": Optional[str],
            }
        """
        if params is None:
            params = self.get_session_params(session_id)

        image_count = len(image_file_ids or [])
        cards: List[VideoCard] = []
        prompt_draft: Optional[str] = None
        error: Optional[str] = None
        waiting = False

        try:
            if params.mode == "refine":
                # 精修模式：生成 1 段提示词，等用户确认后再提交视频模型
                result = await self._prompt_engine.generate_prompt_refine(
                    user_input=user_input,
                    image_count=image_count,
                    duration_sec=params.duration_sec,
                    ratio=params.ratio,
                    resolution=params.resolution,
                )
                prompt_draft = self._format_prompt_draft_md(result)
                # 第一阶段简化：精修模式生成提示词后直接提交视频模型（不实现"用户确认"循环）
                # 用户可在视频生成后通过 continue_with_video 微调
                card = await self._submit_video_generation(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    prompt_result=result,
                    image_file_ids=image_file_ids,
                    params=params,
                )
                cards.append(card)
                waiting = card.provider_status in ("PENDING", "RUNNING")
            elif params.mode == "agile":
                # 敏捷模式：一次生成 N 段差异化提示词，无需用户确认，直接提交 N 条
                count = max(1, min(params.card_count or DEFAULT_AGILE_COUNT, 3))
                results = await self._prompt_engine.generate_prompts_agile(
                    user_input=user_input,
                    image_count=image_count,
                    count=count,
                    duration_sec=params.duration_sec,
                    ratio=params.ratio,
                    resolution=params.resolution,
                )
                for idx, result in enumerate(results):
                    card = await self._submit_video_generation(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        session_id=session_id,
                        prompt_result=result,
                        image_file_ids=image_file_ids,
                        params=params,
                    )
                    cards.append(card)
                waiting = any(c.provider_status in ("PENDING", "RUNNING") for c in cards)
            else:
                error = f"未知创作模式: {params.mode}"
        except Exception as e:
            logger.error(f"[VideoChatService] handle_user_message 失败: {e}", exc_info=True)
            error = str(e)

        return {
            "mode": params.mode,
            "cards": [asdict(c) for c in cards],
            "prompt_draft": prompt_draft,
            "waiting": waiting,
            "error": error,
        }

    # ------------------------------------------------------------------
    # 视频生成提交（计费 + provider 调用）
    # ------------------------------------------------------------------
    async def _submit_video_generation(
        self,
        tenant_id: Optional[str],
        user_id: Optional[str],
        session_id: str,
        prompt_result: PromptResult,
        image_file_ids: Optional[List[str]],
        params: VideoGenParams,
    ) -> VideoCard:
        """提交单条视频生成任务 + 写 chat_records 计费

        流程：
        1. 生成 card_id 与 seed
        2. 写 chat_records（status=pending，预扣 credit_cost）
        3. 调 provider.submit 提交视频生成
        4. 失败时更新 chat_records.status=refunded 退还预扣
        """
        if self._provider is None:
            raise RuntimeError("视频生成 provider 未就绪（settings.video_gen 配置缺失）")

        card_id = f"card_{uuid.uuid4().hex[:12]}"
        seed = random.randint(0, 2147483647)
        # 把 seed 写入 model_params 便于溯源
        prompt_result.model_params["seed"] = seed

        # 预扣计费：按目标时长 × 单价 × factor（实际成片时长可能略短，成功后按实际结算差额）
        cost_per_second = self._get_cost_per_second(self._provider.name, settings.video_gen.provider)
        expected_credit = calculate_video_credit_cost(
            seconds=params.duration_sec,
            cost_per_second_yuan=cost_per_second,
            provider=self._provider.name,
            model=self._get_model_name(),
        )

        # 写 chat_records（pending 状态，预扣）
        record_id = self._write_chat_record(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            model=self._get_model_name(),
            provider=self._provider.name,
            execution_details={
                "seconds": params.duration_sec,
                "cost_per_second_yuan": cost_per_second,
                "card_id": card_id,
                "seed": seed,
                "resolution": params.resolution,
                "ratio": params.ratio,
            },
            credit_cost=expected_credit,
            status=STATUS_PENDING,
        )

        card = VideoCard(
            card_id=card_id,
            seed=seed,
            business_prompt=prompt_result.business_prompt,
            craft_prompt=prompt_result.craft_prompt,
            model_params=prompt_result.model_params,
            credit_cost=expected_credit,
        )

        # 构造 VideoGenRequest（参考图：image_file_ids[0]）
        reference_data_url = self._read_image_as_data_url(
            image_file_ids[0] if image_file_ids else None
        )
        first_frame_data_url = (
            self._read_image_as_data_url(image_file_ids[1]) if len(image_file_ids or []) > 1
            else reference_data_url
        )
        req = VideoGenRequest(
            prompt=prompt_result.craft_prompt,
            reference_image_data_url=reference_data_url,
            first_frame_data_url=first_frame_data_url,
            seed=seed,
            negative_prompt=prompt_result.model_params.get("negative_prompt", ""),
            duration=params.duration_sec,
            resolution=params.resolution,
            ratio=params.ratio,
        )

        try:
            submit_result = await self._provider.submit(req)
            card.provider_task_id = submit_result.task_id
            card.provider_status = submit_result.task_status or "PENDING"
            logger.info(
                f"[VideoChatService] 视频生成已提交: card_id={card_id}, "
                f"task_id={submit_result.task_id}, status={card.provider_status}"
            )
        except Exception as e:
            logger.error(f"[VideoChatService] 视频生成提交失败: card_id={card_id}, error={e}")
            card.provider_status = "FAILED"
            card.error = str(e)
            # 失败退还预扣
            if record_id:
                self._update_chat_record_status(record_id, STATUS_REFUNDED, error_message=str(e))

        return card

    # ------------------------------------------------------------------
    # 留用 / 不喜欢 / 继续
    # ------------------------------------------------------------------
    def keep_video(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        card: VideoCard,
        video_file_id: str,
        video_display_name: str,
        save_prompt_to_library: bool = True,
    ) -> Dict[str, Any]:
        """用户留用视频：写 prompt_library.kept + work_outcomes.file

        Args:
            tenant_id: 租户 ID
            user_id: 留用者
            session_id: 会话 ID
            card: 视频卡片（含提示词）
            video_file_id: 视频文件 file_id（用于下载入口）
            video_display_name: 视频显示名
            save_prompt_to_library: 是否同时收入提示词库（默认 True）

        Returns:
            {"prompt_library_id": Optional[int], "work_outcome_id": Optional[str]}
        """
        prompt_library_id: Optional[int] = None
        if save_prompt_to_library:
            prompt_library_id = self._insert_prompt_library(
                tenant_id=tenant_id,
                user_id=user_id,
                category="kept",
                business_prompt=card.business_prompt,
                craft_prompt=card.craft_prompt,
                model_params=card.model_params,
                source_video_file_id=video_file_id,
                source_chat_session_id=session_id,
            )

        # 写 work_outcomes（视频库）
        metadata = {
            "prompt_library_id": prompt_library_id,
            "generation_params": {
                "duration_sec": card.model_params.get("duration"),
                "ratio": card.model_params.get("ratio"),
                "resolution": card.model_params.get("resolution"),
                "seed": card.seed,
            },
            "credit_cost": card.credit_cost,
            "business_prompt": card.business_prompt,
            "craft_prompt": card.craft_prompt,
        }
        outcome = WorkOutcomeDB.create(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            summary=f"留用视频: {video_display_name}",
            outcome_type="file",
            subagent_id=VIDEO_AGENT_ID,
            file_id=video_file_id,
            file_name=video_display_name,
            metadata=metadata,
            source="cp_realtime",
        )
        logger.info(
            f"[VideoChatService] 视频留用入库: tenant={tenant_id}, user={user_id}, "
            f"prompt_library_id={prompt_library_id}, outcome_id={outcome.get('outcome_id')}"
        )
        return {"prompt_library_id": prompt_library_id, "work_outcome_id": outcome.get("outcome_id")}

    def dislike_video(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        card: VideoCard,
        video_file_id: str,
        dislike_reason: Optional[str] = None,
    ) -> int:
        """用户不喜欢：写 prompt_library.blacklist（可选填 dislike_reason）

        Returns:
            prompt_library.id
        """
        prompt_library_id = self._insert_prompt_library(
            tenant_id=tenant_id,
            user_id=user_id,
            category="blacklist",
            business_prompt=card.business_prompt,
            craft_prompt=card.craft_prompt,
            model_params=card.model_params,
            source_video_file_id=video_file_id,
            source_chat_session_id=session_id,
            dislike_reason=dislike_reason,
        )
        logger.info(
            f"[VideoChatService] 视频不喜欢入库: tenant={tenant_id}, user={user_id}, "
            f"prompt_library_id={prompt_library_id}, reason={dislike_reason}"
        )
        return prompt_library_id

    async def continue_with_video(
        self,
        session_id: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        user_input: str,
        source_video_file_id: str,
        source_card: VideoCard,
        params: Optional[VideoGenParams] = None,
    ) -> Dict[str, Any]:
        """基于已有视频微调（混合使用场景）

        Args:
            session_id: 会话 ID
            tenant_id: 租户 ID
            user_id: 用户 ID
            user_input: 微调描述（如"基于上一条改成横版"）
            source_video_file_id: 源视频 file_id
            source_card: 源视频卡片（提供原始提示词基础）
            params: 视频生成参数（None 时读会话 metadata）
        """
        if params is None:
            params = self.get_session_params(session_id)
        # 把"基于上一条 + 用户微调"作为新的用户输入送入精修模式
        combined_input = f"基于上一条提示词（{source_card.business_prompt}）做以下调整：{user_input}"
        result = await self._prompt_engine.generate_prompt_refine(
            user_input=combined_input,
            image_count=0,
            duration_sec=params.duration_sec,
            ratio=params.ratio,
            resolution=params.resolution,
        )
        # 溯源：在 model_params 中记录 source_video_file_id
        result.model_params["source_video_file_id"] = source_video_file_id
        card = await self._submit_video_generation(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            prompt_result=result,
            image_file_ids=None,
            params=params,
        )
        return {
            "mode": "refine",
            "cards": [asdict(card)],
            "prompt_draft": self._format_prompt_draft_md(result),
            "waiting": card.provider_status in ("PENDING", "RUNNING"),
            "error": None,
        }

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _get_model_name(self) -> str:
        """当前 provider 使用的视频模型名"""
        if self._provider is None:
            return ""
        if self._provider.name == "wanx":
            return settings.video_gen.wanx.model or "wan2.7-r2v"
        if self._provider.name == "minimax":
            return settings.video_gen.minimax.model or "MiniMax-H3"
        return ""

    def _get_cost_per_second(self, provider_name: str, cfg_provider: str) -> float:
        """从 token_cost_prices 查视频模型按秒单价"""
        model_name = self._get_model_name()
        if not model_name:
            return 0.0
        try:
            tcp = TokenCostPriceDB.get_by_model_name(model_name)
            if tcp and tcp.get("price_per_second") is not None:
                return float(tcp["price_per_second"])
        except Exception as e:
            logger.warning(f"查询视频模型 {model_name} 单价失败: {e}")
        return 0.0

    def _read_image_as_data_url(self, file_id: Optional[str]) -> Optional[str]:
        """从 uploaded_file:{file_id} Redis 协议读取图片并转 base64 data URL"""
        if not file_id:
            return None
        try:
            from src.video_gen.media import MediaRegistry
            return MediaRegistry.read_as_base64(file_id)
        except Exception as e:
            logger.warning(f"读取图片 file_id={file_id} 转 base64 失败: {e}")
            return None

    def _format_prompt_draft_md(self, result: PromptResult) -> str:
        """把 PromptResult 格式化为 Markdown 提示词草稿（精修模式推送给用户确认）"""
        lines = [
            "## 提示词草稿",
            "",
            "### 业务层（员工可读）",
            result.business_prompt,
            "",
            "### 工艺层（可灵 8 层框架）",
            "```",
            result.craft_prompt,
            "```",
            "",
            "### 模型参数",
        ]
        for k, v in result.model_params.items():
            lines.append(f"- **{k}**: {v}")
        lines.extend([
            "",
            "---",
            "请回复「确认」直接生成视频，或描述需要调整的内容（如「改成横版」「换暖色调」）。",
        ])
        return "\n".join(lines)

    def _write_chat_record(
        self,
        session_id: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        model: str,
        provider: str,
        execution_details: Dict[str, Any],
        credit_cost: float,
        status: str,
    ) -> Optional[int]:
        """写 chat_records 计费记录（source_type=video_gen）

        Returns:
            chat_records.id，失败返回 None
        """
        try:
            record = ChatRecordDB.create(
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                model=model,
                provider=provider,
                execution_details=execution_details,
                status=status,
                source_type="video_gen",
                credit_cost=credit_cost,
            )
            return record.get("id") if record else None
        except Exception as e:
            logger.error(f"[VideoChatService] 写 chat_records 失败: {e}", exc_info=True)
            return None

    def _update_chat_record_status(
        self,
        record_id: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """更新 chat_records 状态（completed/refunded）

        status=refunded 时同事务退还租户余额（与 ChatRecordDB.create 的扣减对称）。
        ChatRecordDB.create 在创建记录时已原子扣减 tenants.credit_balance，
        若 provider 提交失败必须在此处对称退还，否则租户被扣积分却无视频产出。
        """
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # 退还积分：仅 status=refunded 时；先查原记录的 tenant_id 与 credit_cost
                tenant_id_to_invalidate: Optional[str] = None
                if status == STATUS_REFUNDED:
                    cursor.execute(
                        "SELECT tenant_id, credit_cost FROM chat_records WHERE id = %s",
                        (record_id,),
                    )
                    row = cursor.fetchone()
                    if row and row.get("tenant_id") and (row.get("credit_cost") or 0) > 0:
                        cursor.execute(
                            "UPDATE tenants SET credit_balance = credit_balance + %s WHERE tenant_id = %s",
                            (row["credit_cost"], row["tenant_id"]),
                        )
                        tenant_id_to_invalidate = row["tenant_id"]
                if error_message:
                    cursor.execute(
                        "UPDATE chat_records SET status = %s, error_message = %s WHERE id = %s",
                        (status, error_message, record_id),
                    )
                else:
                    cursor.execute(
                        "UPDATE chat_records SET status = %s WHERE id = %s",
                        (status, record_id),
                    )
                conn.commit()
            # 退还后失效租户缓存，确保下次入口拦截读到最新余额
            if tenant_id_to_invalidate:
                try:
                    from src.core.cache_utils import invalidate_tenant_cache
                    invalidate_tenant_cache(tenant_id_to_invalidate)
                except Exception as cache_err:
                    logger.warning(f"退还积分后失效租户缓存失败: {cache_err}")
        except Exception as e:
            logger.error(f"[VideoChatService] 更新 chat_records 状态失败: {e}", exc_info=True)

    def _insert_prompt_library(
        self,
        tenant_id: str,
        user_id: str,
        category: str,
        business_prompt: str,
        craft_prompt: str,
        model_params: Dict[str, Any],
        source_video_file_id: Optional[str] = None,
        source_chat_session_id: Optional[str] = None,
        dislike_reason: Optional[str] = None,
    ) -> int:
        """插入 prompt_library 记录"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO prompt_library
                    (tenant_id, user_id, category, business_prompt, craft_prompt,
                     model_params, source_video_file_id, source_chat_session_id, dislike_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    tenant_id, user_id, category, business_prompt, craft_prompt,
                    json.dumps(model_params, ensure_ascii=False),
                    source_video_file_id, source_chat_session_id, dislike_reason,
                ),
            )
            row = cursor.fetchone()
            conn.commit()
            return row["id"] if row else 0

    def _update_session_metadata(self, session_id: str, metadata: Dict[str, Any]) -> None:
        """更新 chat_sessions.metadata JSONB"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE chat_sessions SET metadata = %s WHERE session_id = %s",
                    (json.dumps(metadata, ensure_ascii=False), session_id),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"更新 chat_sessions.metadata 失败: session_id={session_id}, error={e}")

    def _get_session_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """读取 chat_sessions.metadata JSONB"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT metadata FROM chat_sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = cursor.fetchone()
                if row and row.get("metadata"):
                    md = row["metadata"]
                    if isinstance(md, str):
                        return json.loads(md)
                    return md
                return None
        except Exception as e:
            logger.error(f"读取 chat_sessions.metadata 失败: session_id={session_id}, error={e}")
            return None


# 模块级单例（惰性，避免 import 副作用）
_video_chat_service_instance: Optional[VideoChatService] = None


def get_video_chat_service() -> VideoChatService:
    """返回 VideoChatService 单例"""
    global _video_chat_service_instance
    if _video_chat_service_instance is None:
        _video_chat_service_instance = VideoChatService()
    return _video_chat_service_instance
