"""billing_daily 定时生产者：租户积分将耗尽 / 有效期将到期提醒（管理员逐人展开）。

幂等口径：credit_low 事件键含年月（每月至多一条）；tenant_expiry 事件键含到期日与
阈值档（每个档位在每个到期日周期至多一条）。生产者先查后发：原事务内查管理员逐人
publish_in_tx，唯一约束保证每管理员一条。
"""
import asyncio
from datetime import date, datetime, time, timedelta

from loguru import logger

from .contracts import Subject
from .events import publish_in_tx
from .service import notification_repository

PRODUCER = 'billing_daily'
# 每日低峰执行时刻（本地时间）
RUN_AT = time(hour=3, minute=10)


def _publish_events(cursor, admins, tenant, events, now, ttl_seconds):
    tenant_id = tenant['tenant_id']
    published = 0
    for user_id in admins:
        subject = Subject(user_id, tenant_id)
        for event in events:
            ref = publish_in_tx(cursor, subject=subject, producer=PRODUCER,
                event_type=event['event_type'], event_key=event['event_key'],
                resource_ref=event['resource_ref'], parameters=event['parameters'],
                occurred_at=now, accepted_at=now, expires_at=now+timedelta(seconds=ttl_seconds),
                category='billing', priority=0, policy_key='tenant_admin_event')
            if ref:
                published += 1
    return published


def run_billing_notifications_once(repository=None, today=None):
    """扫描 active 租户并发布提醒；返回新落库的消息数。同步函数，调用方负责线程化。"""
    if repository is None:
        repository = notification_repository()
    config = repository.config
    if not config.tenant_events_enabled:
        return 0
    today = today or date.today()
    thresholds = sorted(config.tenant_expiry_thresholds, reverse=True)
    published = 0
    with repository.connection_factory() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT tenant_id,company_name,credit_balance,expire_at FROM tenants
            WHERE status='active' ''')
        tenants = cursor.fetchall()
        for tenant in tenants:
            tenant_id = tenant['tenant_id']
            tenant_name = (tenant['company_name'] or tenant_id)[:128]
            try:
                events = []
                # 积分将耗尽：近 7 天滚动日均消耗推算
                if config.tenant_credit_low_enabled:
                    cursor.execute('''SELECT COALESCE(SUM(credit_cost),0) AS total FROM chat_records
                        WHERE tenant_id=%s AND created_at>=CURRENT_TIMESTAMP-%s*interval '1 day'
                        AND credit_cost>0''', (tenant_id, 7))
                    daily_avg = float(cursor.fetchone()['total'] or 0) / 7.0
                    # credit_balance 允许透支为负，参数模型要求非负，负数按 0 上报
                    remaining = max(0.0, float(tenant['credit_balance'] or 0))
                    if daily_avg > 0 and remaining / daily_avg < config.tenant_credit_low_days:
                        events.append({
                            'event_type': 'billing.credit_low',
                            'event_key': f'credit_low:{tenant_id}:{today.strftime("%Y-%m")}',
                            'resource_ref': {'type': 'tenant_credit', 'id': tenant_id},
                            'parameters': {
                                'tenant_name': tenant_name,
                                'daily_avg_credits': round(daily_avg, 2),
                                'estimated_days': max(0, int(remaining / daily_avg)),
                                'remaining_credits': round(remaining, 2),
                            }})
                # 有效期将到期：命中阈值档提醒
                if config.tenant_expiry_enabled and tenant['expire_at'] is not None:
                    expire_day = tenant['expire_at'].date() if isinstance(tenant['expire_at'], datetime) else tenant['expire_at']
                    days_left = (expire_day - today).days
                    # 取满足条件的最小阈值档，保证 30/7/1 逐级升级提醒（event_key 含档位，各档各发一条）
                    threshold = next((t for t in sorted(thresholds) if days_left <= t), None)
                    if threshold is not None and days_left >= 0:
                        events.append({
                            'event_type': 'billing.tenant_expiry',
                            'event_key': f'tenant_expiry:{tenant_id}:{expire_day.isoformat()}:{threshold}',
                            'resource_ref': {'type': 'tenant_profile', 'id': tenant_id},
                            'parameters': {
                                'tenant_name': tenant_name,
                                'expire_date': expire_day.isoformat(),
                                'days_left': max(0, days_left),
                            }})
                if not events:
                    continue
                cursor.execute('''SELECT user_id FROM users
                    WHERE tenant_id=%s AND role='tenant_admin' AND status='active' ''', (tenant_id,))
                admins = [row['user_id'] for row in cursor.fetchall()]
                if not admins:
                    logger.warning('billing_daily: 租户 {} 无可用管理员，跳过通知', tenant_id)
                    continue
                cursor.execute('SELECT clock_timestamp() AS now')
                now = cursor.fetchone()['now']
                published += _publish_events(cursor, admins, tenant, events, now, config.ttl_seconds)
                conn.commit()
            except Exception:
                conn.rollback()
                logger.opt(exception=True).error(
                    f'billing_daily: 租户 {tenant_id} 通知发布失败')
    return published


class TenantBillingNotificationScheduler:
    """挂 background_runner 平台生命周期（与通知 dispatcher 同层），不进 Gunicorn 请求进程。"""

    def __init__(self, repository=None):
        self.repository = repository or notification_repository()
        self._stop = asyncio.Event()
        self._task = None

    async def start(self):
        config = self.repository.config
        if not config.tenant_events_enabled or self._task is not None:
            return
        await asyncio.to_thread(self.repository.assert_schema)
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info('billing_daily 租户通知调度已启动')

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            task, self._task = self._task, None
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self):
        while not self._stop.is_set():
            try:
                count = await asyncio.to_thread(run_billing_notifications_once, self.repository)
                if count:
                    logger.info(f'billing_daily: 处理租户通知意图 {count} 条（含幂等去重）')
            except Exception as error:
                logger.opt(exception=True).error(
                    f'billing_daily: 执行失败: {error.__class__.__name__}: {error}')
            # 首跑立即执行（重启补跑语义，event_key 幂等去重防重复外发），
            # 随后等到下一个 RUN_AT（含当天未到的情况），错过则次日补跑
            now = datetime.now()
            target = datetime.combine(now.date(), RUN_AT)
            if now >= target:
                target += timedelta(days=1)
            delay = (target - now).total_seconds()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
