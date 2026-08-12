"""视频生成 Service 层。

串联 db / provider / media / preprocess / scenes，实现抽卡式工具的完整业务逻辑：
- create_session: 创建会话 + 人脸裁剪产品图 + 向 provider 提交 N 条任务（不同 seed 差异化）
- poll_pending_cards: 后台轮询，SUCCEEDED 下载成片（含烧录 AI 标识）+ 注册 file_id
- get_session / list_sessions: 会话与卡片查询

Provider 由 settings.video_gen.provider 决定（wanx / minimax），通过 factory 构造。
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
from src.video_gen.base import BaseVideoProviderError, ProviderOptions, VideoGenRequest
from src.video_gen.factory import build_provider
from src.video_gen.media import MediaRegistry
from src.video_gen.scenes import get_scene, list_scenes as _list_scene_presets

# card 终态集合（轮询时跳过）
_TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN")

# 预设比例 -> (宽/高) 浮点值，用于智能识别时找最接近的预设
# 不含 21:9（已下线）：5 个预设覆盖竖屏/横屏/方屏/常见比例
_RATIO_PRESETS = {
    "9:16": 9 / 16,   # 竖屏
    "16:9": 16 / 9,   # 横屏
    "1:1":  1.0,      # 方屏
    "4:3":  4 / 3,    # 横屏（传统）
    "3:4":  3 / 4,    # 竖屏（传统）
}


def _detect_ratio_from_image(file_id: str) -> str:
    """读图片实际宽高，返回最接近的预设比例（9:16/16:9/1:1/4:3/3:4）。

    用 PIL 读图，按 |actual - preset| 最小者匹配。读图失败时回退到 9:16（短视频主流竖屏）。
    """
    try:
        from PIL import Image
        path = MediaRegistry.get_local_path(file_id)
        with Image.open(path) as img:
            w, h = img.size
        if h == 0:
            return "9:16"
        actual = w / h
        # 找最接近的预设
        best = min(_RATIO_PRESETS.items(), key=lambda kv: abs(kv[1] - actual))
        logger.info(f"智能识别比例 file_id={file_id} 实际={w}x{h}={actual:.3f} -> {best[0]}")
        return best[0]
    except Exception as exc:
        logger.warning(f"智能识别比例失败 file_id={file_id}，回退 9:16: {exc}")
        return "9:16"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class VideoGenService:
    def __init__(self) -> None:
        # 通过 factory 构造当前 provider（wanx / minimax），api_keys 注入便于测试
        self._provider = build_provider(settings.video_gen, settings.llm.qwen.api_keys)
        # task 有效期取 provider 声明值（万相 24h / MiniMax 168h）
        self._task_max_age = timedelta(hours=self._provider.get_options().task_max_age_hours)
        self.media = MediaRegistry()

    def get_options(self) -> ProviderOptions:
        """透传当前 provider 的能力声明（给前端 /options 端点用）。"""
        return self._provider.get_options()

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
        card_count: int = 2,
        expanded_prompt: str | None = None,
        model_image_fid: str | None = None,
        enable_ai_label: bool = True,
        duration_sec: int = 5,
        resolution: str = "720P",
        ratio: str = "9:16",
    ) -> dict[str, Any]:
        """创建抽卡会话 + 立即向万相提交 card_count 条任务。

        流程（mvp-design.md §8.4）：
        1. 校验 scene_id
        2. 读 base64：产品图→reference_image（锁定外观），模特图（可选）→first_frame（控制起始）
        3. card_count 个不同 seed，各调 wanx.submit
        4. 写 gen_sessions + gen_cards

        注：不做程序侧素材预处理，由用户自行上传正确比例的素材（如竖屏 9:16）。
        """
        # 1. 校验场景
        scene = get_scene(scene_id)
        if scene is None:
            raise ValueError(f"未知场景: {scene_id}")
        if not 1 <= card_count <= 3:
            raise ValueError("card_count 必须为 1-3")
        # 校验时长 / 分辨率 / 比例：用当前 provider 暴露的白名单
        opts = self._provider.get_options()
        valid_durations = [int(d.value) for d in opts.durations]
        if duration_sec not in valid_durations:
            raise ValueError(f"duration_sec 必须为 {valid_durations}")
        valid_resolutions = [r.value for r in opts.resolutions]
        if resolution not in valid_resolutions:
            raise ValueError(f"resolution 必须为 {valid_resolutions}")
        valid_ratios = [r.value for r in opts.ratios] + ["auto"]
        if ratio not in valid_ratios:
            raise ValueError(f"ratio 必须为 {valid_ratios} 或 auto")
        # auto 智能识别：读产品图实际宽高，选最接近的预设比例（落地为具体值，便于 DB 持久化与 regenerate 复用）
        if ratio == "auto":
            ratio = _detect_ratio_from_image(product_image_fid)

        # 2. 提示词：expanded_prompt 为空则用场景模板填空（极简提示词引擎，§6）
        prompt = expanded_prompt or scene.prompt_template.format(copywriting=copywriting)

        # 3. 读 base64：产品图→reference_image（锁定外观）；模特图→first_frame，无模特图则用产品图
        reference_data_url = MediaRegistry.read_as_base64(product_image_fid)
        first_frame_data_url = (
            MediaRegistry.read_as_base64(model_image_fid) if model_image_fid else reference_data_url
        )

        # 4. 写会话 + 提交 N 条任务（不同 seed 差异化）
        session_id = new_id("sess")
        base_seed = random.randint(0, 2147483647)

        cards: list[dict[str, Any]] = []
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO gen_sessions
                   (session_id, tenant_id, user_id, scene_id, product_image_fid,
                    model_image_fid, copywriting, expanded_prompt, card_count,
                    enable_ai_label, duration_sec, resolution, ratio, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'generating')""",
                (session_id, tenant_id, user_id, scene_id, product_image_fid,
                 model_image_fid, copywriting, prompt, card_count,
                 enable_ai_label, duration_sec, resolution, ratio),
            )

            for idx in range(card_count):
                seed = (base_seed + idx) & 0x7FFFFFFF   # 正整数差异化 seed
                card_id = new_id("card")
                provider_task_id = None
                provider_status = "PENDING"
                error_msg = None
                try:
                    # 构造统一请求；MiniMax 不支持 negative_prompt，service 层据此决定是否传
                    negative = scene.negative_prompt or ""
                    if not opts.supports_negative_prompt:
                        if negative:
                            logger.info(f"视频生成: 当前 provider 不支持 negative_prompt，已忽略: session={session_id}")
                        negative = ""
                    req = VideoGenRequest(
                        prompt=prompt,
                        reference_image_data_url=reference_data_url,
                        first_frame_data_url=first_frame_data_url,
                        seed=seed,
                        negative_prompt=negative,
                        duration=duration_sec,
                        resolution=resolution,
                        ratio=ratio,
                    )
                    submit_result = await self._provider.submit(req)
                    provider_task_id = submit_result.task_id
                    provider_status = submit_result.task_status
                except BaseVideoProviderError as exc:
                    error_msg = str(exc)
                    provider_status = "FAILED"
                    logger.error(f"视频生成 card 提交失败 session={session_id} idx={idx}: {exc}")
                except Exception as exc:
                    # 兜底非预期异常（如 provider 返回非 JSON），防止单条失败导致整批 session 回滚丢失
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
        # 边界处理：所有 card 在提交时就全部 FAILED（万相全挂）-> 直接 finalize session
        final_status = self._maybe_finalize_session(session_id, tenant_id)
        return {
            "session_id": session_id, "scene_id": scene_id,
            "scene_name": scene.name, "copywriting": copywriting,
            "product_image_fid": product_image_fid,
            "model_image_fid": model_image_fid,
            "expanded_prompt": prompt, "card_count": card_count,
            "status": final_status or "generating",
            "cards": cards,
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_session(self, tenant_id: str | None, session_id: str) -> dict[str, Any] | None:
        """查会话 + 其下所有 cards 状态。"""
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT session_id, tenant_id, scene_id, product_image_fid, model_image_fid,
                          copywriting, expanded_prompt, card_count,
                          enable_ai_label, duration_sec, resolution, ratio,
                          status, created_at
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
                """SELECT session_id, tenant_id, scene_id, product_image_fid, model_image_fid,
                          copywriting, expanded_prompt, card_count,
                          enable_ai_label, duration_sec, resolution, ratio,
                          status, created_at
                   FROM gen_sessions
                   WHERE tenant_id IS NOT DISTINCT FROM %s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
            sessions = [dict(r) for r in cur.fetchall()]
        return [self._serialize_session(s) for s in sessions]

    # ------------------------------------------------------------------
    # 卡片查询
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
                """SELECT c.card_id, c.tenant_id, c.session_id, c.provider_task_id, c.created_at,
                          s.enable_ai_label
                   FROM gen_cards c
                   JOIN gen_sessions s ON c.session_id = s.session_id
                   WHERE c.provider_status IN ('PENDING', 'RUNNING')
                     AND c.provider_task_id IS NOT NULL"""
            )
            pending = [dict(r) for r in cur.fetchall()]

        # 临时 tlog：扫描结果（含 0 也要记，便于排查 background_runner 是否在跑）
        try:
            from src.core.temp_logger import tlog
            tlog(
                "video-agent-阶段三",
                "poll_pending_cards 扫描到 pending={n} 条 cards={cids}",
                n=len(pending),
                cids=[c.get("card_id") for c in pending],
            )
        except Exception:
            pass

        if not pending:
            return 0

        processed = 0
        for card in pending:
            # task 超过有效期则标失败
            if self._is_task_expired(card["created_at"]):
                self._mark_failed(
                    card["card_id"], card["tenant_id"],
                    f"成片任务已过期（超过{int(self._task_max_age.total_seconds() // 3600)}h 未完成），请重新生成",
                    session_id=card["session_id"],
                )
                processed += 1
                continue

            try:
                result = await self._provider.poll(card["provider_task_id"])
            except BaseVideoProviderError as exc:
                logger.warning(f"视频生成轮询失败 card={card['card_id']}: {exc}")
                try:
                    from src.core.temp_logger import tlog
                    tlog(
                        "video-agent-阶段三",
                        "poll_pending_cards 轮询失败（下轮重试）card={cid} err={err}",
                        cid=card["card_id"],
                        err=str(exc),
                        level="WARNING",
                    )
                except Exception:
                    pass
                continue   # 网络/临时错误，下轮再试
            except Exception as exc:
                # 切换 provider 后，旧 provider 的 task_id 在新 provider 上无法查询
                # （如 wanx -> minimax 切换），抛 unknown-task 异常时直接标 FAILED
                logger.warning(f"视频生成轮询异常（可能 provider 切换）card={card['card_id']}: {exc}")
                try:
                    from src.core.temp_logger import tlog
                    tlog(
                        "video-agent-阶段三",
                        "poll_pending_cards 轮询异常（标 FAILED）card={cid} err={err}",
                        cid=card["card_id"],
                        err=str(exc),
                        level="ERROR",
                    )
                except Exception:
                    pass
                self._mark_failed(
                    card["card_id"], card["tenant_id"],
                    f"原 provider 任务无法轮询，请重新生成: {exc}",
                    session_id=card["session_id"],
                )
                processed += 1
                continue

            try:
                from src.core.temp_logger import tlog
                tlog(
                    "video-agent-阶段三",
                    "poll_pending_cards 状态更新 card={cid} status={st} has_video={hv}",
                    cid=card["card_id"],
                    st=result.task_status,
                    hv=bool(result.video_url),
                )
            except Exception:
                pass

            if result.task_status == "SUCCEEDED" and result.video_url:
                await self._on_card_succeeded(card, result, enable_ai_label=card.get("enable_ai_label", True))
            elif result.task_status in ("FAILED", "CANCELED"):
                self._mark_failed(
                    card["card_id"], card["tenant_id"],
                    result.error or f"生成失败 ({result.task_status})",
                    session_id=card["session_id"],
                )
            # PENDING/RUNNING/UNKNOWN 不动，下轮继续
            processed += 1

        if processed:
            logger.info(f"视频生成轮询: 处理 {processed} 条 card")
        return processed

    async def _on_card_succeeded(
        self,
        card: dict[str, Any],
        result,
        enable_ai_label: bool = True,
    ) -> None:
        """SUCCEEDED：下载成片（按 session 配置决定是否烧录 AI 标识）→ 注册 file_id → 更新 card。"""
        try:
            output_fid = await self.media.download_and_register(
                url=result.video_url,
                tenant_id=card["tenant_id"] or "demo",
                display_name=f"{card['card_id']}.mp4",
                burn_label=enable_ai_label,
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
            # card 成功后，可能该 session 下所有 card 都已到终态，尝试更新 session.status
            self._maybe_finalize_session(card["session_id"], card["tenant_id"])
        except Exception as exc:
            # 临时调试：打印完整 traceback + 关键上下文（万相返回的 video_url 是 24h 临时 URL，
            # 可能 404/超时；烧录 FFmpeg 可能因中文字体缺失失败）。bug 修复后可降级。
            logger.exception(
                f"视频生成成片下载/烧录失败 card={card['card_id']} tenant={card.get('tenant_id')} "
                f"provider_task_id={card.get('provider_task_id')} "
                f"video_url={getattr(result, 'video_url', None)} "
                f"duration={getattr(result, 'duration', None)}: {exc!r}"
            )
            self._mark_failed(
                card["card_id"], card["tenant_id"], "成片下载失败，请重新生成",
                session_id=card["session_id"],
            )

    def _mark_failed(
        self,
        card_id: str,
        tenant_id: str | None,
        error_msg: str,
        session_id: str | None = None,
    ) -> None:
        """标记 card 失败。若 session_id 传入，则尝试 finalize session status。"""
        with get_db_connection() as conn:
            conn.cursor().execute(
                """UPDATE gen_cards
                   SET provider_status = 'FAILED', error_msg = %s, updated_at = NOW()
                   WHERE card_id = %s""",
                (error_msg, card_id),
            )
            conn.commit()
        # card 失败后，可能该 session 下所有 card 都已到终态，尝试更新 session.status
        if session_id is not None:
            self._maybe_finalize_session(session_id, tenant_id)

    def _maybe_finalize_session(self, session_id: str, tenant_id: str | None) -> str | None:
        """检查 session 下所有 cards 是否都已到终态，若是则更新 gen_sessions.status。

        - 有任何 SUCCEEDED -> 'done'（用户拿到了视频，按成功处理）
        - 全部 FAILED/CANCELED/UNKNOWN -> 'failed'
        - 仍有 PENDING/RUNNING -> 不更新，返回 None
        - session.status 已是 done/failed -> 不重复更新，返回当前 status

        返回值：更新后的 status（或当前已是终态时的 status）；未更新时返回 None。
        """
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT status FROM gen_sessions
                   WHERE session_id = %s AND tenant_id IS NOT DISTINCT FROM %s""",
                (session_id, tenant_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            current_status = dict(row).get("status")
            if current_status != "generating":
                return current_status
            cur.execute(
                "SELECT provider_status FROM gen_cards WHERE session_id = %s",
                (session_id,),
            )
            statuses = [dict(r)["provider_status"] for r in cur.fetchall()]
            if not statuses:
                return None
            # 还有未到终态的 card，不更新
            if any(s not in _TERMINAL_STATUSES for s in statuses):
                return None
            final_status = "done" if any(s == "SUCCEEDED" for s in statuses) else "failed"
            cur.execute(
                """UPDATE gen_sessions SET status = %s, updated_at = NOW()
                   WHERE session_id = %s AND tenant_id IS NOT DISTINCT FROM %s""",
                (final_status, session_id, tenant_id),
            )
            conn.commit()
            logger.info(f"视频生成 session 终态 {session_id} -> {final_status}")
            return final_status

    def _is_task_expired(self, created_at) -> bool:
        """task 是否超过 provider 查询有效期（万相 24h / MiniMax 168h）。"""
        if created_at is None:
            return False
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                return False
        try:
            return datetime.now(created_at.tzinfo) - created_at > self._task_max_age
        except Exception:
            return datetime.utcnow() - created_at > self._task_max_age

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
