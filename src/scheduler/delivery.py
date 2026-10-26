"""定时任务结果渠道投递（尽力投递）

背景：wecom_kf 等第三方渠道的会话有平台侧收发限制（如 48 小时窗口、
配额、接待状态），定时任务执行成功后不保证能把结果发到微信侧客户。
本模块实现三层尽力投递：
1. 创建端如实告知（工具层负责，本模块不涉及）；
2. 执行成功后立即直投一次，失败/无直投机会时登记 pending；
3. 客户下次来消息时（入站钩子）把 pending 记录合并补投。
"""

from typing import Any, Dict, Optional

from loguru import logger

from src.scheduler.db import ScheduledTaskDeliveryDB

_RESULT_SNIPPET_LEN = 400
_TASK_NAME_LEN = 50
_MERGED_TEXT_LIMIT = 1800


def resolve_channel_target(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """解析任务绑定的渠道投递目标（仅 wecom_kf）。

    通过任务行 session_id（创建提醒时的对话会话）回查 channel_sessions；
    查不到 / 非 wecom_kf / 租户不一致（fail-closed）返回 None，表示无
    投递目标，调用方静默跳过（结果仍保留在任务日志与会话中）。
    """
    session_id = task.get("session_id")
    if not session_id:
        return None
    try:
        from src.channels.session import channel_session_manager
        session = channel_session_manager.get_session_by_id(session_id)
    except Exception as e:
        logger.opt(exception=True).warning(
            f"后端日志：定时任务投递目标回查会话失败 session_id={session_id}, {e}")
        return None
    if not session:
        return None
    if session.get("channel_type") != "wecom_kf":
        return None
    session_tenant = (session.get("tenant_id") or "").strip()
    task_tenant = (task.get("tenant_id") or "").strip()
    if task_tenant and session_tenant and task_tenant != session_tenant:
        # 跨租户不一致属异常数据，拒绝投递且不落日志细节（避免泄漏租户标识）
        logger.warning("后端日志：定时任务投递目标租户不一致，已拒绝投递")
        return None
    # 租户兜底顺序：会话行优先（adapter 配置按租户解析），任务行兜底；
    # 双空（遗留未回填数据）无法定位租户渠道配置，fail-closed 拒绝，
    # 避免落一条永远无法被入站钩子命中的 pending
    resolved_tenant = session_tenant or task_tenant
    if not resolved_tenant:
        logger.warning("后端日志：定时任务投递目标租户缺失，已拒绝投递")
        return None
    if not session.get("channel_chat_id") or not session.get("channel_user_id"):
        return None
    return {
        "tenant_id": resolved_tenant,
        "channel_chat_id": session["channel_chat_id"],
        "channel_user_id": session["channel_user_id"],
        "session_id": session_id,
    }


def build_delivery_content(task: Dict[str, Any], result_text: str) -> str:
    name = (task.get("name") or "定时提醒").strip()[:_TASK_NAME_LEN]
    result = (result_text or "").strip() or "（任务已完成，无输出内容）"
    result = result[:_RESULT_SNIPPET_LEN]
    return f"【定时提醒】{name}\n{result}"


async def _send_wecom_kf_text(tenant_id: str, open_kfid: str,
                              external_userid: str, text: str) -> bool:
    """经 ChannelFactory 取 adapter 发送纯文本。

    - factory 返回的是进程级缓存实例（回调链路共用），调用方不得 close。
    - open_kfid 经显式参数传给 send_text，不写共享实例的 current_open_kfid：
      调度线程与回调链路（以及并发的其它任务执行线程）可能同时使用同一
      adapter，写共享状态存在交错窗口。
    - 已知限制：多客服账号租户下 factory 按「verified 优先」解析 config，
      可能取到与会话 open_kfid 不同账号的 adapter；open_kfid 与企业凭证
      不匹配时企微 API 直接报错，发送失败降级 pending（不会错发到其他
      账号），由补投兜底。channel_sessions 未登记 config_id，暂不反查。
    """
    from src.saas.services.channel_factory import ChannelFactory
    adapter, _, _ = await ChannelFactory.create_from_tenant_config(
        tenant_id=tenant_id, channel_type="wecom_kf")
    if adapter is None:
        logger.warning(
            f"后端日志：定时任务投递未取到 wecom_kf adapter tenant_id={tenant_id}")
        return False
    return await adapter.send_text(text, external_userid, open_kfid=open_kfid)


async def deliver_task_result(task: Dict[str, Any],
                              result_text: str) -> Dict[str, Any]:
    """任务执行成功后的尽力直投。

    Returns: {"delivered": bool, "reason": str}
    - delivered=True：已直投到客户
    - no_channel_target：非渠道会话任务（web 等结果走会话/日志，无需投递）
    - send_failed_pending：直投失败已登记补投（常见：48h 窗口关闭/
      配额耗尽/人工接待中），客户下次来消息时补投
    """
    target = resolve_channel_target(task)
    if not target:
        return {"delivered": False, "reason": "no_channel_target"}

    content = build_delivery_content(task, result_text)
    try:
        ok = await _send_wecom_kf_text(
            target["tenant_id"], target["channel_chat_id"],
            target["channel_user_id"], content)
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：定时任务直投异常 task_id={task.get('task_id')}, {e}")
        ok = False

    if ok:
        logger.info(
            f"后端日志：定时任务结果已直投 task_id={task.get('task_id')}, "
            f"channel=wecom_kf")
        return {"delivered": True, "reason": "sent"}

    row = ScheduledTaskDeliveryDB.create_pending(
        task_id=task.get("task_id") or "",
        tenant_id=target["tenant_id"],
        user_id=task.get("user_id") or "",
        channel_type="wecom_kf",
        channel_chat_id=target["channel_chat_id"],
        channel_user_id=target["channel_user_id"],
        session_id=target["session_id"],
        content=content,
    )
    if row:
        logger.info(
            f"后端日志：定时任务直投失败已登记补投 task_id={task.get('task_id')}, "
            f"delivery_id={row['delivery_id']}")
        return {"delivered": False, "reason": "send_failed_pending"}
    logger.error(
        f"后端日志：定时任务直投失败且补投登记失败 task_id={task.get('task_id')}")
    return {"delivered": False, "reason": "send_failed"}


async def deliver_pending_for_channel_user(tenant_id: str, open_kfid: str,
                                           external_userid: str,
                                           adapter: Any) -> None:
    """入站补投钩子：客户来消息时补投其名下全部待投递提醒（合并一条）。

    由渠道入站流程在 AI 可接待状态分支内调用；发送复用调用方 adapter
    （其 current_open_kfid 由入站循环维护）。任何异常只记日志，不影响
    正常消息处理。
    """
    try:
        rows = ScheduledTaskDeliveryDB.list_pending(
            tenant_id, open_kfid, external_userid)
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：补投记录查询失败 {e}")
        return
    if not rows:
        return

    merged = "\n\n".join(r.get("content") or "" for r in rows).strip()
    if len(merged) > _MERGED_TEXT_LIMIT:
        merged = merged[:_MERGED_TEXT_LIMIT] + "\n……（其余提醒内容已省略）"
    if not merged:
        return

    send_error = ""
    try:
        ok = await adapter.send_text(merged, external_userid)
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：补投发送异常 open_kfid={open_kfid}, count={len(rows)}, {e}")
        ok = False
        send_error = str(e)

    if ok:
        for r in rows:
            # 逐条容错：单行标记失败不影响其余行，减少重复补投窗口
            try:
                ScheduledTaskDeliveryDB.mark_sent(r["delivery_id"])
            except Exception:
                logger.opt(exception=True).error(
                    f"后端日志：补投标记已送达失败 delivery_id={r['delivery_id']}")
        logger.info(
            f"后端日志：定时任务提醒补投成功 open_kfid={open_kfid}, count={len(rows)}")
    else:
        for r in rows:
            try:
                ScheduledTaskDeliveryDB.mark_failed(r["delivery_id"], send_error)
            except Exception:
                logger.opt(exception=True).error(
                    f"后端日志：补投标记失败原因出错 delivery_id={r['delivery_id']}")
        logger.warning(
            f"后端日志：定时任务提醒补投失败保持待投 open_kfid={open_kfid}, count={len(rows)}")
