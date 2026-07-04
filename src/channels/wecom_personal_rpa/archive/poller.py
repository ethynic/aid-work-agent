"""服务端会话存档兜底轮询调度器

主路径是 callback_handler 收到企微回调后立即触发 fetcher.fetch_once（实时）。
本模块负责 60s 一次的保险扫描：

使用场景：
- 回调 URL 短暂不可达（DNS、网络、服务重启期间）
- 回调丢失（企微重试 3 次都失败）
- 服务端长期停机后启动，需要补拉 5 天内的积压消息

工作流：
  每 60s：
    1. 扫描所有 channel_type='wecom_personal_rpa' AND verified=true 的配置
    2. 对每个配置 asyncio.create_task(fetcher.fetch_once(tenant_id, config_id))
       （fetcher 内部已有 Redis 锁防并发，可放心并发触发）
  启动时立即扫一次（补偿服务停机期间的消息）

容错：
  - 单个 fetcher 任务异常不影响其他租户
  - 扫描周期异常不崩主循环
  - shutdown 时优雅取消所有任务，等待最长 10s

与 C# ChatArchiveListener.PollLoopAsync 行为对齐（C# 是单租户轮询，
本模块是多租户并发轮询，由 fetcher 内部的 Redis 锁做并发保护）。
"""

import asyncio
from typing import List, Optional

from loguru import logger

from src.channels.wecom_personal_rpa.archive.fetcher import ServerArchiveFetcher, fetcher as _default_fetcher
from src.saas.db.channel_config_db import ChannelConfigDB

# 兜底轮询周期（秒）。企微会话存档保留 5 天，60s 周期不会丢消息
_DEFAULT_POLL_INTERVAL_SECONDS = 60

# 单次 shutdown 等待所有 fetcher 任务完成的最长时间
_SHUTDOWN_TIMEOUT_SECONDS = 10

# 单次扫描所有租户的超时（防止单次扫描卡死整个轮询循环）
_SCAN_TIMEOUT_SECONDS = 30

_CHANNEL_TYPE = "wecom_personal_rpa"


