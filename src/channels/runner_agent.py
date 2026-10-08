"""渠道只观察独立 Runner 的执行；合并、历史与投递仍由原渠道负责。"""

import asyncio
import inspect
import uuid
from dataclasses import asdict
from urllib.parse import quote

from src.core.agent import AgentResponse
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.web_client import RunnerServiceClient


def _clarification_wait(waiting):
    """主 Agent 委派的追问也由原渠道转交给用户。"""
    for _ in range(32):
        if not isinstance(waiting, dict):
            return None
        if waiting.get('kind') == 'clarification':
            return waiting
        if waiting.get('kind') not in {'child_wait', 'child_clarification'}:
            return None
        waiting = waiting.get('child_wait')
    return None


class ChannelRunnerAgent:
    def __init__(self, *, source, session_id, channel_user_id, channel_chat_id,
                 profile_id='main', config_id=None, client=None):
        from src.config.settings import settings
        self.source, self.session_id = source, session_id
        self.channel_user_id, self.channel_chat_id = channel_user_id, channel_chat_id
        self.profile_id, self.config_id = profile_id or 'main', config_id
        self.client = client or RunnerServiceClient(settings.agent_runner)

    @property
    def subagent_config(self):
        # 原 recap 只需配置，不在渠道进程装配模型或执行 Agent。
        from src.services.agent_runner.profiles import MainProfileCatalog
        return MainProfileCatalog().resolve(self.profile_id)[0]

    async def _request(self, method, path, **kwargs):
        for attempt in range(4):
            try:
                return await self.client.request(method, path, authorization=None,
                    channel_source=self.source, channel_user=self.channel_user_id,
                    channel_chat=self.channel_chat_id, **kwargs)
            except RunnerError as error:
                if error.code != 'RUNNER_SERVICE_UNAVAILABLE' or attempt == 3:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))

    async def _accept(self, path, body, runner_id=None):
        # HTTP 接受响应窗口内的取消不能留下已受理执行；幂等键始终不变。
        accepting = asyncio.create_task(self._request('POST', path, body=body))
        try:
            return await asyncio.shield(accepting)
        except asyncio.CancelledError:
            async def cleanup():
                try:
                    response = await accepting
                    accepted_id = response['runner']['runner_id']
                except Exception:
                    if not runner_id:
                        raise
                    accepted_id = runner_id
                await self._cancel_and_wait(accepted_id)
            await self._finish_cleanup(cleanup())
            raise

    async def _finish_cleanup(self, operation):
        cleanup = asyncio.create_task(operation)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            cleanup.result()
        except Exception as error:
            from loguru import logger
            logger.error('Channel Runner cancellation unconfirmed: {}', type(error).__name__)

    async def process_message_sync(self, *, user_input, session_id, attachments=None,
                                   extra_system_prompt=None, progress_callback=None,
                                   cancel_check=None, feedback_state=None, **kwargs):
        if session_id != self.session_id:
            raise RunnerError('CHANNEL_SESSION_MISMATCH', 403)
        if cancel_check and cancel_check():
            return AgentResponse('')
        request_id = uuid.uuid4().hex
        params = {'source':self.source, 'profile_id':self.profile_id,
                  'channel_user_id':self.channel_user_id, 'channel_chat_id':self.channel_chat_id}
        page = await self._request('GET', '/v1/sessions/channel/' + quote(session_id, safe='') + '/runners', params=params)
        active = page.get('active_runners') or []
        waiting = next((row for row in active if row['status']=='waiting'), None)
        refs = [{key:value for key,value in item.items() if key in
                 {'file_id','name','type','mime_type','content','size'}} for item in attachments or []]
        for ref in refs:
            ref['type'] = 'image' if ref.get('type') == 'image' else 'file'
            if ref.get('file_id'):
                ref.pop('content', None)
        reply_wait_id = None
        reply_control_id = None
        if waiting:
            wait = _clarification_wait((waiting.get('snapshot') or {}).get('waiting')) or {}
            # 回复已停车的追问；普通新消息不留着旧会话 claim 无期限排队。
            if wait.get('kind') == 'clarification' and wait.get('wait_id') and wait.get('target_execution_id'):
                accepted = await self._accept(f"/v1/runners/{waiting['runner_id']}/controls", {
                    'action':'reply', 'client_request_id':request_id, 'answer':user_input,
                    'attachments':refs, 'wait_id':wait['wait_id'],
                    'target_execution_id':wait['target_execution_id']}, waiting['runner_id'])
                row = accepted['runner']
                reply_wait_id, reply_control_id = wait['wait_id'], accepted['control']['control_id']
            else:
                await self._cancel_and_wait(waiting['runner_id'])
                waiting = None
        if not waiting:
            for previous in active:
                if previous['status'] not in {'completed','failed','cancelled'}:
                    await self._cancel_and_wait(previous['runner_id'])
            body = {'source':self.source, 'session':{'kind':'channel','session_id':session_id},
                'channel_user_id':self.channel_user_id, 'channel_chat_id':self.channel_chat_id,
                'profile_id':self.profile_id, 'client_request_id':request_id, 'text':user_input,
                'attachments':refs, 'prompt_augmentations':[extra_system_prompt] if extra_system_prompt else [],
                'request_data':{'channel_config_id':self.config_id}}
            if self.source == 'wecom_kf':
                from src.channels.wecom_kf.context import get_kf_context
                ctx = get_kf_context() or {}
                body['request_data']['channel_user_info'] = ctx.get('channel_user_info') or {}
                from src.core.verbose_feedback import VerboseFeedbackConfig
                if isinstance(kwargs.get('verbose_config'), VerboseFeedbackConfig):
                    body['request_data']['verbose_feedback'] = asdict(kwargs['verbose_config'])
            row = (await self._accept('/v1/runners', body))['runner']
        runner_id = row['runner_id']
        progress_seen = len((waiting.get('snapshot') or {}).get('progressMessages') or []) if waiting else 0
        verbose_seen = {item.get('eventId') for item in (waiting.get('snapshot') or {}).get('verboseMessages') or []} if waiting else set()
        try:
            while True:
                if cancel_check and cancel_check():
                    await self._cancel_and_wait(runner_id)
                    return AgentResponse('')
                snapshot = row.get('snapshot') or {}
                if progress_callback:
                    async def emit(event):
                        result = progress_callback(event)
                        if inspect.isawaitable(result):
                            await result
                    progress = snapshot.get('progressMessages') or []
                    for event in progress[progress_seen:]:
                        await emit(event)
                    progress_seen = len(progress)
                    for item in snapshot.get('verboseMessages') or []:
                        if item.get('eventId') not in verbose_seen:
                            verbose_seen.add(item.get('eventId'))
                            event = {'type':'verbose', **item}
                            if feedback_state is not None and not feedback_state.try_emit(event):
                                continue
                            await emit(event)
                if row['status'] in {'completed','failed','cancelled'}:
                    result = row.get('result') or {}
                    if row['status']=='failed':
                        raise RunnerError(result.get('error_code') or 'RUNNER_EXECUTION_FAILED', 502)
                    if progress_callback and row['status']=='completed':
                        history = await self._request('GET', f'/v1/runners/{runner_id}/channel-result')
                        await emit({'type':'tool_messages', 'messages':history.get('messages') or []})
                    return AgentResponse(result.get('output') or '', result.get('images') or snapshot.get('images') or [])
                if row['status']=='waiting':
                    wait = _clarification_wait(snapshot.get('waiting')) or {}
                    # 新 reply 尚待应用时，不能把原问题再次当作本次回复。
                    if reply_wait_id and wait.get('wait_id') == reply_wait_id:
                        control = (await self._request('GET', f'/v1/runners/{runner_id}/controls/{reply_control_id}'))['control']
                        if control['status'] == 'rejected':
                            raise RunnerError(control.get('error_code') or 'CHANNEL_REPLY_REJECTED', 409)
                    elif wait.get('kind') == 'clarification' and wait.get('question'):
                        return AgentResponse(wait['question'])
                    else:
                        await self._cancel_and_wait(runner_id)
                        raise RunnerError('CHANNEL_EXECUTION_WAIT_UNSUPPORTED', 409)
                if row['status'] in {'paused','interrupted'}:
                    await self._cancel_and_wait(runner_id)
                    raise RunnerError('CHANNEL_EXECUTION_INTERRUPTED', 409)
                await asyncio.sleep(0.25)
                row = (await self._request('GET', f'/v1/runners/{runner_id}'))['runner']
        except asyncio.CancelledError:
            await self._finish_cleanup(self._cancel_and_wait(runner_id))
            raise

    async def _cancel_and_wait(self, runner_id):
        row = (await self._request('POST', f'/v1/runners/{runner_id}/cancel'))['runner']
        for _ in range(120):
            if row['status'] in {'completed','failed','cancelled'}:
                return
            await asyncio.sleep(0.25)
            row = (await self._request('GET', f'/v1/runners/{runner_id}'))['runner']
        raise RunnerError('CHANNEL_CANCELLATION_PENDING', 503)
