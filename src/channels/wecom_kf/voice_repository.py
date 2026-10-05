"""Accepted voice preparation and its original physical fee owner, in short TXs."""

import hashlib
import json
from src.db.database import get_db_connection
from src.services.agent_runner.ownership import lock_runner, Attempt
from src.services.agent_runner.input_repository import InputRepository
from src.services.agent_runner.source_receipts import SourceLocator, SourceUnavailable, PreparationCandidate
from src.services.agent_runner.usage_repository import UsageRepository
from .ingress_repository import KfIngressRepository


OPERATION_VERSION = 1
CHAT_PLACEHOLDER = 'SOURCE_PREPARATION_CHAT_PLACEHOLDER'


def preparation_id(input_ref):
    return 'voice_' + hashlib.sha256(f'{input_ref}:{OPERATION_VERSION}'.encode()).hexdigest()


class VoiceRepository:
    def __init__(self, provider, connection_factory=get_db_connection, usage=None):
        self.provider, self.connection_factory = provider, connection_factory
        self.usage = usage or UsageRepository(connection_factory)

    preparation_id=staticmethod(preparation_id)

    @staticmethod
    def projection_in_tx(cursor, fact):
        cursor.execute('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s',
                       (fact['input_ref'], OPERATION_VERSION))
        prep = cursor.fetchone()
        if not prep:
            return None
        if (prep['intent_digest'] != fact['intent_digest'] or prep['provenance'] != fact['provenance']):
            raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
        placeholder = prep['error_code'] == CHAT_PLACEHOLDER
        if prep['phase'] != 'known' and not placeholder:
            return None
        text = prep['transcript'] or ''
        success = prep['success'] and not placeholder
        model_text = ('[ASR识别结果] ' + text if len(text) < 10 else text) if success else '[语音消息]'
        history_text = '[ASR识别结果] ' + text if success else '[语音消息]'
        attachments = [{'type':'voice','media_id':prep['media_id']}]
        if prep['artifact']:
            attachments = [{'type':'voice','media_id':prep['media_id'],**prep['artifact']}]
        return {'model_text': model_text, 'history_text': history_text,
                'attachments': attachments, 'preparation_ref': prep['preparation_ref']}

    def choose_placeholder(self, fact):
        """Continue this chat without asserting a result for the paid operation.

        The choice survives a late observation: settlement may advance, while
        the already selected model/history input stays the original placeholder.
        """
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            lock_runner(cursor, fact['current_runner_id'])
            cursor.execute('SELECT * FROM agent_runner_inputs WHERE input_ref=%s FOR UPDATE',
                           (fact['input_ref'],))
            current = cursor.fetchone()
            if (not current or any(current[key] != fact[key] for key in
                    ('current_runner_id', 'intent_digest', 'provenance'))):
                raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
            cursor.execute('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s FOR UPDATE',
                           (fact['input_ref'], OPERATION_VERSION))
            prep = cursor.fetchone()
            if (not prep or prep['intent_digest'] != fact['intent_digest']
                    or prep['provenance'] != fact['provenance'] or not prep['receipt_id']):
                raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
            if prep['error_code']=='SOURCE_PREPARATION_OBSERVATION_FAILED' and prep['phase']!='known':
                raise SourceUnavailable('SOURCE_PREPARATION_OBSERVATION_FAILED')
            if prep['phase'] in {'started', 'unknown'}:
                cursor.execute('UPDATE wecom_kf_input_preparations SET error_code=%s WHERE preparation_ref=%s',
                               (CHAT_PLACEHOLDER, prep['preparation_ref']))
            conn.commit()

    @staticmethod
    def cancel_proof_in_tx(cursor,row,checkpoint):
        if row.get('cancel_requested') is not True:
            return None
        cursor.execute('SELECT * FROM agent_runner_inputs WHERE current_runner_id=%s ORDER BY ordinal FOR UPDATE',
                       (row['runner_id'],))
        facts=cursor.fetchall()
        preparations={}
        for fact in facts:
            cursor.execute('''SELECT * FROM wecom_kf_input_preparations
                WHERE input_ref=%s AND operation_version=%s FOR UPDATE''',
                (fact['input_ref'],OPERATION_VERSION))
            prep=cursor.fetchone()
            if prep is None:
                continue
            if (prep['intent_digest']!=fact['intent_digest'] or prep['provenance']!=fact['provenance']):
                raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
            if prep['phase'] in {'started','unknown'}:
                # Cancellation does not prove a dispatched ASR result. Keep the
                # original input/claim/fee owner until its result is reconciled.
                raise SourceUnavailable('SOURCE_PREPARATION_RESULT_UNKNOWN')
            preparations[fact['input_ref']]=prep
        if (any(checkpoint.get(k) for k in ('execution','children','tools'))
                or any((row.get('checkpoint') or {}).get(k) for k in ('execution','children','tools'))):
            return None
        initial=checkpoint.get('source_initial_ref')
        initial_fact=next((fact for fact in facts if fact['input_ref']==initial),None)
        if not initial_fact or initial_fact['phase'] not in {'accepted','deferred','cancelled'}: return None
        locator=initial_fact['locator']
        cursor.execute('SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
                       (locator['account_id'],locator['namespace'],locator['message_id']))
        inbox=cursor.fetchone()
        if (not inbox or inbox['message_type']!='voice' or inbox['origin']!=3
                or inbox['payload_digest']!=initial_fact['provenance']['payload_digest']): return None
        prep_ids=[];receipt_ids=[]
        for fact in facts:
            if fact['phase'] not in {'accepted','deferred','cancelled'}: return None
            prep=preparations.get(fact['input_ref'])
            if prep is None:
                continue
            if (prep['phase']!='known' or prep['intent_digest']!=fact['intent_digest']
                    or prep['provenance']!=fact['provenance']): return None
            prep_ids.append(prep['preparation_ref'])
            if prep['receipt_id']:
                cursor.execute('SELECT * FROM agent_runner_usage_receipts WHERE receipt_id=%s',(prep['receipt_id'],))
                receipt=cursor.fetchone()
                if (not receipt or receipt['phase']!='observed' or receipt['owner']!='asr'
                        or receipt['provider']!='aliyun' or receipt['model']!='aliyun-nls-asr'
                        or receipt['runner_id']!=prep['fee_owner_runner_id']
                        or receipt['authorized_attempt']!=prep['authorized_attempt']): return None
                receipt_ids.append(prep['receipt_id'])
        # No model/other physical operation occurred in this cancelling root.
        cursor.execute('SELECT receipt_id FROM agent_runner_usage_receipts WHERE runner_id=%s', (row['runner_id'],))
        if not {item['receipt_id'] for item in cursor.fetchall()}.issubset(set(receipt_ids)): return None
        return {'version':1,'initial_ref':initial,'preparation_refs':sorted(prep_ids),'receipt_ids':sorted(receipt_ids)}

    def candidates(self, runner_id):
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            cursor.execute("""SELECT * FROM agent_runner_inputs WHERE current_runner_id=%s
                AND phase IN ('accepted','attached','deferred') ORDER BY ordinal LIMIT 128""", (runner_id,))
            result = []
            for fact in cursor.fetchall():
                locator = SourceLocator(**fact['locator'])
                _, inbox, _, provenance = self.provider.read_in_tx(cursor, locator)
                if fact['provenance'] != provenance:
                    raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
                if inbox['message_type'] == 'voice':
                    cursor.execute('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s',
                                   (fact['input_ref'], OPERATION_VERSION))
                    result.append(PreparationCandidate(dict(fact),inbox['payload']['voice'].get('recognition'),cursor.fetchone()))
            return result

    def _scope(self, cursor, attempt, fact, prepared):
        row = lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
        InputRepository.lock_claim(cursor, row)
        locator = SourceLocator(**fact['locator'])
        _, inbox, _, provenance = self.provider.assert_prepared_in_tx(cursor, locator,
            row['service_id'], prepared=prepared, admission=False)
        cursor.execute('SELECT * FROM agent_runner_inputs WHERE input_ref=%s FOR UPDATE', (fact['input_ref'],))
        current = cursor.fetchone()
        if (not current or current['current_runner_id'] != row['runner_id']
                or current['intent_digest'] != fact['intent_digest'] or current['provenance'] != provenance
                or current['phase'] not in {'accepted','attached','deferred'}):
            raise SourceUnavailable('SOURCE_PREPARATION_INPUT_CHANGED')
        if inbox['origin'] != 3 or inbox['message_type'] != 'voice':
            raise SourceUnavailable('SOURCE_PREPARATION_INPUT_CHANGED')
        return row, inbox

    def remember(self, attempt, fact, prepared, *, artifact=None, recognition=None, failed=False):
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            _, inbox = self._scope(cursor, attempt, fact, prepared)
            voice = inbox['payload']['voice']
            if recognition is not None and recognition != voice.get('recognition'):
                raise SourceUnavailable('SOURCE_RECOGNITION_CHANGED')
            phase = 'known' if recognition is not None or failed else 'media_ready'
            cursor.execute("""INSERT INTO wecom_kf_input_preparations
                (preparation_ref,input_ref,operation_version,tenant_id,intent_digest,provenance,media_id,
                 phase,artifact,transcript,success,result_kind,observed_at)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,CASE WHEN %s='known' THEN clock_timestamp() ELSE NULL END)
                ON CONFLICT(input_ref,operation_version) DO NOTHING RETURNING *""",
                (preparation_id(fact['input_ref']),fact['input_ref'],OPERATION_VERSION,fact['provenance']['tenant_id'],fact['intent_digest'],
                 json.dumps(fact['provenance']),voice['media_id'],phase,json.dumps(artifact) if artifact else None,
                 recognition if recognition is not None else '' if failed else None,recognition is not None, 'recognition' if recognition is not None else 'preflight' if failed else None,phase))
            prep = cursor.fetchone()
            if prep is None:
                cursor.execute('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s FOR UPDATE',
                               (fact['input_ref'], OPERATION_VERSION))
                prep = cursor.fetchone()
                if prep['intent_digest'] != fact['intent_digest'] or prep['provenance'] != fact['provenance']:
                    raise SourceUnavailable('SOURCE_PREPARATION_BINDING_CHANGED')
            self.provider.assert_prepared_in_tx(cursor, SourceLocator(**fact['locator']),
                service_id=lock_runner(cursor,attempt.runner_id,attempt,dispatch=True)['service_id'],
                prepared=prepared, admission=False)
            lock_runner(cursor,attempt.runner_id,attempt,dispatch=True)
            conn.commit()
            return dict(prep)

    def start(self, attempt, fact, prepared, artifact, price_snapshot):
        if (not isinstance(price_snapshot,dict) or type(price_snapshot.get('version')) is not int
                or price_snapshot['version']!=1 or not isinstance(price_snapshot.get('price'),dict)):
            raise SourceUnavailable('SOURCE_ASR_PRICE_UNAVAILABLE')
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            self._scope(cursor,attempt,fact,prepared)
            cursor.execute('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s FOR UPDATE',
                           (fact['input_ref'],OPERATION_VERSION))
            prep = cursor.fetchone()
            if not prep or prep['phase'] != 'media_ready' or prep['artifact'] != artifact:
                raise SourceUnavailable('SOURCE_PREPARATION_ALREADY_STARTED')
            call_id = preparation_id(fact['input_ref']) + ':asr'
            receipt = self.usage.start_in_tx(cursor,attempt,call_id=call_id,execution_id=attempt.runner_id,
                owner='asr',purpose='source_voice',boundary='main',provider='aliyun',model='aliyun-nls-asr',
                price_snapshot=price_snapshot)
            cursor.execute("""UPDATE wecom_kf_input_preparations SET phase='started',
                fee_owner_runner_id=%s,authorized_attempt=%s,authorized_worker_id=%s,
                physical_call_id=%s,receipt_id=%s,started_at=clock_timestamp(),
                io_config_version=%s::timestamptz,dispatch_observed_at=%s::timestamptz
                WHERE preparation_ref=%s""", (attempt.runner_id,attempt.number,attempt.worker_id,
                    call_id,receipt['receipt_id'],prepared.provenance['io_config_version'],prepared.observed_at,prep['preparation_ref']))
            # All blocking SQL completed: recheck the original dispatch/age fence.
            row = lock_runner(cursor,attempt.runner_id,attempt,dispatch=True)
            self.provider.assert_prepared_in_tx(cursor,SourceLocator(**fact['locator']),
                row['service_id'],prepared=prepared,admission=False)
            lock_runner(cursor,attempt.runner_id,attempt,dispatch=True)
            conn.commit()
            return receipt

    def result(self, input_ref, *, success, text, status):
        # This is observation of an already started operation, not a new dispatch.
        if (type(success) is not bool or type(status) is not int or not -(2**63)<=status<2**63
                or not isinstance(text,str) or len(text.encode())>32768 or '\x00' in text
                or (success and not text.strip()) or (not success and text!='')):
            raise SourceUnavailable('SOURCE_PREPARATION_RESULT_INVALID')
        with self.connection_factory() as conn:
            cursor = KfIngressRepository._cursor(conn)
            cursor.execute('SELECT fee_owner_runner_id FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s',
                           (input_ref,OPERATION_VERSION))
            owner = cursor.fetchone()
            if not owner or not owner['fee_owner_runner_id']:
                raise SourceUnavailable('SOURCE_PREPARATION_OWNER_MISSING')
            lock_runner(cursor,owner['fee_owner_runner_id'])
            cursor.execute('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s FOR UPDATE',
                           (input_ref,OPERATION_VERSION))
            prep = cursor.fetchone()
            if prep['phase'] == 'known':
                if (prep['success'],prep['transcript'],prep['provider_status']) != (success,text,status):
                    raise SourceUnavailable('SOURCE_PREPARATION_RESULT_CONFLICT')
                return
            if prep['phase'] not in {'started','unknown'}:
                raise SourceUnavailable('SOURCE_PREPARATION_NOT_STARTED')
            attempt = Attempt(prep['fee_owner_runner_id'],prep['authorized_worker_id'],prep['authorized_attempt'])
            self.usage.observe_in_tx(cursor,attempt,prep['receipt_id'],{'calls':int(success)},
                provider='aliyun',model='aliyun-nls-asr')
            cursor.execute("""UPDATE wecom_kf_input_preparations SET phase='known',success=%s,
                transcript=%s,provider_status=%s,result_kind='provider',observed_at=clock_timestamp()
                WHERE preparation_ref=%s""", (success,text,status,prep['preparation_ref']))
            conn.commit()

    def uncertain(self, input_ref, code):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute('SELECT fee_owner_runner_id FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s',
                           (input_ref,OPERATION_VERSION))
            owner=cursor.fetchone()
            if not owner or not owner['fee_owner_runner_id']: return
            lock_runner(cursor,owner['fee_owner_runner_id'])
            cursor.execute('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s AND operation_version=%s FOR UPDATE',
                           (input_ref,OPERATION_VERSION))
            prep=cursor.fetchone()
            if prep['phase'] not in {'started','unknown'}: return
            attempt=Attempt(prep['fee_owner_runner_id'],prep['authorized_worker_id'],prep['authorized_attempt'])
            self.usage.uncertain_in_tx(cursor,attempt,prep['receipt_id'])
            cursor.execute("""UPDATE wecom_kf_input_preparations SET phase='unknown',
                error_code=CASE WHEN error_code=%s THEN error_code ELSE %s END WHERE preparation_ref=%s""",
                           (CHAT_PLACEHOLDER,code,prep['preparation_ref']))
            conn.commit()

    def preflight_failed(self, input_ref):
        with self.connection_factory() as conn:
            cursor=KfIngressRepository._cursor(conn)
            cursor.execute("""UPDATE wecom_kf_input_preparations SET phase='known',success=false,
                transcript='',result_kind='preflight',observed_at=clock_timestamp()
                WHERE input_ref=%s AND operation_version=%s AND phase='media_ready'""", (input_ref,OPERATION_VERSION))
            conn.commit()