class ServerArchivePoller:
    """兜底轮询调度器（全局单例，由 main.py lifespan 管理）。"""

    def __init__(
        self,
        fetcher: Optional[ServerArchiveFetcher] = None,
        poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS,
    ):
        self._fetcher = fetcher or _default_fetcher
        self._poll_interval = max(5, poll_interval_seconds)  # 最小 5s 防误配置
        self._stop_event: Optional[asyncio.Event] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._running_fetcher_tasks: set = set()

    @property
    def is_running(self) -> bool:
        """poller 是否在运行（启动后 / 停止前为 True）。"""
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self) -> None:
        """启动兜底轮询（不阻塞，后台 asyncio.Task）。"""
        if self._loop_task is not None and not self._loop_task.done():
            logger.warning("[ServerArchivePoller] 已在运行，忽略重复 start")
            return

        self._stop_event = asyncio.Event()
        self._loop_task = asyncio.create_task(
            self._poll_loop(), name="server_archive_poller"
        )
        logger.info(
            f"[ServerArchivePoller] 启动兜底轮询 interval={self._poll_interval}s"
        )

    async def stop(self) -> None:
        """停止轮询，等待所有在途 fetcher 任务完成（最长 _SHUTDOWN_TIMEOUT_SECONDS）。"""
        if self._stop_event is not None:
            self._stop_event.set()

        if self._loop_task is not None:
            try:
                await asyncio.wait_for(self._loop_task, timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[ServerArchivePoller] 轮询任务 {_SHUTDOWN_TIMEOUT_SECONDS}s 未结束，强制取消"
                )
                self._loop_task.cancel()
            except Exception as e:
                logger.warning(f"[ServerArchivePoller] 轮询任务退出异常: {e}")
            finally:
                self._loop_task = None

        # 等待所有在途 fetcher 任务完成
        pending = [t for t in self._running_fetcher_tasks if not t.done()]
        if pending:
            logger.info(
                f"[ServerArchivePoller] 等待 {len(pending)} 个在途 fetcher 任务完成"
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=_SHUTDOWN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[ServerArchivePoller] 仍有 {len(pending)} 个 fetcher 任务未完成，"
                    f"已超过 {_SHUTDOWN_TIMEOUT_SECONDS}s，放弃等待"
                )
        self._running_fetcher_tasks.clear()
        self._stop_event = None
        logger.info("[ServerArchivePoller] 已停止")

    # ----------------- 内部实现 -----------------

    async def _poll_loop(self) -> None:
        """主循环：启动时立即扫一次，之后每 poll_interval 秒扫一次。

        用 wait_for(stop_event.wait()) 实现可中断的 sleep：stop() 调用 event.set()
        后 wait() 立即返回 True，wait_for 提前完成（非 timeout）→ 退出循环。
        """
        # 启动时立即扫一次（补偿停机期间的消息）
        await self._scan_once(reason="initial")

        while True:
            try:
                # 等待 stop_event（被 set 时立即返回 True）或超时（继续周期扫描）
                stopped = await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval,
                )
                if stopped:
                    logger.info("[ServerArchivePoller] 收到停止信号，退出主循环")
                    return
            except asyncio.TimeoutError:
                # 正常超时 → 触发一次周期扫描
                pass
            except asyncio.CancelledError:
                logger.info("[ServerArchivePoller] 任务被取消，退出主循环")
                return
            except Exception as e:
                logger.warning(
                    f"[ServerArchivePoller] sleep 异常（不退出循环）: {type(e).__name__}: {e}"
                )
                await asyncio.sleep(self._poll_interval)

            await self._scan_once(reason="periodic")

    async def _scan_once(self, reason: str = "periodic") -> None:
        """扫描一次所有 verified 的 wecom_personal_rpa 配置，对每个触发 fetcher。

        单次扫描异常不影响主循环（下一周期重试）。
        """
        try:
            # 用 timeout 防止 DB 查询卡死整个轮询循环
            configs = await asyncio.wait_for(
                asyncio.to_thread(self._list_server_configs),
                timeout=_SCAN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[ServerArchivePoller] 配置查询超时 {_SCAN_TIMEOUT_SECONDS}s，跳过本次扫描"
            )
            return
        except Exception as e:
            logger.warning(
                f"[ServerArchivePoller] 配置查询异常，跳过本次扫描: {type(e).__name__}: {e}"
            )
            return

        if not configs:
            logger.debug(f"[ServerArchivePoller] 无 verified server 配置，跳过（{reason}）")
            return

        logger.info(
            f"[ServerArchivePoller] 触发 {len(configs)} 个租户拉取（{reason}）"
        )

        for cfg in configs:
            tenant_id = cfg.get("tenant_id")
            config_id = cfg.get("config_id")
            if not tenant_id or not config_id:
                continue

            # 异步触发（不等完成，单租户失败不影响其他租户）
            task = asyncio.create_task(
                self._safe_fetch_once(tenant_id, config_id),
                name=f"archive_fetcher_{tenant_id}",
            )
            self._running_fetcher_tasks.add(task)
            task.add_done_callback(self._running_fetcher_tasks.discard)

    async def _safe_fetch_once(self, tenant_id: str, config_id: str) -> None:
        """包装 fetcher.fetch_once，确保异常不外泄（已由 fetcher 兜底，这里双保险）。"""
        try:
            await self._fetcher.fetch_once(tenant_id, config_id)
        except Exception as e:
            logger.warning(
                f"[ServerArchivePoller] fetcher 异常外泄 tenant={tenant_id}: "
                f"{type(e).__name__}: {e}"
            )

    @staticmethod
    def _list_server_configs() -> List[dict]:
        """查询所有 verified 的 wecom_personal_rpa 配置。

        过滤 listen_mode='server'（防御性，codec 已强制 server）。
        在 asyncio.to_thread 中执行避免阻塞事件循环。
        """
        all_configs = ChannelConfigDB.list_by_channel_type(
            _CHANNEL_TYPE, verified_only=True
        )
        result = []
        for cfg in all_configs:
            config_data = cfg.get("config") or {}
            listen_mode = config_data.get("listen_mode", "server")
            if listen_mode == "server":
                result.append(cfg)
        return result


# 全局单例（由 main.py lifespan 启停）
poller = ServerArchivePoller()
