"""Fresh KF receipt authorization. Platform fields stay outside Runner DAL."""

import asyncio
import hashlib
from datetime import datetime, timezone

from src.db.database import get_db_connection
from src.services.agent_runner.contracts import RunnerError, RunnerSubmit, SessionRef
from src.services.agent_runner.source_receipts import PreparedSource, SourceLocator, SourceUnavailable
from .api_client import WeComKfApiClient
from .lifecycle_repository import (LifecycleRepository, StateObservation, read_receipt_in_tx, route_scope)
from .ingress_auth import encoded, KfIngressError
from .ingress_repository import KfIngressRepository
from .ingress_worker import _thread, _drain


class KfSourceProvider:
    def __init__(self, config, connection_factory=get_db_connection, client_factory=WeComKfApiClient):
        self.config, self.connection_factory, self.client_factory = config, connection_factory, client_factory

    @staticmethod
    def project_input_in_tx(cursor,fact):
        from .voice_repository import VoiceRepository
        locator=fact['locator']
        cursor.execute('SELECT message_type,payload_digest FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
                       (locator['account_id'],locator['namespace'],locator['message_id']))
        inbox=cursor.fetchone()
        if not inbox or inbox['payload_digest']!=fact['provenance']['payload_digest']:
            raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
        if inbox['message_type']=='voice':
            return VoiceRepository.projection_in_tx(cursor,fact)
        return {'model_text':fact['intent']['text'],'history_text':fact['intent']['text'],
                'attachments':fact['intent'].get('attachments') or [],'preparation_ref':None}

    def _peer(self, service_id, *, admission=True):
        peer = self.config.peers.get(service_id)
        if ((admission and not self.config.wecom_kf.enabled) or service_id != self.config.wecom_kf.service_id
                or not peer or set(peer.sources) != {'wecom_kf'}):
            raise RunnerError('SOURCE_SERVICE_FORBIDDEN',403)

    def read_in_tx(self, cursor, locator, *, lock=False):
        cursor.execute("SET LOCAL statement_timeout='5s'")
        cursor.execute("SET LOCAL lock_timeout='3s'")
        if locator.source!='wecom_kf' or locator.namespace!='sync':
            raise RunnerError('SOURCE_INPUT_UNSUPPORTED',409)
        try:
            current,inbox,route=read_receipt_in_tx(cursor,locator,lock=lock)
        except KfIngressError as error:
            raise RunnerError('SOURCE_BINDING_CHANGED',error.status) from error
        if inbox.get('receive_seq') is None:
            raise RunnerError('SOURCE_RECEIPT_NOT_FOUND',404)
        proof=current.proof
        provenance = {k:route[k] for k in ('tenant_id','source','config_id','corp_id','open_kfid',
            'actor_id','chat_kind','chat_id','profile_id','session_id','user_id','route_id')}
        provenance.update(account_id=proof.account_id,namespace=locator.namespace,
            message_id=locator.message_id,payload_digest=inbox['payload_digest'])
        binding={k:v for k,v in provenance.items() if k not in ('namespace','message_id','payload_digest')}
        provenance['execution_binding']=hashlib.sha256(encoded(binding).encode()).hexdigest()
        return current, dict(inbox), dict(route), provenance

    def link_in_tx(self,cursor,locator,input_ref):
        _,inbox,_,_=self.read_in_tx(cursor,locator,lock=True)
        cursor.execute("""UPDATE wecom_kf_inbox SET accepted_input_ref=%s WHERE account_id=%s
            AND namespace=%s AND message_id=%s AND tenant_id=%s
            AND (accepted_input_ref IS NULL OR accepted_input_ref=%s) RETURNING accepted_input_ref""",
            (input_ref,locator.account_id,locator.namespace,locator.message_id,inbox['tenant_id'],input_ref))
        if cursor.fetchone() is None:
            raise RunnerError('SOURCE_RECEIPT_CONFLICT',409)

    def _read(self, locator):
        with self.connection_factory() as conn:
            return self.read_in_tx(KfIngressRepository._cursor(conn),locator)

    def _record_observation(self,locator,service_id,provenance,observation,*,admission):
        self._peer(service_id,admission=admission)
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            current,inbox,route,fresh=self.read_in_tx(cursor,locator,lock=True)
            LifecycleRepository.assert_observation_in_tx(cursor,current,observation)
            if provenance!={**fresh,'io_config_version':current.proof.config_version.isoformat()}:
                raise RunnerError('SOURCE_STATE_UNAVAILABLE',409)
            if admission and not inbox.get('accepted_input_ref'):
                LifecycleRepository.record_observation_in_tx(cursor,inbox,route,observation)
            if observation.state==1:
                LifecycleRepository.assert_ai_in_tx(cursor,inbox,route)
            LifecycleRepository.assert_observation_in_tx(cursor,current,observation)
            conn.commit()

    async def prepare(self, locator, service_id, *, admission=True):
        self._peer(service_id,admission=admission)
        try:
            current,inbox,route,provenance = await _thread(self._read,locator)
            if inbox['origin']!=3 or inbox['message_type'] not in {'text','voice'}:
                raise RunnerError('SOURCE_INPUT_UNSUPPORTED',409)
            client = self.client_factory(current.proof.corp_id,current.secret)
            client.enable_ingress_mode(self.config.wecom_kf.page_bytes)
            try:
                async with asyncio.timeout(35):
                    result = await client.get_service_state(current.proof.open_kfid,inbox['actor_id'])
                    observed_at = datetime.now(timezone.utc)
                if (not isinstance(result,dict) or type(result.get('errcode')) is not int or result['errcode']!=0
                        or type(result.get('service_state')) is not int or result['service_state'] not in range(5)):
                    # No reliable observation, so no permanent classification.
                    raise RunnerError('SOURCE_STATE_UNAVAILABLE',409)
            finally:
                await _drain(asyncio.create_task(client.close()))
            provenance['io_config_version'] = current.proof.config_version.isoformat()
            observation=StateObservation(result['service_state'],observed_at,current.proof.config_version,inbox['payload_digest'],route_scope(route))
            # A reliable context state is retained even though AI admission is refused.
            await _thread(self._record_observation,locator,service_id,provenance,observation,admission=admission)
            if observation.state!=1:
                raise RunnerError('SOURCE_STATE_UNAVAILABLE',409)
            return PreparedSource(locator,provenance,observed_at)
        except KfIngressError as error:
            raise RunnerError('SOURCE_BINDING_CHANGED',409) from error

    def assert_prepared_in_tx(self, cursor, locator, service_id, *, prepared, admission=False):
        self._peer(service_id,admission=admission)
        if prepared is None or prepared.locator != locator:
            raise RunnerError('SOURCE_STATE_UNAVAILABLE',409)
        current,inbox,route,provenance = self.read_in_tx(cursor,locator,lock=True)
        cursor.execute('SELECT clock_timestamp() AS now')
        age=(cursor.fetchone()['now']-prepared.observed_at).total_seconds()
        if not 0<=age<=10 or prepared.provenance!={**provenance,'io_config_version':current.proof.config_version.isoformat()}:
            raise RunnerError('SOURCE_STATE_UNAVAILABLE',409)
        if inbox['origin']!=3 or inbox['message_type'] not in {'text','voice'}:
            raise RunnerError('SOURCE_INPUT_UNSUPPORTED',409)
        try:
            LifecycleRepository.assert_ai_in_tx(cursor,inbox,route)
        except KfIngressError as error:
            raise RunnerError('SOURCE_STATE_UNAVAILABLE',409) from error
        return current,inbox,route,provenance

    def authorize_in_tx(self, cursor, locator, service_id, *, prepared=None):
        current,inbox,route,provenance = self.assert_prepared_in_tx(cursor,locator,
            service_id,prepared=prepared,admission=True)
        if not inbox.get('accepted_input_ref'):
            cursor.execute("""SELECT 1 FROM wecom_kf_business_facts WHERE account_id=%s AND namespace=%s
                AND message_id=%s AND scope=%s::jsonb AND payload_digest=%s
                AND business_kind='account_blocked' AND phase='known' LIMIT 1""",
                (locator.account_id,locator.namespace,locator.message_id,encoded(route_scope(route)),inbox['payload_digest']))
            if cursor.fetchone():raise RunnerError('SOURCE_ACCOUNT_BLOCKED',409)
        # A preceding lifecycle/recall fact needs its later durable consumer;
        # this text-only gate cannot guess around it.
        cursor.execute('''SELECT 1 FROM wecom_kf_inbox WHERE account_id=%s AND actor_id=%s
            AND receive_seq<=%s AND (namespace='callback' OR message_type='event')
            AND NOT EXISTS (SELECT 1 FROM wecom_kf_receipt_classifications c
                WHERE c.account_id=wecom_kf_inbox.account_id AND c.namespace=wecom_kf_inbox.namespace
                AND c.message_id=wecom_kf_inbox.message_id AND c.classification_resolved) LIMIT 1''',
            (locator.account_id,inbox['actor_id'],inbox['receive_seq']))
        if cursor.fetchone():
            raise RunnerError('SOURCE_LIFECYCLE_PENDING',409)
        request = RunnerSubmit(client_request_id=locator.stable_key,source='wecom_kf',
            session=SessionRef(kind='channel',session_id=route['session_id']),
            channel_user_id=route['actor_id'],channel_chat_id=route['chat_id'],
            profile_id=route['profile_id'],text=inbox['payload']['text']['content'] if inbox['message_type']=='text' else '[语音消息]')
        return request, provenance, inbox['receive_seq']

    def authorize_row_in_tx(self, cursor, row, credentials=None, *, execute=False, prepared=None):
        initial = (row.get('checkpoint') or {}).get('source_initial_ref')
        if not initial:
            if execute and self.config.wecom_kf.enabled:
                raise SourceUnavailable('SOURCE_RECEIPT_REQUIRED')
            return
        if execute:
            self._peer(row['service_id'],admission=False)
        ref=credentials.get('source_input') if credentials is not None else initial
        if not ref: raise RunnerError('SOURCE_RECEIPT_REQUIRED',403)
        cursor.execute('SELECT * FROM agent_runner_inputs WHERE input_ref=%s',(ref,))
        fact = cursor.fetchone()
        if (not fact or (execute and fact['current_runner_id']!=row['runner_id'])
                or (not execute and row['runner_id'] not in (fact['current_runner_id'],fact['accepted_runner_id']))):
            if execute: raise SourceUnavailable('SOURCE_INPUT_OWNER_CHANGED')
            raise RunnerError('SOURCE_INPUT_FORBIDDEN',403)
        locator = SourceLocator(**fact['locator'])
        current,inbox,route,provenance = self.read_in_tx(cursor,locator,lock=execute)
        if fact['provenance']!=provenance or any(row[k]!=provenance[p] for k,p in (
                ('tenant_id','tenant_id'),('session_id','session_id'),('profile_id','profile_id'),('user_id','user_id'))):
            raise SourceUnavailable('SOURCE_INPUT_OWNER_CHANGED')
        if execute:
            self._assert_execution_source(cursor,inbox,route,locator,current,provenance,prepared)
        if credentials is not None:
            if (credentials.get('source_input')!=ref or credentials.get('actor_source')!='wecom_kf'
                    or credentials.get('actor_user')!=provenance['actor_id']
                    or credentials.get('actor_chat')!=provenance['chat_id']
                    or credentials.get('service_id')!=self.config.wecom_kf.service_id):
                raise RunnerError('SOURCE_SERVICE_FORBIDDEN',403)

    @staticmethod
    def _assert_execution_source(cursor,inbox,route,locator,current,provenance,prepared):
        try:LifecycleRepository.assert_ai_in_tx(cursor,inbox,route)
        except KfIngressError as error:raise SourceUnavailable('SOURCE_STATE_UNAVAILABLE') from error
        cursor.execute('SELECT clock_timestamp() AS now')
        age=(cursor.fetchone()['now']-prepared.observed_at).total_seconds() if prepared else -1
        if (prepared is None or prepared.locator!=locator or not 0<=age<=10
                or prepared.provenance!={**provenance,'io_config_version':current.proof.config_version.isoformat()}):
            raise SourceUnavailable('SOURCE_STATE_UNAVAILABLE')

    async def prepare_row(self, row):
        ref = (row.get('checkpoint') or {}).get('source_initial_ref')
        if not ref:
            if row['source']=='wecom_kf' and self.config.wecom_kf.enabled:
                raise SourceUnavailable('SOURCE_RECEIPT_REQUIRED')
            return
        def locator():
            with self.connection_factory() as conn:
                cursor=KfIngressRepository._cursor(conn)
                cursor.execute('SELECT locator FROM agent_runner_inputs WHERE input_ref=%s',(ref,))
                fact=cursor.fetchone()
                if not fact: raise SourceUnavailable('SOURCE_RECEIPT_NOT_FOUND')
                return SourceLocator(**fact['locator'])
        try:
            return await self.prepare(await _thread(locator),row['service_id'],admission=False)
        except (RunnerError,KfIngressError) as error:
            raise SourceUnavailable('SOURCE_STATE_UNAVAILABLE') from error

    def _read_batch(self, batch):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            values=[self.read_in_tx(cursor,locator) for locator in batch.members]
            scope=route_scope(values[0][2])
            version=values[0][0].proof.config_version
            if any(inbox['origin']!=3 or inbox['message_type']!='text'
                   or route_scope(route)!=scope or current.proof.config_version!=version
                   for current,inbox,route,_ in values):
                raise RunnerError('SOURCE_BATCH_BINDING_CHANGED',409)
            KfBatchRepository.assert_manifest_in_tx(cursor,batch,[value[3] for value in values])
            return values

    async def prepare_batch(self, batch, service_id):
        """Authenticate the real sealed manifest before any classification effect."""
        self._peer(service_id)
        values=await _thread(self._read_batch,batch)
        first=values[0][0]
        client=self.client_factory(first.proof.corp_id,first.secret)
        client.enable_ingress_mode(self.config.wecom_kf.page_bytes)
        try:
            async with asyncio.timeout(35):
                result=await client.get_service_state(first.proof.open_kfid,values[0][1]['actor_id'])
                observed_at=datetime.now(timezone.utc)
            if (not isinstance(result,dict) or type(result.get('errcode')) is not int or result['errcode']!=0
                    or type(result.get('service_state')) is not int or result['service_state'] not in range(5)):
                raise RunnerError('SOURCE_STATE_UNAVAILABLE',409)
        finally:
            await _drain(asyncio.create_task(client.close()))
        prepared=[]
        def record():
            with self.connection_factory() as conn:
                cursor=KfIngressRepository._cursor(conn)
                fresh_values=[]
                for locator,old in zip(batch.members,values):
                    current,inbox,route,provenance=self.read_in_tx(cursor,locator,lock=True)
                    if provenance!=old[3] or current.proof.config_version!=old[0].proof.config_version:
                        raise RunnerError('SOURCE_BATCH_BINDING_CHANGED',409)
                    fresh_values.append(provenance)
                    observation=StateObservation(result['service_state'],observed_at,
                        current.proof.config_version,inbox['payload_digest'],route_scope(route))
                    LifecycleRepository.assert_observation_in_tx(cursor,current,observation)
                    if not inbox.get('accepted_input_ref'):
                        LifecycleRepository.record_observation_in_tx(cursor,inbox,route,observation)
                    prepared.append(PreparedSource(locator,{**provenance,
                        'io_config_version':current.proof.config_version.isoformat()},observed_at))
                KfBatchRepository.assert_manifest_in_tx(cursor,batch,fresh_values)
                for current,_,_,_ in values:
                    LifecycleRepository.assert_observation_in_tx(cursor,current,
                        StateObservation(result['service_state'],observed_at,current.proof.config_version,
                                         values[0][1]['payload_digest'],route_scope(values[0][2])))
                conn.commit()
        await _thread(record)
        if result['service_state']!=1:
            raise RunnerError('SOURCE_STATE_UNAVAILABLE',409)
        return tuple(prepared)


