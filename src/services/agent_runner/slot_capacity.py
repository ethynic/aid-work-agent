"""轻池槽内并发容量计算与事件循环停跳看门狗（A 方案，设计 §6）。

effective_slot_concurrency：重池（general）强制 1 槽 1 任务，仅 light 角色
启用执行进程内协程并发；置 AGENT_RUNNER_SLOT_CONCURRENCY=1 即回退现行拓扑。

LoopWatchdog：心跳线程周期性 call_soon_threadsafe 投递探针，探针在事件循环内
执行并更新时间戳；超过阈值未响应即判定循环停跳（11-05 假异步类挂死，pgrep
healthcheck 探测不到），回收本进程全部执行体（既有中断/恢复语义）后硬退，
由监督进程的容器重启路径拉起新进程（"永久损失"降级为"短暂损失"）。
"""

import os
import threading
import time

from loguru import logger


def effective_slot_concurrency(role, config):
    """light 角色按配置并发，其余角色一律 1（误配兜底，防重池并发化）。"""
    return config.slot_concurrency if role == 'light' else 1


def resolve_slot_concurrency(role, config, explicit=None):
    """监督进程显式传参（--slot-concurrency）优先；无论何种来源，general 钳制为 1。"""
    value = explicit if explicit is not None else config.slot_concurrency
    return value if role == 'light' else 1


class LoopWatchdog:

    def __init__(self, loop, executions, worker_id, *, threshold_seconds=15,
                 probe_interval=5):
        self.loop = loop
        self.executions = executions
        self.worker_id = worker_id
        self.threshold_seconds = threshold_seconds
        self.probe_interval = probe_interval
        self._last_seen = time.monotonic()
        self._stopping = threading.Event()
        self._triggered = False
        self._thread = None

    def _probe(self):
        self._last_seen = time.monotonic()

    def _recover_owned_attempts(self):
        """同步 DB 回收本 worker 全部执行体；失败不阻断硬退（租约过期兜底）。

        ExecutionRepository 的同步方法本就经 asyncio.to_thread 在多线程并发使用，
        看门狗线程内直接复用同一实例与连接工厂。"""
        attempts = self.executions.owned_attempts(self.worker_id)
        for attempt in attempts:
            try:
                self.executions.interrupt_attempt(attempt)
            except Exception as error:
                logger.warning('AgentRunner watchdog reclaim failed runner={} kind={}',
                    attempt.runner_id, type(error).__name__)
        return attempts

    def _watch(self):
        while not self._stopping.wait(self.probe_interval):
            try:
                self.loop.call_soon_threadsafe(self._probe)
            except RuntimeError:
                # 事件循环已关闭（正常退出），看门狗随之结束。
                return
            if time.monotonic() - self._last_seen <= self.threshold_seconds:
                continue
            if self._triggered:
                continue
            self._triggered = True
            from src.core.temp_logger import tlog
            tlog("runner看门狗", "事件循环停跳 worker={worker} 阈值={threshold}s，回收执行体后硬退",
                 worker=self.worker_id, threshold=self.threshold_seconds, level="ERROR")
            logger.error('AgentRunner loop watchdog triggered worker={} threshold={}s; '
                         'reclaiming owned attempts and exiting', self.worker_id,
                         self.threshold_seconds)
            try:
                reclaimed = self._recover_owned_attempts()
                logger.warning('AgentRunner watchdog reclaimed attempts={} count={}',
                    self.worker_id, len(reclaimed))
            except Exception as error:
                logger.opt(exception=True).warning(
                    'AgentRunner watchdog reclaim unavailable; lease expiry will recover')
            os._exit(1)

    def start(self):
        self._thread = threading.Thread(target=self._watch, name='runner-loop-watchdog', daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping.set()
