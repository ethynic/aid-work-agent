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





    def get(self, runner_id, credentials):
        self.authorizer.verify_service(credentials["service_id"], credentials["service_token"], None)
        row = self.repository.get(runner_id)
        self._authorize_row(row, credentials)
        return public_runner(row)

    def channel_result(self, runner_id, credentials):
        """原渠道历史 owner 读取本轮工具消息；没有执行、落库或投递副作用。"""
        import copy
        import json
        self.authorizer.verify_service(credentials['service_id'], credentials['service_token'], None)
        row = self.repository.get(runner_id)
        self._authorize_row(row, credentials)
        if row['source'] != 'wecom_kf' or row['session_kind'] != 'channel':
            raise RunnerError('CHANNEL_RESULT_FORBIDDEN', 403)
        messages = []
        if row['status'] == 'completed':
            from src.core.agent_events import mask_tool_args
            for message in (row.get('checkpoint') or {}).get('pending_finalization', {}).get('messages') or []:
                if message.get('role') == 'tool' or message.get('tool_calls'):
                    value = copy.deepcopy(message)
                    for call in value.get('tool_calls') or []:
                        function = call.get('function') or {}
                        arguments = function.get('arguments')
                        if isinstance(arguments, dict):
                            function['arguments'] = mask_tool_args(arguments)
                        elif isinstance(arguments, str):
                            try:
                                parsed = json.loads(arguments)
                                function['arguments'] = json.dumps(mask_tool_args(parsed), ensure_ascii=False) if isinstance(parsed, dict) else '{}'
                            except (TypeError, ValueError):
                                function['arguments'] = '{}'
                    messages.append(value)
        return {'success':True, 'runner':public_runner(row), 'messages':messages}

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
