"""Application requests: durable acceptance and projections, never execution ownership."""

from .contracts import RunnerSubmit, RunnerError, scope_key
from .repository import RunnerRepository, public_runner
from .event_read import RunnerEventRead


class RunnerManager:
    def __init__(self, repository, authorizer, profiles):
        self.repository, self.authorizer, self.profiles = repository, authorizer, profiles

    def read_events(self, runner_id, credentials, after_seq, *, limit=100, max_bytes=65_536):
        return RunnerEventRead(self.repository,self.authorizer).page(
            runner_id,credentials,after_seq,limit=limit,max_bytes=max_bytes)

    def submit(self, request, credentials, *, accept_new=True):
        principal = self.authorizer.authorize(request, execute=False, **credentials)
        if request.routing_policy == 'default_single' and (
                request.session.kind != 'web' or request.source != 'chat'
                or request.profile_id != 'main'
                or principal.service_id != self.authorizer.config.web_service_id):
            raise RunnerError('ROUTING_POLICY_FORBIDDEN', 403)
        existing = self.repository.find_request(principal, request)
        if existing:
            return public_runner(existing), False
        if not accept_new:
            raise RunnerError('WEB_SUBMISSIONS_DISABLED', 503)
        # 单请求字节闸在入口拒绝（413 背压），不落任何行；已受理的幂等重放不受影响。
        from .persistence_limits import assert_intent_within_request_limit
        assert_intent_within_request_limit(request.intent(), code='REQUEST_TOO_LARGE')
        profile_id = self.profiles.default_profile(principal.identity) if request.routing_policy == 'default_single' else request.profile_id
        execution_request = request.model_copy(update={'profile_id':profile_id})
        principal = self.authorizer.authorize(execution_request, **credentials)
        self.authorizer.assert_credit(principal)
        _, fingerprint = self.profiles.resolve(profile_id)
        from .input_projection import project_execution_context
        execution_context = project_execution_context(request, profile_id, fingerprint)
        row, created = self.repository.submit(principal, request, fingerprint, resolved_profile_id=profile_id,
                                              execution_context=execution_context)
        return public_runner(row), created

    def _authorize_row(self, row, credentials):
        if row['session_kind']=='channel' and self.authorizer.source_port is not None:
            return self.authorizer.authorize_row(row,credentials)
        intent = dict(row["input"])
        if row["session_kind"] == "channel":
            # A query cannot authenticate itself by copying the target's actor.
            # The trusted channel bridge supplies its current platform route.
            if credentials.get("actor_source") != row["source"] or not credentials.get("actor_user"):
                raise RunnerError("CHANNEL_ACTOR_REQUIRED", 403)
            intent["channel_user_id"] = credentials["actor_user"]
            intent["channel_chat_id"] = credentials.get("actor_chat")
        request = RunnerSubmit(client_request_id=row["client_request_id"], **intent)
        principal = self.authorizer.authorize(request, execute=False, **credentials)
        RunnerRepository.assert_owner(principal, row)
        return principal

    def find_source(self, locator, credentials):
        """An accepted receipt is readable even after its execution cutoff."""
        self.authorizer.verify_service(credentials['service_id'],credentials['service_token'],locator.source)
        port=self.authorizer.source_port
        if port is None: raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503)
        with self.repository.connection_factory() as conn:
            cursor=conn.cursor()
            fact,provenance=port.find_in_tx(cursor,locator,credentials['service_id'])
            if not fact: return None
            if provenance!=fact['provenance']: raise RunnerError('SOURCE_BINDING_CHANGED',409)
            cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(fact['current_runner_id'],))
            from .public_view import decoded
            row=decoded(cursor.fetchone())
            if row is None: raise RunnerError('RUNNER_NOT_FOUND',404)
            fresh={**credentials,'source_input':fact['input_ref'],'actor_source':locator.source,
                   'actor_user':provenance['actor_id'],'actor_chat':provenance['chat_id']}
            self.authorizer.authorize_read_in_tx(cursor,row,fresh)
            port.link_in_tx(cursor,locator,fact["input_ref"])
            conn.commit()
            return port.inputs.response(fact)

    def read_source(self,locator,credentials,*,presentation=False):
        """Pure receipt lookup and its current public execution state, one RR."""
        self.authorizer.verify_service(credentials['service_id'],credentials['service_token'],locator.source)
        port=self.authorizer.source_port
        if port is None: raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503)
        with self.repository.connection_factory() as conn:
            # This method owns a new short connection; checkout's SELECT 1
            # is not a caller transaction and must precede the RR boundary.
            conn.rollback()
            cursor=conn.cursor()
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cursor.execute("SET LOCAL statement_timeout='5s'")
            cursor.execute("SET LOCAL lock_timeout='3s'")
            fact,provenance=port.find_in_tx(cursor,locator,credentials['service_id'])
            if not fact: raise RunnerError('SOURCE_RECEIPT_NOT_FOUND',404)
            if provenance!=fact['provenance']: raise RunnerError('SOURCE_BINDING_CHANGED',409)
            cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(fact['current_runner_id'],))
            from .public_view import decoded
            row=decoded(cursor.fetchone())
            if row is None: raise RunnerError('RUNNER_NOT_FOUND',404)
            fresh={**credentials,'source_input':fact['input_ref'],'actor_source':locator.source,
                   'actor_user':provenance['actor_id'],'actor_chat':provenance['chat_id']}
            self.authorizer.authorize_read_in_tx(cursor,row,fresh)
            view=public_runner(row)
            state=view if presentation else {key:view[key] for key in ('runner_id','session','profile_id','status',
                'settlement_status','revision','view_revision','control_revision','resume_requested')}
            cursor.execute('SELECT clock_timestamp() AS now')
            observed_at=cursor.fetchone()['now'].isoformat()
            return {**port.inputs.response(fact),'locator':locator.value(),
                    'client_request_id':locator.stable_key,'runner':state,'observed_at':observed_at}

    def finish_source_delivery(self,locator,delivery_id,credentials):
        """The installed domain proves actual presentation outcomes, not caller ACK."""
        self.authorizer.verify_service(credentials['service_id'],credentials['service_token'],locator.source)
        port=self.authorizer.source_port
        if port is None: raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503)
        from .ownership import lock_runner
        # Original profile tasks are resolved outside the commit cursor. They
        # never confer execution or affect an already-known customer ACK.
        preview=self.read_source(locator,credentials)
        from .contracts import canonical_json
        profile_config,profile_fingerprint=self.profiles.resolve(preview['runner']['profile_id'])
        from src.services.recap.runner import parse_recap_tasks
        recap_tasks=[task.name for task in parse_recap_tasks(getattr(profile_config,'recap',None))]
        with self.repository.connection_factory() as conn:
            cursor=conn.cursor()
            fact=port.inputs.find_in_tx(cursor,locator)
            if fact is None: raise RunnerError('SOURCE_RECEIPT_NOT_FOUND',404)
            row=lock_runner(cursor,fact['current_runner_id'])
            cursor.execute('SELECT * FROM agent_runner_session_claims WHERE scope_key=%s AND session_kind=%s AND session_id=%s FOR UPDATE',
                (row['scope_key'],row['session_kind'],row['session_id']))
            claim=cursor.fetchone()
            fresh_fact,provenance=port.find_in_tx(cursor,locator,credentials['service_id'])
            if fresh_fact is None or fresh_fact['current_runner_id']!=row['runner_id'] or provenance!=fresh_fact['provenance']:
                raise RunnerError('SOURCE_BINDING_CHANGED',409)
            fresh={**credentials,'source_input':fact['input_ref'],'actor_source':locator.source,
                'actor_user':provenance['actor_id'],'actor_chat':provenance['chat_id']}
            self.authorizer.authorize_read_in_tx(cursor,row,fresh)
            proof=port.finish_delivery_in_tx(cursor,locator,fresh_fact,row,delivery_id,
                recap_tasks=recap_tasks if profile_fingerprint==row['profile_fingerprint'] else ())
            if claim is not None:
                if claim['owner_runner_id']!=row['runner_id'] or claim['gate']!='delivery':
                    raise RunnerError('SOURCE_DELIVERY_CLAIM_CHANGED',409)
                cursor.execute("DELETE FROM agent_runner_session_claims WHERE scope_key=%s AND session_kind=%s AND session_id=%s AND owner_runner_id=%s AND gate='delivery'",
                    (row['scope_key'],row['session_kind'],row['session_id'],row['runner_id']))
                if cursor.rowcount!=1: raise RunnerError('SOURCE_DELIVERY_CLAIM_CHANGED',409)
            conn.commit()
            return {'success':True,'current_runner_id':row['runner_id'],'delivery_id':delivery_id,'outcome':proof}

    async def prepare_source(self, locator, credentials):
        self.authorizer.verify_service(credentials['service_id'],credentials['service_token'],locator.source)
        if self.authorizer.source_port is None: raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503)
        return await self.authorizer.source_port.prepare(locator,credentials['service_id'])

    def find_source_batch(self, batch, credentials):
        """Lost replies resolve the original complete membership, not new work."""
        values = [self.find_source(locator, credentials) for locator in batch.members]
        if not any(values):
            return None
        if not all(values):
            raise RunnerError('SOURCE_BATCH_CONFLICT', 409)
        port = self.authorizer.source_port
        with self.repository.connection_factory() as conn:
            cursor = conn.cursor()
            facts = [port.inputs.find_in_tx(cursor, locator) for locator in batch.members]
            port.inputs._assert_batch_members_in_tx(cursor, batch, facts)
        return {'success': True, 'batch_ref': batch.stable_key,
                'current_runner_id': values[0]['current_runner_id'], 'members': values, 'created': False}

    async def prepare_source_batch(self, batch, credentials):
        self.authorizer.verify_service(credentials['service_id'], credentials['service_token'], batch.members[0].source)
        port = self.authorizer.source_port
        if port is None:
            raise RunnerError('SOURCE_SERVICE_UNAVAILABLE', 503)
        return await port.prepare_batch(batch, credentials['service_id'])

    def _source_reply_in_tx(self,cursor,owner,principal,requests,provenance,stable_key):
        if owner is None or owner['status'] not in {'waiting','paused','interrupted'}:return None
        port=self.authorizer.source_port
        cursor.execute('SELECT provenance FROM agent_runner_inputs WHERE input_ref=%s',
                       ((owner.get('checkpoint') or {}).get('source_initial_ref'),))
        initial=cursor.fetchone()
        if initial is None or initial['provenance']['execution_binding']!=provenance['execution_binding']:
            return None
        if any(request.attachments for request in requests):raise RunnerError('SOURCE_REPLY_UNSUPPORTED',409)
        target,wait=port.question_reply_in_tx(cursor,owner,provenance)
        from .control_contracts import RunnerControl
        from .control_repository import ControlRepository
        request=RunnerControl(client_request_id=stable_key,action='reply',target_execution_id=target,
                              wait_id=wait,answer='\n'.join(request.text for request in requests))
        control,_=ControlRepository(self.repository.connection_factory).submit_in_tx(cursor,principal,
            owner['runner_id'],request,expected_revision=owner['revision'])
        return control['control_id']

    def accept_source_batch(self, batch, stable_key, credentials, prepared):
        if stable_key != batch.stable_key or len(prepared) != len(batch.members):
            raise RunnerError('INVALID_SOURCE_BATCH', 422)
        self.authorizer.verify_service(credentials['service_id'], credentials['service_token'], batch.members[0].source)
        port = self.authorizer.source_port
        if port is None:
            raise RunnerError('SOURCE_SERVICE_UNAVAILABLE', 503)
        profile = prepared[0].provenance['profile_id']
        _, fingerprint = self.profiles.resolve(profile)
        from .ownership import lock_runner
        from .input_projection import project_execution_context
        for _ in range(3):
            with self.repository.connection_factory() as conn:
                cursor = conn.cursor()
                cursor.execute("SET LOCAL statement_timeout='5s'")
                cursor.execute("SET LOCAL lock_timeout='3s'")
                cursor.execute('''SELECT owner_runner_id FROM agent_runner_session_claims
                    WHERE scope_key=%s AND session_kind='channel' AND session_id=%s''',
                    (scope_key(prepared[0].provenance['tenant_id']), prepared[0].provenance['session_id']))
                candidate = cursor.fetchone()
                owner = lock_runner(cursor, candidate['owner_runner_id']) if candidate else None
                if owner is not None:
                    cursor.execute('''SELECT owner_runner_id FROM agent_runner_session_claims
                        WHERE scope_key=%s AND session_kind='channel' AND session_id=%s FOR UPDATE''',
                        (owner['scope_key'], owner['session_id']))
                    actual = cursor.fetchone()
                    if not actual or actual['owner_runner_id'] != owner['runner_id']:
                        continue
                values, principal = [], None
                for locator, proof in zip(batch.members, prepared):
                    member_principal, request, provenance, ordinal = self.authorizer.authorize_source_in_tx(
                        cursor, locator, credentials['service_id'], proof)
                    if principal is not None and member_principal.identity != principal.identity:
                        raise RunnerError('SOURCE_BATCH_BINDING_CHANGED', 409)
                    principal = member_principal
                    values.append((request, provenance, ordinal))
                port.assert_batch_in_tx(cursor, batch, [value[1] for value in values])
                if owner is None:
                    cursor.execute('''SELECT 1 FROM agent_runner_session_claims WHERE scope_key=%s
                        AND session_kind='channel' AND session_id=%s''',
                        (principal.scope_key, principal.identity.session_id))
                    if cursor.fetchone():
                        continue
                source_control=self._source_reply_in_tx(cursor,owner,principal,[value[0] for value in values],values[0][1],batch.stable_key)
                context = project_execution_context(values[0][0], profile, fingerprint)
                result = port.inputs.accept_batch_in_tx(cursor, batch, values, principal,
                    self.repository, fingerprint, context, owner,source_control)
                for locator, value, proof in zip(batch.members, result['members'], prepared):
                    port.link_in_tx(cursor, locator, value['input_ref'])
                    # Every blocking source/member write has completed. Recheck
                    # original authority and actual observation age at the tail.
                    self.authorizer.authorize_source_in_tx(cursor, locator, credentials['service_id'], proof)
                port.assert_batch_in_tx(cursor, batch, [value[1] for value in values],
                                        accepted_runner_id=result['current_runner_id'])
                conn.commit()
                return result
        raise RunnerError('SOURCE_ACCEPT_RETRY_REQUIRED', 409)

    def accept_source(self, locator, stable_key, credentials, prepared):
        if stable_key!=locator.stable_key: raise RunnerError('INVALID_SOURCE_RECEIPT',422)
        self.authorizer.verify_service(credentials['service_id'],credentials['service_token'],locator.source)
        port=self.authorizer.source_port
        if port is None: raise RunnerError('SOURCE_SERVICE_UNAVAILABLE',503)
        # Resolve actual profile files outside the locked acceptance transaction.
        profile=prepared.provenance['profile_id']
        _,fingerprint=self.profiles.resolve(profile)
        from .ownership import lock_runner
        from .input_projection import project_execution_context
        for _ in range(3):
            with self.repository.connection_factory() as conn:
                cursor=conn.cursor()
                cursor.execute("SET LOCAL statement_timeout='5s'")
                cursor.execute("SET LOCAL lock_timeout='3s'")
                cursor.execute('''SELECT c.owner_runner_id FROM agent_runner_session_claims c
                    WHERE scope_key=%s AND session_kind='channel' AND session_id=%s''',
                    (scope_key(prepared.provenance['tenant_id']),prepared.provenance['session_id']))
                candidate=cursor.fetchone()
                owner=lock_runner(cursor,candidate['owner_runner_id']) if candidate else None
                if owner is not None:
                    cursor.execute('''SELECT owner_runner_id FROM agent_runner_session_claims
                        WHERE scope_key=%s AND session_kind='channel' AND session_id=%s FOR UPDATE''',
                        (owner['scope_key'],owner['session_id']))
                    actual=cursor.fetchone()
                    if not actual or actual['owner_runner_id']!=owner['runner_id']: continue
                principal,request,provenance,ordinal=self.authorizer.authorize_source_in_tx(
                    cursor,locator,credentials['service_id'],prepared)
                # A newly acquired root was absent from the unlocked candidate.
                # Roll back before taking its root, never upgrade cfg->root.
                if owner is None:
                    cursor.execute('''SELECT 1 FROM agent_runner_session_claims WHERE scope_key=%s
                        AND session_kind='channel' AND session_id=%s''',(principal.scope_key,principal.identity.session_id))
                    if cursor.fetchone(): continue
                source_control=self._source_reply_in_tx(cursor,owner,principal,[request],provenance,locator.stable_key)
                context=project_execution_context(request,profile,fingerprint)
                value=port.inputs.accept_in_tx(cursor,locator,request,provenance,ordinal,principal,
                    self.repository,fingerprint,context,owner,source_control)
                port.link_in_tx(cursor,locator,value["input_ref"])
                self.authorizer.authorize_source_in_tx(cursor,locator,credentials['service_id'],prepared)
                conn.commit()
                return value
        raise RunnerError('SOURCE_ACCEPT_RETRY_REQUIRED',409)

    def get(self, runner_id, credentials):
        self.authorizer.verify_service(credentials["service_id"], credentials["service_token"], None)
        row = self.repository.get(runner_id)
        self._authorize_row(row, credentials)
        return public_runner(row)

    def cancel(self, runner_id, credentials):
        self.authorizer.verify_service(credentials["service_id"], credentials["service_token"], None)
        row = self.repository.get(runner_id)
        principal = self._authorize_row(row, credentials)
        return public_runner(self.repository.cancel(principal, runner_id))

    def list_session(self, request, credentials, limit=100, before_runner_id=None):
        principal = self.authorizer.authorize(request, execute=False, **credentials)
        page = self.repository.list_session(principal, limit, before_runner_id)
        return {"runners": [public_runner(row) for row in page["rows"]],
                "active_runners": [public_runner(row) for row in page["active"]],
                "has_more": page["has_more"], "next_cursor": page["next_cursor"]}