class KfBatchRepository:
    """The two-second product window is a durable, bounded receipt manifest.

    All facts remain in the inbox. A member belongs to only one manifest, and
    every non-text fact is a barrier; this never cancels/replays an execution.
    """
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def collect(self, locator):
        from src.services.agent_runner.source_receipts import SourceBatch
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            current, first, route = read_receipt_in_tx(cursor, locator, lock=True)
            if (first['namespace'] != 'sync' or first['origin'] != 3
                    or first['message_type'] != 'text' or first.get('accepted_input_ref')):
                raise RunnerError('SOURCE_BATCH_UNSUPPORTED', 409)
            cursor.execute('''SELECT b.* FROM wecom_kf_input_batch_members m
                JOIN wecom_kf_input_batches b USING(batch_ref)
                WHERE m.account_id=%s AND m.namespace=%s AND m.message_id=%s FOR UPDATE OF b''',
                (locator.account_id, locator.namespace, locator.message_id))
            batch = cursor.fetchone()
            scope = route_scope(route)
            if batch is None:
                # Session/route locks serialize collectors. Only the earliest
                # eligible unassigned fact may open the two-second window;
                # a second consumer must not reserve C2 ahead of collecting C1.
                cursor.execute(f'''SELECT i.message_id FROM wecom_kf_inbox i
                    LEFT JOIN wecom_kf_receipt_classifications c USING(account_id,namespace,message_id)
                    WHERE i.account_id=%s AND i.route_id=%s AND i.namespace='sync'
                      AND i.origin=3 AND i.message_type='text' AND i.accepted_input_ref IS NULL
                      AND {LifecycleRepository.ai_candidate_sql()}
                      AND NOT EXISTS(SELECT 1 FROM wecom_kf_business_facts handled WHERE handled.account_id=i.account_id
                        AND handled.namespace=i.namespace AND handled.message_id=i.message_id
                        AND handled.payload_digest=i.payload_digest AND handled.business_kind='account_blocked' AND handled.phase='known')
                      AND NOT EXISTS(SELECT 1 FROM wecom_kf_input_batch_members m
                        WHERE m.account_id=i.account_id AND m.namespace=i.namespace AND m.message_id=i.message_id)
                    ORDER BY i.receive_seq LIMIT 1''', (locator.account_id, route['route_id']))
                anchor = cursor.fetchone()
                cursor.execute("SELECT 1 FROM wecom_kf_input_batches WHERE scope=%s::jsonb AND phase='collecting' LIMIT 1",
                               (encoded(scope),))
                if cursor.fetchone() or anchor is None or anchor['message_id'] != locator.message_id:
                    return None
                batch_ref = 'kf_batch_' + hashlib.sha256(locator.stable_key.encode()).hexdigest()
                cursor.execute('''INSERT INTO wecom_kf_input_batches(batch_ref,tenant_id,account_id,
                    scope,opened_at,deadline_at,phase) VALUES(%s,%s,%s,%s::jsonb,
                    clock_timestamp(),clock_timestamp()+interval '2 seconds','collecting')
                    ON CONFLICT(batch_ref) DO NOTHING''',
                    (batch_ref, first['tenant_id'], locator.account_id, encoded(scope)))
                cursor.execute('SELECT * FROM wecom_kf_input_batches WHERE batch_ref=%s FOR UPDATE', (batch_ref,))
                batch = cursor.fetchone()
            if batch is None or batch['scope'] != scope or batch['account_id'] != locator.account_id:
                raise RunnerError('SOURCE_BATCH_BINDING_CHANGED', 409)
            if batch['phase'] != 'accepted':
                cursor.execute('''SELECT i.*,c.classification FROM wecom_kf_inbox i
                    LEFT JOIN wecom_kf_receipt_classifications c USING(account_id,namespace,message_id)
                    WHERE i.account_id=%s AND i.receive_seq>=%s ORDER BY i.receive_seq LIMIT 33''',
                    (locator.account_id, first['receive_seq']))
                rows = cursor.fetchall()
                cursor.execute('DELETE FROM wecom_kf_input_batch_members WHERE batch_ref=%s',(batch['batch_ref'],))
                members, size, barrier = [], 0, False
                for row in rows:
                    if row['received_at']>batch['deadline_at']:
                        barrier=True
                        break
                    if (row['namespace'] != 'sync' or row['origin'] != 3 or row['message_type'] != 'text'
                            or row['route_id'] != route['route_id'] or row['actor_id'] != first['actor_id']
                            or row.get('accepted_input_ref')):
                        barrier = True
                        break
                    if row['classification'] not in (None,'ai') and not LifecycleRepository.transition_permit_in_tx(cursor,row,route):
                        barrier=True
                        break
                    if LifecycleRepository.recalled_in_tx(cursor,row,route):continue
                    cursor.execute("""SELECT 1 FROM wecom_kf_business_facts WHERE account_id=%s AND namespace=%s
                        AND message_id=%s AND scope=%s::jsonb AND payload_digest=%s
                        AND business_kind='account_blocked' AND phase='known' LIMIT 1""",
                        (row['account_id'],row['namespace'],row['message_id'],encoded(scope),row['payload_digest']))
                    if cursor.fetchone():continue
                    content = row['payload'].get('text', {}).get('content')
                    if not isinstance(content, str):
                        raise RunnerError('SOURCE_BATCH_BINDING_CHANGED', 409)
                    if len(members) == 32 or size + len(content.encode()) > 65536:
                        barrier = True
                        break
                    members.append(row)
                    size += len(content.encode())
                if not members:
                    cursor.execute("UPDATE wecom_kf_input_batches SET phase='sealed',sealed_at=COALESCE(sealed_at,clock_timestamp()) WHERE batch_ref=%s",(batch['batch_ref'],))
                    conn.commit();return None
                for ordinal, member in enumerate(members):
                    cursor.execute('''INSERT INTO wecom_kf_input_batch_members(batch_ref,ordinal,
                        account_id,namespace,message_id,payload_digest,receipt_seq)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(account_id,namespace,message_id) DO NOTHING''',
                        (batch['batch_ref'], ordinal, locator.account_id, member['namespace'],
                         member['message_id'], member['payload_digest'], member['receive_seq']))
                    cursor.execute('''SELECT batch_ref,ordinal,payload_digest FROM wecom_kf_input_batch_members
                        WHERE account_id=%s AND namespace=%s AND message_id=%s''',
                        (locator.account_id, member['namespace'], member['message_id']))
                    saved = cursor.fetchone()
                    if (saved is None or saved['batch_ref'] != batch['batch_ref']
                            or saved['ordinal'] != ordinal or saved['payload_digest'] != member['payload_digest']):
                        raise RunnerError('SOURCE_BATCH_CONFLICT', 409)
                cursor.execute('SELECT clock_timestamp() AS now')
                now = cursor.fetchone()['now']
                if barrier or now >= batch['deadline_at']:
                    cursor.execute("""UPDATE wecom_kf_input_batches SET phase='sealed',sealed_at=clock_timestamp()
                        WHERE batch_ref=%s AND phase='collecting'""", (batch['batch_ref'],))
                    batch = {**batch, 'phase': 'sealed'}
            cursor.execute('''SELECT account_id,namespace,message_id FROM wecom_kf_input_batch_members
                WHERE batch_ref=%s ORDER BY ordinal''', (batch['batch_ref'],))
            values = tuple(SourceLocator('wecom_kf', row['account_id'], row['namespace'], row['message_id'])
                           for row in cursor.fetchall())
            conn.commit()
            return SourceBatch(values) if batch['phase'] in {'sealed', 'accepted'} else None

    @staticmethod
    def assert_manifest_in_tx(cursor, batch, provenances, *, accepted_runner_id=None):
        """Use the sealed domain winner; caller member arrays are not proof."""
        first = batch.members[0]
        cursor.execute('''SELECT b.* FROM wecom_kf_input_batch_members m
            JOIN wecom_kf_input_batches b USING(batch_ref)
            WHERE m.account_id=%s AND m.namespace=%s AND m.message_id=%s FOR UPDATE OF b''',
            (first.account_id, first.namespace, first.message_id))
        manifest = cursor.fetchone()
        if manifest is None or manifest['phase'] not in {'sealed', 'accepted'}:
            raise RunnerError('SOURCE_BATCH_NOT_SEALED', 409)
        cursor.execute('SELECT * FROM wecom_kf_input_batch_members WHERE batch_ref=%s ORDER BY ordinal',
                       (manifest['batch_ref'],))
        saved = cursor.fetchall()
        if len(saved) != len(batch.members) or len(provenances) != len(saved):
            raise RunnerError('SOURCE_BATCH_CONFLICT', 409)
        for ordinal, (member, proof, stored) in enumerate(zip(batch.members, provenances, saved)):
            if (stored['ordinal'] != ordinal or (stored['account_id'], stored['namespace'], stored['message_id']) !=
                    (member.account_id, member.namespace, member.message_id)
                    or stored['payload_digest'] != proof['payload_digest']
                    or manifest['scope'] != {key: proof[key] for key in manifest['scope']}):
                raise RunnerError('SOURCE_BATCH_BINDING_CHANGED', 409)
            if manifest['phase']!='accepted':
                cursor.execute("""SELECT 1 FROM wecom_kf_business_facts WHERE account_id=%s AND namespace=%s
                    AND message_id=%s AND scope=%s::jsonb AND payload_digest=%s
                    AND business_kind='account_blocked' AND phase='known' LIMIT 1""",
                    (member.account_id,member.namespace,member.message_id,encoded(manifest['scope']),proof['payload_digest']))
                if cursor.fetchone():raise RunnerError('SOURCE_ACCOUNT_BLOCKED',409)
        if accepted_runner_id is not None:
            cursor.execute("""UPDATE wecom_kf_input_batches SET phase='accepted',accepted_runner_id=%s
                WHERE batch_ref=%s AND (accepted_runner_id IS NULL OR accepted_runner_id=%s)
                RETURNING batch_ref""", (accepted_runner_id, manifest['batch_ref'], accepted_runner_id))
            if cursor.fetchone() is None:
                raise RunnerError('SOURCE_BATCH_CONFLICT', 409)
        return manifest
