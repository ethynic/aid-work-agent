"""渠道只观察独立 Runner 的执行；合并、历史与投递仍由原渠道负责。"""

import asyncio
import inspect
import uuid
from dataclasses import asdict
from urllib.parse import quote

from src.channels import ask_delivery, ask_render
from src.core.agent import AgentResponse
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.web_client import RunnerServiceClient


class ChannelAskResponse(AgentResponse):
    """携带结构化提问分段载荷的渠道回复（WP5，设计 §7.2）。

    AgentResponse 的渠道层扩展：附加 ``ask_segments`` 字段传递分段结构，
    经既有会话发送封装（session.make_send_response）逐段发送——此处只声明
    分段，不预判送达；发送事实由实际发送确认处记录。str 语义与序列化行为
    与 AgentResponse 完全一致。
    """

    def __new__(cls, text, images=(), ask_segments=None):
        result = super().__new__(cls, text, images)
        result.ask_segments = ask_segments
        return result


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
        reply_form = None
        if waiting:
            wait = _clarification_wait((waiting.get('snapshot') or {}).get('waiting')) or {}
            # 回复已停车的追问；普通新消息不留着旧会话 claim 无期限排队。
            if wait.get('kind') == 'clarification' and wait.get('wait_id') and wait.get('target_execution_id'):
                form = wait.get('form')
                reply_form = form if isinstance(form, dict) and form.get('questions') else None
                body = None
                if reply_form:
                    # 结构化提问入站单管线（设计 §7.2，与投递状态无关）：
                    # 精确指令 / Q 编号解析 / 自然语言三分法。隐藏命令已在
                    # channel_routes 更早拦截，此处不重复。
                    outcome = await self._handle_ask_inbound(waiting, wait, reply_form, user_input)
                    if outcome is not None:
                        kind, value = outcome
                        if kind == 'response':
                            # 纠正提示 / 重发分段：不提交、wait 不消费（一次不计数）。
                            return value
                        body = value
                if body is None:
                    # 自然语言（含旧 clarify 无 form 的等待）：沿现有 reply 路径
                    # 原样提交为 answer，永不吞。
                    body = {'action':'reply', 'client_request_id':request_id, 'answer':user_input,
                        'attachments':refs, 'wait_id':wait['wait_id'],
                        'target_execution_id':wait['target_execution_id']}
                try:
                    accepted = await self._accept(f"/v1/runners/{waiting['runner_id']}/controls",
                        body, waiting['runner_id'])
                except RunnerError as error:
                    if error.errors:
                        # 受理事务内表单校验失败（同步 422 + errors）：wait 不消费、
                        # control 不创建，向用户原样转述逐题明细，修正后可直接重提。
                        return AgentResponse(ask_render.submission_error_text(error.errors))
                    raise
                row = accepted['runner']
                reply_wait_id, reply_control_id = wait['wait_id'], accepted['control']['control_id']
                if reply_form:
                    # 登记在途受理（仅事实，不承诺应用）：重发边界依赖该记录。
                    ask_delivery.record_submission(self.session_id, reply_wait_id, reply_control_id)
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
            if self.source in ('wecom_kf', 'feishu', 'dingtalk', 'wecom'):
                # 渠道侧冻结 verbose 配置；channel_user_info 仍为 KF 工具上下文专属。
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
                if reply_wait_id and row['status'] != 'waiting':
                    # 答案应用成功（wait 消失）：清理该 form 剩余发送记录，
                    # 此后「重发」不再针对旧表单（设计 v6 受理/应用边界）。
                    ask_delivery.clear_facts(self.session_id, reply_wait_id)
                    reply_wait_id = None
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
                            # 控制被拒且 wait 仍有效：清除在途标记，允许修正/重发。
                            ask_delivery.clear_submission(self.session_id, reply_wait_id)
                            raise RunnerError(control.get('error_code') or 'CHANNEL_REPLY_REJECTED', 409)
                    elif wait.get('kind') == 'clarification' and (wait.get('question') or wait.get('form')):
                        return self._ask_wait_response(wait)
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

    # ==================== 结构化提问（WP5，设计 §7.2） ====================

    async def _handle_ask_inbound(self, waiting, wait, form, user_input):
        """结构化提问入站单管线（三分法 + 精确指令，与投递状态无关）。

        返回：
        - ``None``：自然语言 → 调用方沿既有 reply 路径原样提交（永不吞）；
        - ``('response', AgentResponse)``：直接回复（纠正提示 / 重发分段），
          不提交、wait 不消费；
        - ``('submit', body)``：结构化提交（skip=true / answers），answer 置空，
          canonical 由服务端生成。
        """
        text = (user_input or '').strip()
        wait_id, target = wait['wait_id'], wait['target_execution_id']
        if ask_render.is_skip_reply(text):
            # 跳过唯一入口 = 显式 skip；allow_skip=false 由服务端权威拒绝（422）。
            return ('submit', {'action':'reply', 'client_request_id':uuid.uuid4().hex,
                'answer':'', 'skip':True, 'wait_id':wait_id, 'target_execution_id':target})
        if ask_render.is_resend_reply(text):
            return ('response', await self._build_resend_response(waiting, wait, form))
        parsed = ask_render.parse_reply(text, form)
        if parsed is None:
            return None
        if not parsed.get('ok'):
            # 协议错误：纠正一次、不计数，不提交也不消费 wait。
            return ('response', AgentResponse(parsed['correction']))
        return ('submit', {'action':'reply', 'client_request_id':uuid.uuid4().hex,
            'answer':'', 'answers':parsed['answers'], 'wait_id':wait_id,
            'target_execution_id':target})

    async def _build_resend_response(self, waiting, wait, form):
        """「重发/简化」：按发送事实续传缺段（只发缺的，不重复已发），该条不作答案。

        受理/应用边界（设计 v6）：control 已受理未应用期间不执行重发（答案可能
        即将应用）；被拒且 wait 仍有效可再重发；应用成功后剩余记录已由轮询清理。
        全程读既有 control 状态与发送事实，不新增状态机。
        """
        session_id, wait_id = self.session_id, wait['wait_id']
        facts = ask_delivery.load_facts(session_id, wait_id)
        submitted = (facts or {}).get('submitted') or {}
        if submitted.get('control_id'):
            try:
                control = (await self._request('GET', f"/v1/runners/{waiting['runner_id']}"
                    f"/controls/{submitted['control_id']}"))['control']
            except RunnerError:
                control = None
            state = (control or {}).get('status')
            if state in ('accepted', 'claimed'):
                return AgentResponse('已收到你的回答，正在等待应用生效，暂不重发。')
            if state == 'consumed':
                ask_delivery.clear_facts(session_id, wait_id)
                return AgentResponse('该提问已完成作答并生效，无需重发。')
            # rejected / 状态不可读且 wait 仍有效：清除在途标记，允许重发。
            ask_delivery.clear_submission(session_id, wait_id)
        if not facts or not isinstance(facts.get('segments'), list) or not facts['segments']:
            return AgentResponse('当前提问没有可重发的未发送内容。')
        missing = ask_delivery.missing_segments(facts)
        if not missing:
            return AgentResponse('提问内容此前均已发送；如仍需查看，请直接回复文字说明具体问题。')
        ask_delivery.record_resend(session_id, wait_id)
        return ChannelAskResponse('\n\n'.join(item['text'] for item in missing), ask_segments={
            'session_id': session_id, 'wait_id': wait_id, 'resend': True,
            'segments': missing, 'limit_bytes': ask_render.source_segment_limit(self.source)})

    def _ask_wait_response(self, wait):
        """提问等待的出站响应：form → 分段结构（不预判送达）；无 form 退回 question。

        旧 runner 兼容：wait 无 form（旧 clarify）时行为与历史逐字节一致。
        """
        question = wait.get('question') or ''
        form = wait.get('form')
        wait_id = wait.get('wait_id') or ''
        if not (isinstance(form, dict) and form.get('questions') and wait_id):
            return AgentResponse(question)
        limit = ask_render.source_segment_limit(self.source)
        segments = ask_render.render_segments(form, limit) or [question or '（收到提问）']
        return ChannelAskResponse('\n\n'.join(segments), ask_segments={
            'session_id': self.session_id, 'wait_id': wait_id, 'resend': False,
            'form': form, 'limit_bytes': limit,
            'segments': [{'index': index, 'text': text} for index, text in enumerate(segments)]})

    async def _cancel_and_wait(self, runner_id):
        row = (await self._request('POST', f'/v1/runners/{runner_id}/cancel'))['runner']
        for _ in range(120):
            if row['status'] in {'completed','failed','cancelled'}:
                return
            await asyncio.sleep(0.25)
            row = (await self._request('GET', f'/v1/runners/{runner_id}'))['runner']
        raise RunnerError('CHANNEL_CANCELLATION_PENDING', 503)
