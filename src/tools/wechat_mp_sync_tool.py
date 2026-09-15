"""微信公众号同步薄工具（WP8，设计 §3/§14，计划 WP8 节）。

分层纪律（设计 §3）：工具为薄入口——只做参数解析与状态回传，直接调用
src/wechat_mp/service.py 的受理/查询能力；抓取、入库、计费、限流、去重等
全部逻辑只在 service 层实现，本模块禁止出现任何 SQL/抓取/入库代码。

身份边界：tenant_id / user_id / subagent_id 一律取可信工具执行上下文
（current_tool_execution_context），InputModel 不含任何身份字段，
绝不接受 LLM 传入的租户/用户身份（设计 §14）。

口径硬规则（设计 §14 能力边界，写进 description 与返回 message）：
- 只承诺「已提交获取/刷新任务，已排队处理」，不承诺立即完成；
- 不得宣称能发现公众号最新文章（主动发现依赖回调与清单源，P1 未接入）；
- 已有文章重新提交同 URL = 请求刷新（内容变化才更新，未变零费用）；
- 任务完成后用 knowledge_base_search 检索，本工具不返回文章正文。
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools.context import current_tool_execution_context
from src.wechat_mp.service import (
    WeChatMPBusinessError,
    get_run,
    import_urls,
    list_runs,
)

# error_message 回传截断长度（服务端写库前已脱敏，此处仅限长）
_ERROR_MSG_MAX_CHARS = 200

_SYNC_DONE_HINT = "任务完成后请用 knowledge_base_search 工具检索公众号内容回答用户"


class WechatMPSyncInput(BaseModel):
    """同步公众号文章参数"""
    urls: List[str] = Field(
        ...,
        description=(
            "用户提供的微信公众号文章 URL 列表（1~50 条）。仅支持 mp.weixin.qq.com "
            "文章链接，短链形如 https://mp.weixin.qq.com/s/AbCdEf12345，"
            "长链形如 https://mp.weixin.qq.com/s?__biz=MzA1MjU0NjEwNA=="
            "&mid=2247483728&idx=1&sn=3f9a2b7c。"
            "用户消息里的每条链接作为列表的一个元素传入"
        ),
    )


class WechatMPSyncStatusInput(BaseModel):
    """查询公众号同步状态参数"""
    run_id: Optional[int] = Field(
        None,
        description=(
            "要查询的同步任务 ID（wechat_mp_sync 返回的 run_id）。"
            "不传则返回最近 5 条任务的概要"
        ),
    )


def _iso(value: Any) -> Any:
    """datetime 转 ISO 字符串（工具结果经 json.dumps 序列化，datetime 不可序列化）。"""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value


def _clip(value: Any, limit: int = _ERROR_MSG_MAX_CHARS) -> str:
    """文本截断（None 转空串），用于错误信息回传。"""
    return (str(value) if value else "")[:limit]


def _format_run_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    """run 行 → 工具回传摘要（仅选取对 LLM 有意义的字段，时间转字符串）。"""
    return {
        "run_id": run.get("id"),
        "status": run.get("status"),
        "trigger_type": run.get("trigger_type"),
        "total_count": run.get("total_count"),
        "new_count": run.get("new_count"),
        "updated_count": run.get("updated_count"),
        "deleted_count": run.get("deleted_count"),
        "skipped_count": run.get("skipped_count"),
        "failed_count": run.get("failed_count"),
        "credits_charged": float(run.get("credits_charged") or 0),
        "error_message": _clip(run.get("error_message")),
        "created_at": _iso(run.get("created_at")),
        "started_at": _iso(run.get("started_at")),
        "completed_at": _iso(run.get("completed_at")),
    }


def _require_tenant() -> Optional[str]:
    """从可信执行上下文取 tenant_id；缺失返回 None（由调用方明确失败，不静默）。"""
    context = current_tool_execution_context()
    return context.tenant_id if context else None


class WechatMPSyncTool(BaseTool):
    """提交公众号文章 URL 到知识库同步队列（薄入口，逻辑在 service 层）"""

    name = "wechat_mp_sync"
    display_name = "同步公众号文章"
    description = (
        "将用户粘贴的微信公众号文章 URL 提交到知识库同步队列（获取新文章或刷新已有文章），"
        "适用于用户提供公众号文章链接、要求把文章加入知识库或刷新文章内容的场景。"
        "口径约束：1) 只承诺「已提交获取/刷新任务，已排队处理」，处理按租户队列串行进行，"
        "不承诺立即完成；2) 本工具不能自动发现公众号最新文章，只处理用户明确粘贴的 URL；"
        "3) 已有文章重新提交同一 URL 即视为请求刷新（内容有变化才更新，内容未变不重复计费）；"
        "4) 本工具不返回文章正文，任务完成后请改用 knowledge_base_search 检索公众号内容回答用户。"
    )
    InputModel = WechatMPSyncInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        urls = kwargs.get("urls")
        if not urls or not isinstance(urls, list):
            return {
                "success": False,
                "error": "urls 不能为空，请提供至少 1 条微信公众号文章 URL",
            }

        tenant_id = _require_tenant()
        if not tenant_id:
            logger.bind(module="wechat_mp").warning(
                "后端日志：wechat_mp_sync 缺少租户上下文，拒绝执行"
            )
            return {
                "success": False,
                "error": "缺少租户身份上下文，无法提交同步任务",
            }
        context = current_tool_execution_context()
        user_id = context.user_id if context else None

        logger.bind(module="wechat_mp").info(
            "后端日志：wechat_mp_sync 受理 tenant_id={} user_id={} subagent_id={} url_count={}",
            tenant_id, user_id,
            context.subagent_id if context else None, len(urls),
        )
        try:
            # 假异步规范（.claude/rules/backend_dev.md）：service 调用含多次 DB 往返
            # 与 Redis 唤醒，async 入口必须 to_thread 包裹，避免阻塞事件循环
            result = await asyncio.to_thread(import_urls, tenant_id, user_id, urls)
        except WeChatMPBusinessError as e:
            # 限流、超量等业务规则错误：消息面向用户可直接展示
            return {"success": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 意外异常按规范记日志，回传友好失败
            logger.opt(exception=True).error(
                "后端日志：wechat_mp_sync 提交失败 tenant_id={}: {}", tenant_id, e
            )
            return {
                "success": False,
                "error": "提交公众号文章同步任务失败，请稍后重试或联系管理员",
            }

        if not result.get("run_id"):
            # 无任何 URL 入队：全部非法 / 全部已在待处理队列 / 空列表。
            # 如实回传原因（rejected/duplicates），不伪造成功
            return {
                "success": False,
                "error": result.get("message") or "没有可导入的合法 URL",
                "rejected": result.get("rejected") or [],
                "duplicates": result.get("duplicates") or [],
            }

        accepted = int(result.get("accepted") or 0)
        rejected = result.get("rejected") or []
        duplicates = result.get("duplicates") or []
        message = (
            f"已提交获取/刷新任务，已排队处理（run_id={result['run_id']}，"
            f"受理 {accepted} 条，驳回 {len(rejected)} 条，批内重复 {len(duplicates)} 条），"
            "按租户队列串行处理，不保证立即完成。"
            "本工具只处理已粘贴 URL 的获取/刷新，不能自动发现公众号最新文章；"
            "同一篇文章重复提交视为请求刷新（内容有变化才更新，未变不重复计费）。"
            f"{_SYNC_DONE_HINT}。"
        )
        return {
            "success": True,
            "run_id": result["run_id"],
            "accepted": accepted,
            "rejected": rejected,
            "duplicates": duplicates,
            "message": message,
        }


class WechatMPSyncStatusTool(BaseTool):
    """查询公众号文章同步任务状态（薄入口，逻辑在 service 层）"""

    name = "wechat_mp_sync_status"
    display_name = "查询公众号同步状态"
    description = (
        "查询微信公众号文章同步任务的状态与结果。传入 wechat_mp_sync 返回的 run_id "
        "可查询单个任务：排队中/运行中/成功/部分失败/失败，含各计数、消耗积分与"
        "每篇文章的处理状态、失败原因（如实返回，不做美化）；不传 run_id 则返回"
        "最近 5 条任务概要。任务完成后请用 knowledge_base_search 工具检索已入库的"
        "公众号内容回答用户，本工具不返回文章正文。"
    )
    InputModel = WechatMPSyncStatusInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        run_id = kwargs.get("run_id")

        tenant_id = _require_tenant()
        if not tenant_id:
            logger.bind(module="wechat_mp").warning(
                "后端日志：wechat_mp_sync_status 缺少租户上下文，拒绝执行"
            )
            return {
                "success": False,
                "error": "缺少租户身份上下文，无法查询同步状态",
            }

        try:
            # 查询路径同样含 DB 往返，按假异步规范在 async 入口 to_thread 包裹
            #（_query_* 保持同步 def，不改变与 service 的薄入口边界）
            if run_id is not None:
                return await asyncio.to_thread(self._query_single_run, tenant_id, run_id)
            return await asyncio.to_thread(self._query_recent_runs, tenant_id)
        except Exception as e:  # noqa: BLE001 意外异常按规范记日志，回传友好失败
            logger.opt(exception=True).error(
                "后端日志：wechat_mp_sync_status 查询失败 tenant_id={}: {}", tenant_id, e
            )
            return {
                "success": False,
                "error": "查询公众号同步状态失败，请稍后重试",
            }

    def _query_single_run(self, tenant_id: str, run_id: Any) -> Dict[str, Any]:
        """按 run_id 查询详情；他租户/不存在统一返回「未找到」，不泄露存在性。"""
        try:
            # run_id 可能被 LLM 传成字符串，薄工具层做类型规范化（不改变语义）
            run_id = int(run_id)
        except (TypeError, ValueError):
            return {"success": False, "error": "未找到该任务"}

        data = get_run(tenant_id, run_id)
        if not data:
            logger.bind(module="wechat_mp").info(
                "后端日志：wechat_mp_sync_status 未找到任务（不存在或非本租户）"
                "tenant_id={} run_id={}",
                tenant_id, run_id,
            )
            return {"success": False, "error": "未找到该任务"}

        run = _format_run_summary(data["run"])
        items = [
            {
                "article_row_id": it.get("article_row_id"),
                "action": it.get("action"),
                "status": it.get("status"),
                "error_code": it.get("error_code"),
                "error_message": _clip(it.get("error_message")),
            }
            for it in data.get("items") or []
        ]
        return {
            "success": True,
            "run": run,
            "items": items,
            "message": (
                f"任务 run_id={run_id} 当前状态：{run['status']}。"
                "处理中/失败均为真实进度与原因；内容处理成功入库后，"
                f"请用 knowledge_base_search 工具检索公众号内容回答用户。"
            ),
        }

    def _query_recent_runs(self, tenant_id: str) -> Dict[str, Any]:
        """最近 5 条任务概要（租户隔离由 service 层保证）。"""
        data = list_runs(tenant_id, limit=5, offset=0)
        runs = [_format_run_summary(r) for r in data.get("runs") or []]
        return {
            "success": True,
            "runs": runs,
            "total": int(data.get("total") or 0),
            "message": (
                f"共 {data.get('total') or 0} 条同步任务，当前返回最近 {len(runs)} 条概要"
                "（含排队/运行中/成功/失败状态与计数）。需要某条任务的逐篇明细时，"
                "传入其 run_id 再查询；内容处理成功入库后，"
                f"请用 knowledge_base_search 工具检索公众号内容回答用户。"
            ),
        }
