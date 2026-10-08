"""Original registration/referral/welcome stage, with only platform IO fictional."""
import asyncio


async def run_original_enter_business(completion, locator, *, admission=None):
    from src.channels.wecom_kf.completion_business import KfCompletionBusiness
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    worker = admission.business if admission is not None else KfCompletionBusiness(
        completion.config, completion.scope.database.connect,
        adapter_factory=completion.original_adapter)
    try:
        if admission is None:
            first = await worker.handle(locator)
            repeat = await worker.handle(locator)
            customer = first['customer_user_id']
        else:
            assert await admission.run_once() is None
            consumed = admission.last_context
            query = ('SELECT phase,value FROM wecom_kf_business_facts WHERE '
                "account_id=%s AND namespace=%s AND message_id=%s AND business_kind='ready'")
            args = (locator.account_id, locator.namespace, locator.message_id)
            first = (await asyncio.to_thread(completion.scope.rows, query, args))[0]
            assert await admission.run_once() is None
            repeat = (await asyncio.to_thread(completion.scope.rows, query, args))[0]
            customer = first['value']['customer_user_id']
        assert first == repeat and first['phase'] == 'known'
        assert isinstance(customer, str) and customer
        assert completion.platform.count('/cgi-bin/kf/customer/batchget') == 1
        assert completion.platform.count('/cgi-bin/kf/send_msg_on_event') == 1
        s = completion.scope
        rows = await asyncio.to_thread(s.rows,
            'SELECT user_id,tenant_id,source FROM users WHERE user_id=%s', (customer,))
        assert rows == [{'user_id': customer, 'tenant_id': s.tenant_id, 'source': 'wecom_kf'}]
        referral = await asyncio.to_thread(s.rows,
            'SELECT tenant_id,referrer_user_id,customer_user_id,open_kfid,scene '
            'FROM customer_referrals WHERE customer_user_id=%s', (customer,))
        assert referral == [{'tenant_id': s.tenant_id, 'referrer_user_id': completion.referrer_user_id,
            'customer_user_id': customer, 'open_kfid': s.open_kfid, 'scene': completion.scene}]
        session = await asyncio.to_thread(s.rows,
            'SELECT user_id,subagent_id FROM channel_sessions WHERE session_id=%s', (s.legacy_sid,))
        assert session == [{'user_id': None,
            'subagent_id': '' if completion.profile_id == 'main' else completion.profile_id}]
        facts = await asyncio.to_thread(s.rows,
            'SELECT business_kind,phase,value FROM wecom_kf_business_facts '
            'WHERE account_id=%s AND namespace=%s AND message_id=%s ORDER BY business_kind',
            (locator.account_id, locator.namespace, locator.message_id))
        assert [row['business_kind'] for row in facts] == ['ready', 'referral', 'registration']
        assert all(row['phase'] == 'known' for row in facts)
        wire = await asyncio.to_thread(s.rows,
            "SELECT phase,response_origin,errcode,purpose FROM wecom_kf_wire_operations "
            "WHERE locator->>'account_id'=%s AND locator->>'message_id'=%s",
            (locator.account_id, locator.message_id))
        assert wire == [{'phase': 'ack', 'response_origin': 'platform', 'errcode': 0, 'purpose': 'welcome'}]
        inbox = await asyncio.to_thread(s.rows,
            'SELECT payload,capability_ciphertext FROM wecom_kf_inbox '
            'WHERE account_id=%s AND namespace=%s AND message_id=%s',
            (locator.account_id, locator.namespace, locator.message_id))
        assert len(inbox) == 1 and inbox[0]['capability_ciphertext']
        assert 'welcome_code' not in inbox[0]['payload']['event']
        assert completion.welcome_code not in str(inbox[0]['payload'])
        # Original lifecycle consumer resolves this actual enter_session fact.
        # Business side effects alone cannot fake the preceding event guard.
        context = None if admission is not None else ContextWorker(completion.config.wecom_kf,
            repository=ContextRepository(s.database.connect), client_factory=completion.original_client)
        try:
            if context is not None:
                consumed = await context.run_once()
            assert consumed['disposition'] == 'pending_business' and consumed['history_id'] is None
            classifications = await asyncio.to_thread(s.rows,
                'SELECT classification,classification_resolved FROM wecom_kf_receipt_classifications '
                'WHERE account_id=%s AND namespace=%s AND message_id=%s',
                (locator.account_id, locator.namespace, locator.message_id))
            assert classifications == [{'classification': 'event', 'classification_resolved': True}]
        finally:
            if context is not None:
                await context.close()
        assert not worker.tasks and not completion.platform.errors
        return customer
    finally:
        if admission is None:
            await worker.close()
