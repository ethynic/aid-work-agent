"""视频生成 Service 层。

串联 db / wanx_provider / media / preprocess / scenes，实现抽卡式工具的完整业务逻辑：
- create_session: 创建会话 + 人脸裁剪产品图 + 向万相提交 N 条任务（不同 seed 差异化）
- poll_pending_cards: 后台轮询，SUCCEEDED 下载成片（含烧录 AI 标识）+ 注册 file_id
- get_session / list_sessions / set_card_kept / regenerate_card: 会话与卡片管理

设计依据：docs/system/content-production/mvp-design.md §8。
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src.config.settings import settings
from src.db.database import get_db_connection
from src.video_gen.media import MediaRegistry
from src.video_gen.scenes import get_scene, list_scenes as _list_scene_presets
from src.video_gen.wanx_provider import WanxProvider, WanxProviderError

# 万相 task 查询有效期（与 settings.llm.wanx.task_max_age_hours 对齐）
_TASK_MAX_AGE = timedelta(hours=settings.llm.wanx.task_max_age_hours)

# card 终态集合（轮询时跳过）
_TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class VideoGenService:
    def __init__(self) -> None:
        api_key = settings.llm.wanx.api_key or (
            settings.llm.qwen.api_keys[0] if settings.llm.qwen.api_keys else ""
        )
        self.wanx = WanxProvider(api_key=api_key, model=settings.llm.wanx.model)
        self.media = MediaRegistry()

    # ------------------------------------------------------------------
    # 场景
    # ------------------------------------------------------------------
    def list_scenes(self) -> list[dict[str, Any]]:
        """返回可用场景列表（给前端下拉）。"""
        return [
            {"scene_id": s.scene_id, "name": s.name, "description": s.description}
            for s in _list_scene_presets()
        ]

    # ------------------------------------------------------------------
    # 创建抽卡会话
    # ------------------------------------------------------------------
    async def create_session(
        self,
        tenant_id: str | None,
        user_id: str | None,
        scene_id: str,
        product_image_fid: str,
        copywriting: str,
        card_count: int = 3,
        expanded_prompt: str | None = None,
    ) -> dict[str, Any]:
        """创建抽卡会话 + 立即向万相提交 card_count 条任务。

        流程（mvp-design.md §8.4）：
        1. 校验 scene_id
        2. 读用户上传的产品图 base64（spike 验证万相支持 base64 直传，§1.1）
        3. card_count 个不同 seed，各调 wanx.submit
        4. 写 gen_sessions + gen_cards

        注：不做程序侧素材预处理，由用户自行上传正确比例的素材（如竖屏 9:16）。
        """
        # 1. 校验场景
        scene = get_scene(scene_id)
        if scene is None:
            raise ValueError(f"未知场景: {scene_id}")
        if not 2 <= card_count <= 4:
            raise ValueError("card_count 必须为 2-4")

        # 2. 提示词：expanded_prompt 为空则用场景模板填空（极简提示词引擎，§6）
        prompt = expanded_prompt or scene.prompt_template.format(copywriting=copywriting)

        # 3. 读用户上传的产品图 base64（spike 验证万相支持 base64，§1.1）
        image_data_url = MediaRegistry.read_as_base64(product_image_fid)

        # 5. 写会话 + 提交 N 条任务（不同 seed 差异化）
        session_id = new_id("sess")
        base_seed = random.randint(0, 2147483647)

        cards: list[dict[str, Any]] = []
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO gen_sessions
                   (session_id, tenant_id, user_id, scene_id, product_image_fid,
                    copywriting, expanded_prompt, card_count, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'generating')""",
                (session_id, tenant_id, user_id, scene_id, product_image_fid,
                 copywriting, prompt, card_count),
            )

            for idx in range(card_count):
                seed = (base_seed + idx) & 0x7FFFFFFF   # 正整数差异化 seed
                card_id = new_id("card")
                provider_task_id = None
                provider_status = "PENDING"
                error_msg = None
                try:
                    submit_result = await self.wanx.submit(
                        prompt=prompt,
                        product_image_data_url=image_data_url,
                        seed=seed,
                        negative_prompt=scene.negative_prompt,
                        duration=scene.default_duration,
                    )
                    provider_task_id = submit_result.task_id
                    provider_status = submit_result.task_status
                except WanxProviderError as exc:
                    error_msg = str(exc)
                    provider_status = "FAILED"
                    logger.error(f"视频生成 card 提交失败 session={session_id} idx={idx}: {exc}")
                except Exception as exc:
                    # 兜底非预期异常（如万相返回非 JSON），防止单条失败导致整批 session 回滚丢失
                    error_msg = f"提交异常: {exc}"
                    provider_status = "FAILED"
                    logger.error(f"视频生成 card 提交非预期异常 session={session_id} idx={idx}: {exc}", exc_info=True)

                cur.execute(
                    """INSERT INTO gen_cards
                       (card_id, tenant_id, session_id, variant_idx, seed, variant_prompt,
                        provider_task_id, provider_status, error_msg)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (card_id, tenant_id, session_id, idx, seed, prompt,
                     provider_task_id, provider_status, error_msg),
                )
                cards.append({
                    "card_id": card_id, "session_id": session_id, "variant_idx": idx,
                    "seed": seed, "variant_prompt": prompt,
                    "provider_task_id": provider_task_id, "provider_status": provider_status,
                    "output_fid": None, "output_duration": None,
                    "kept": False, "error_msg": error_msg,
                })

            conn.commit()

        logger.info(f"视频生成: 创建会话 {session_id} 场景={scene_id} 条数={card_count}")
        return {
            "session_id": session_id, "scene_id": scene_id,
            "scene_name": scene.name, "copywriting": copywriting,
            "expanded_prompt": prompt, "card_count": card_count,
            "status": "generating", "cards": cards,
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_session(self, tenant_id: str | None, session_id: str) -> dict[str, Any] | None:
        """查会话 + 其下所有 cards 状态。"""
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT session_id, tenant_id, scene_id, product_image_fid, copywriting,
                          expanded_prompt, card_count, status, created_at
                   FROM gen_sessions
                   WHERE session_id = %s AND tenant_id IS NOT DISTINCT FROM %s""",
                (session_id, tenant_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            session = dict(row)
            cur.execute(
                """SELECT card_id, session_id, variant_idx, seed, variant_prompt,
                          provider_task_id, provider_status, output_fid, output_duration,
                          kept, parent_card_id, error_msg, created_at
                   FROM gen_cards WHERE session_id = %s ORDER BY variant_idx ASC""",
                (session_id,),
            )
            session["cards"] = [dict(r) for r in cur.fetchall()]
        return self._serialize_session(session)

    def list_sessions(self, tenant_id: str | None, limit: int = 20) -> list[dict[str, Any]]:
        """会话历史列表。"""
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT session_id, tenant_id, scene_id, product_image_fid, copywriting,
                          expanded_prompt, card_count, status, created_at
                   FROM gen_sessions
                   WHERE tenant_id IS NOT DISTINCT FROM %s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
            sessions = [dict(r) for r in cur.fetchall()]
        return [self._serialize_session(s) for s in sessions]

    # ------------------------------------------------------------------
    # 留用 / 重新生成
    # ------------------------------------------------------------------
    def get_card_output_fid(self, tenant_id: str | None, card_id: str) -> str | None:
        """查 card 的成片 file_id（给下载 URL 端点用）。无成片返回 None。"""
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT output_fid FROM gen_cards
                   WHERE card_id = %s AND tenant_id IS NOT DISTINCT FROM %s""",
                (card_id, tenant_id),
            )
            row = cur.fetchone()
        return dict(row).get("output_fid") if row else None

    def set_card_kept(self, tenant_id: str | None, card_id: str, kept: bool) -> dict[str, Any]:
        """标记 card 留用/取消留用。"""
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE gen_cards SET kept = %s, updated_at = NOW()
                   WHERE card_id = %s AND tenant_id IS NOT DISTINCT FROM %s""",
                (kept, card_id, tenant_id),
            )
            if cur.rowcount == 0:
                conn.rollback()
                raise ValueError("卡片不存在或无权限")
            conn.commit()
        return {"card_id": card_id, "kept": kept}

    async def regenerate_card(
        self,
        tenant_id: str | None,
        card_id: str,
        prompt_override: str | None = None,
        seed_override: int | None = None,
    ) -> dict[str, Any]:
        """重新生成式编辑：基于某张 card 的 session，用新 prompt/seed 重新提交一条任务。

        新 card 的 parent_card_id = 原 card_id，复用原 session 的首帧（裁剪后的产品图）。
        """
        # 取原 card 与 session
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT c.card_id, c.session_id, c.tenant_id, c.variant_prompt, c.seed,
                          s.scene_id, s.expanded_prompt, s.product_image_fid
                   FROM gen_cards c
                   JOIN gen_sessions s ON c.session_id = s.session_id
                   WHERE c.card_id = %s AND c.tenant_id IS NOT DISTINCT FROM %s""",
                (card_id, tenant_id),
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError("卡片不存在或无权限")
        data = dict(row)

        scene = get_scene(data["scene_id"])
        prompt = prompt_override or data["variant_prompt"] or data["expanded_prompt"]
        seed = seed_override if seed_override is not None else random.randint(0, 2147483647)

        image_data_url = MediaRegistry.read_as_base64(data["product_image_fid"])
        new_card_id = new_id("card")
        negative_prompt = scene.negative_prompt if scene else ""

        provider_task_id = None
        provider_status = "PENDING"
        error_msg = None
        try:
            submit_result = await self.wanx.submit(
                prompt=prompt,
                product_image_data_url=image_data_url,
                seed=seed,
                negative_prompt=negative_prompt,
                duration=scene.default_duration if scene else 5,
            )
            provider_task_id = submit_result.task_id
            provider_status = submit_result.task_status
        except WanxProviderError as exc:
            error_msg = str(exc)
            provider_status = "FAILED"
            logger.error(f"视频生成 regenerate 提交失败 parent={card_id}: {exc}")

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO gen_cards
                   (card_id, tenant_id, session_id, variant_idx, seed, variant_prompt,
                    provider_task_id, provider_status, error_msg, parent_card_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (new_card_id, data["tenant_id"], data["session_id"], 99, seed, prompt,
                 provider_task_id, provider_status, error_msg, card_id),
            )
            conn.commit()

        return {
            "card_id": new_card_id, "session_id": data["session_id"],
            "parent_card_id": card_id, "seed": seed, "variant_prompt": prompt,
            "provider_task_id": provider_task_id, "provider_status": provider_status,
            "output_fid": None, "kept": False, "error_msg": error_msg,
        }

    # ------------------------------------------------------------------
    # 后台轮询（scheduler job 调用）
    # ------------------------------------------------------------------
    async def poll_pending_cards(self) -> int:
        """扫描所有 provider_status in (PENDING,RUNNING) 的 cards，调 wanx.poll 更新状态。

        - SUCCEEDED：下载 video_url → 烧录 AI 标识 → 注册 file_id → 更新 output_fid
        - FAILED/CANCELED：写 error_msg
        返回处理的 card 数。
        """
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT card_id, tenant_id, session_id, provider_task_id, created_at
                   FROM gen_cards
                   WHERE provider_status IN ('PENDING', 'RUNNING') AND provider_task_id IS NOT NULL"""
            )
            pending = [dict(r) for r in cur.fetchall()]

        if not pending:
            return 0

        processed = 0
        for card in pending:
            # task 超过有效期则标失败
            if self._is_task_expired(card["created_at"]):
                self._mark_failed(
                    card["card_id"], card["tenant_id"],
                    "成片任务已过期（超过24h未完成），请重新生成",
                )
                processed += 1
                continue

            try:
                result = await self.wanx.poll(card["provider_task_id"])
            except WanxProviderError as exc:
                logger.warning(f"视频生成轮询失败 card={card['card_id']}: {exc}")
                continue   # 网络/临时错误，下轮再试

            if result.task_status == "SUCCEEDED" and result.video_url:
                await self._on_card_succeeded(card, result)
            elif result.task_status in ("FAILED", "CANCELED"):
                self._mark_failed(
                    card["card_id"], card["tenant_id"],
                    result.error or f"生成失败 ({result.task_status})",
                )
            # PENDING/RUNNING/UNKNOWN 不动，下轮继续
            processed += 1

        if processed:
            logger.info(f"视频生成轮询: 处理 {processed} 条 card")
        return processed

    async def _on_card_succeeded(self, card: dict[str, Any], result) -> None:
        """SUCCEEDED：下载成片（含烧录 AI 标识）→ 注册 file_id → 更新 card。"""
        try:
            output_fid = await self.media.download_and_register(
                url=result.video_url,
                tenant_id=card["tenant_id"] or "demo",
                display_name=f"{card['card_id']}.mp4",
                burn_label=True,
            )
            with get_db_connection() as conn:
                conn.cursor().execute(
                    """UPDATE gen_cards
                       SET output_fid = %s, output_duration = %s, provider_status = 'SUCCEEDED',
                           error_msg = NULL, updated_at = NOW()
                       WHERE card_id = %s""",
                    (output_fid, result.duration, card["card_id"]),
                )
                conn.commit()
            logger.info(f"视频生成 card 成功 {card['card_id']} → file_id={output_fid}")
        except Exception as exc:
            logger.error(f"视频生成成片下载/烧录失败 card={card['card_id']}: {exc}")
            self._mark_failed(card["card_id"], card["tenant_id"], "成片下载失败，请重新生成")

    def _mark_failed(self, card_id: str, tenant_id: str | None, error_msg: str) -> None:
        """标记 card 失败。"""
        with get_db_connection() as conn:
            conn.cursor().execute(
                """UPDATE gen_cards
                   SET provider_status = 'FAILED', error_msg = %s, updated_at = NOW()
                   WHERE card_id = %s""",
                (error_msg, card_id),
            )
            conn.commit()

    def _is_task_expired(self, created_at) -> bool:
        """task 是否超过万相查询有效期。"""
        if created_at is None:
            return False
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                return False
        try:
            return datetime.now(created_at.tzinfo) - created_at > _TASK_MAX_AGE
        except Exception:
            return datetime.utcnow() - created_at > _TASK_MAX_AGE

    # ------------------------------------------------------------------
    # 序列化辅助
    # ------------------------------------------------------------------
    def _serialize_session(self, session: dict[str, Any]) -> dict[str, Any]:
        """把 DB row 的 datetime/JSONB 序列化为 JSON 友好格式。"""
        scene = get_scene(session.get("scene_id"))
        result = dict(session)
        result["scene_name"] = scene.name if scene else session.get("scene_id")
        for k, v in result.items():
            if isinstance(v, datetime):
                result[k] = v.isoformat()
        # cards 子项的 datetime 也转
        for card in result.get("cards", []) or []:
            for k, v in list(card.items()):
                if isinstance(v, datetime):
                    card[k] = v.isoformat()
        return result
