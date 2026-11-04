"""企微会话补录/日报的局部业务适配（设计 §5.1 固定顺序）。

由 RuntimeExecution 在受理/恢复入口按**服务器构造的受信业务描述**
（request_data.wecom_conversation_business，普通任务不携带）启用；不新增
execution_mode、Runner 类型或调度队列。接点复用 LocalToolAdapter 既有工具
端口、原模型端口/计费、设备绑定与取消能力（设计 §5.1），并按引擎同款协议
建立工具调用事实（ToolFact/派生 context/逐段 checkpoint，评审 P1-1）。

固定顺序（程序保证，不靠提示词排序）：
1. 以本次请求的会话/日期/时区开启独立日期读取，从头遍历本次范围
   （include_context=true，首录/日报同源）；不使用监控 cursor 或云端库存
   判断已读；只有明确排空（has_more=false）才进入成功路径，缺 cursor、
   页数超界或非日期响应都按真实失败/缺口上报（评审 P1-8）；
2. 每页实际 CLI 结果直接进入分析输入；日期回执由服务器结果受理回调尽力
   补日志（logsink），本流程不等待日志保存、不重复写库；
3. 页面排空后模型只做分组理解/合成报告；补录（backfill）不进入模型步骤；
   模型输入保留原消息 ID、发言人/平台、附件元数据与明确标记的背景正文
   （评审 P2-14），计划特殊关注要求随输入传递（评审 P2-15）；
4. 程序按会话+业务日期 UPSERT 日报（save_daily_report，按首轮 snapshot_at
   新鲜度），失败如实呈现，不冒充已保存。

恢复语义：日期页幂等（唯一键 UPSERT 去重），恢复从本次范围重新遍历；
报告保存幂等。任务状态只查询 Runner；本模块不写 controls、不占
Runner checkpoint 锁以外的业务生命周期。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from src.core.agent_events import make_event
from src.core.agent_engine.contracts import CheckpointFailure, Outcome, ToolCall, ToolFact

READ_TOOL = 'wecom_read_session'
PAGE_LIMIT = 100
DIRECT_SUMMARY_CHAR_LIMIT = 12000
# 合成输入预算（评审二 P2-10：构造性受控）——per-item=预算/4 < 归并组预算=预算/2，
# 保证归并轮数严格收敛；背景提要独立有界（评审二 P2-9）。
SYNTHESIS_INPUT_CHAR_LIMIT = 24000
BACKGROUND_FEED_CHAR_LIMIT = 6000

DEFAULT_TIMEZONE = 'Asia/Shanghai'

# 固定内部总结模板（设计 §5.3）：不套用对客寒暄/服务范围拒答话术。
SUMMARY_SYSTEM_TEMPLATE = '''你是企业微信会话日报分析引擎，负责把一个会话当日聊天记录整理为结构化日报。只依据给出的当日消息与背景，不得编造；内容缺口要如实说明。

输出必须是合法 JSON（UTF-8，不带其它文字），结构：
{
  "overview": "当日概览（3-6句）",
  "categories": [{"topic": "咨询/问题主题", "count": 数量, "detail": "简要说明"}],
  "handled": [{"topic": "已处理事项", "detail": "处理经过与结果", "evidence": [消息ID]}],
  "pending_human": [{"topic": "待人工跟进", "detail": "原因与建议", "evidence": [消息ID]}],
  "noteworthy": [{"topic": "值得关注", "detail": "风险/机会/异常", "evidence": [消息ID]}],
  "notes": "内容缺口或读取限制说明（无则省略）"
}
判定纪律：
- "已处理"必须有明确处理事实（如对方确认、问题解决）；仅收到回复不能认定已解决；无明确结果的列入 pending_human 并标注待确认；
- evidence 使用输入中每条消息标注的 msg_id；没有消息依据的条目不要写；
- 附件未展开时只依据文本与元数据，不得假装已看过图片/文件内容；
- 背景消息（早于当日窗口，输入中明确标注）只作上下文，不计入当日事项。'''

CHUNK_SYSTEM_TEMPLATE = '''你是企业微信会话日报的分组分析引擎。对给出的一组当日消息产出紧凑的中间结论，供最终日报合成使用。只依据给出的消息，不编造。输出 JSON：
{"digest": "本组要点（5-10句）", "items": [{"topic": "...", "kind": "handled|pending|noteworthy|category", "detail": "...", "evidence": [msg_id]}]}'''


from ..contracts import RunnerError as _RunnerError


class WecomBusinessFlowError(_RunnerError):
    """业务分支失败：具体 code 沿 RunnerError 族进入 Worker 终态结果
    （独立测试 P2-1），不再归并为通用 EXECUTION_FAILED。"""

    def __init__(self, code, message):
        super().__init__(code, 500)
        Exception.__init__(self, message)


class WecomSourceError(WecomBusinessFlowError):
    """A fixed account/device mismatch stops the whole accepted execution."""


async def _save_fact(control, state, boundary):
    try:
        await control.save(state, boundary)
    except CheckpointFailure:
        raise
    except Exception as error:
        raise CheckpointFailure('WECOM_CHECKPOINT_FAILED') from error


async def _emit_progress(observer, state, message):
    try:
        await observer.event(state, make_event('progress', data=message))
    except CheckpointFailure:
        raise
    except Exception as error:
        raise CheckpointFailure('WECOM_PROGRESS_CHECKPOINT_FAILED') from error


def _tz(name):
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _tz_name(descriptor):
    return descriptor.get('timezone') or DEFAULT_TIMEZONE


def _previous_business_date(tz_name):
    """定时执行通常总结前一自然日（设计 §5.1）。"""
    return (datetime.now(_tz(tz_name)) - timedelta(days=1)).date().isoformat()


def _descriptor_date(descriptor):
    date = descriptor.get('business_date')
    if isinstance(date, str) and len(date) == 10:
        return date
    return _previous_business_date(_tz_name(descriptor))


class _StopFlow(Exception):
    def __init__(self, command):
        super().__init__(command)
        self.command = command


def _window_of(data):
    window = data.get('read_window') if isinstance(data, dict) else None
    if not isinstance(window, dict):
        return None
    start, end = window.get('start_utc'), window.get('end_utc')
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return None
    return start, end


def _split_day_background(data):
    """按 read_window 区分当日消息与背景（背景不计当天事项）。"""
    window = _window_of(data)
    day, background = [], []
    for message in data.get('messages') or []:
        send_time = message.get('send_time') if isinstance(message, dict) else None
        if not isinstance(send_time, int):
            continue
        if window and window[0] <= send_time < window[1]:
            day.append(message)
        else:
            background.append(message)
    return window, day, background


def _attachment_note(message):
    meta = (message.get('attachment') or {}) if isinstance(message.get('attachment'), dict) else {}
    meta = meta.get('meta') if isinstance(meta.get('meta'), dict) else meta
    name = meta.get('name') or ''
    kind = message.get('kind') or meta.get('kind') or '附件'
    size = meta.get('size_bytes')
    size_text = f'{size}B' if isinstance(size, int) else ''
    return f'[{kind}{":" + str(name) if name else ""}{" " + size_text if size_text else ""}，仅元数据未读内容]'


def _render_message(message, tz_name, *, background=False):
    """统一模型输入投影（评审 P2-14）：原ID、发言人/平台、正文、附件元数据。"""
    if not isinstance(message, dict):
        return ''
    side = message.get('side') or 'peer'
    who = '本人' if side == 'self' else ('系统' if message.get('system') else (message.get('sender_name') or '未知'))
    platform = message.get('sender_platform')
    platform_text = f'/{platform}' if platform in ('wechat', 'wecom') else ''
    time_text = datetime.fromtimestamp(message['send_time'], tz=_tz(tz_name)).strftime('%H:%M:%S') \
        if isinstance(message.get('send_time'), int) else '--:--'
    body = message.get('text') or ''
    if message.get('kind') in ('image', 'file'):
        body = (body + ' ' if body else '') + _attachment_note(message)
    prefix = '[背景] ' if background else ''
    return f"{prefix}[{time_text}] msg_id={message.get('msg_id')} {who}{platform_text}: {body}".rstrip()


def _summary_input(conversation_name, business_date, tz_name, day_messages, background_messages, notes=''):
    """模型输入：当日正文 + 明确标记的背景正文（评审 P2-14），按业务时区显示时间。"""
    lines = [f'会话：{conversation_name}', f'业务日期：{business_date}（时区 {tz_name}）']
    if background_messages:
        lines.append(f'以下为该日零点前的背景消息（{len(background_messages)} 条，仅作上下文，不计入当日事项）：')
        lines.extend(_render_message(message, tz_name, background=True) for message in background_messages)
    else:
        lines.append('背景：无')
    lines.append('以下为当日消息：')
    lines.extend(_render_message(message, tz_name) for message in day_messages)
    if notes:
        lines.append(f'读取说明：{notes}')
    return chr(10).join(lines)


async def _verify_device_before_daily(execution, descriptor):
    """Check the accepted device; the first invocation pins it atomically."""
    control = execution.control
    if not getattr(control, 'durable_owner', False):
        return
    root = control
    while hasattr(root, 'parent_control'):
        root = root.parent_control
    from src.services.agent_runner.wecom_device_binding import bound_device
    from src.local_tools import repository
    from src.local_tools.device_policy import device_ready_error
    identity = {key: getattr(execution.identity, key) for key in
                ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')}
    original = bound_device(root.envelope['execution'], root.attempt.runner_id, identity)
    device_id = str(descriptor.get('device'))
    if original is not None and original != device_id:
        raise WecomSourceError('WECOM_SOURCE_MISMATCH', '当前执行设备与受理来源不符')
    devices = await asyncio.to_thread(repository.list_devices,
                                      execution.identity.tenant_id,
                                      execution.identity.user_id)
    device = next((row for row in devices if str(row.get('id')) == device_id), None)
    if (device is None or device_ready_error(device, READ_TOOL,
            datetime.now(_tz('UTC')), require_selected=False)):
        raise WecomSourceError('DEVICE_UNAVAILABLE', '受理设备离线、已失权或不支持日期读取')


async def _verify_read_source(execution, descriptor, result, fact, args, expected_locator):
    """The result proves account; the durable invocation proves device."""
    identity_codes = {'IDENTITY_UNCONFIRMED', 'WECOM_TASK_DEVICE_CHANGED',
                      'DEVICE_UNAVAILABLE', 'LOCAL_DEVICE_UNSUPPORTED',
                      'LOCAL_DEVICE_OFFLINE', 'SNAPSHOT_UNSTABLE',
                      'CURSOR_UNSUPPORTED', 'NOT_LOGGED_IN', 'WECOM_NOT_FOUND'}
    data = result.get('data') if isinstance(result, dict) else None
    if (isinstance(data, dict) and data.get('self_user_id') is not None
            and data['self_user_id'] != descriptor.get('account')):
        raise WecomSourceError('WECOM_SOURCE_MISMATCH', '日期读取账号与受理来源不符')
    if isinstance(result, dict) and not result.get('success'):
        if result.get('code') in identity_codes:
            raise WecomSourceError(str(result['code']), '本地来源身份未通过核验')
        return
    if (not isinstance(data, dict) or data.get('self_user_id') != descriptor.get('account')
            or data.get('session') != expected_locator):
        raise WecomSourceError('WECOM_SOURCE_MISMATCH', '日期读取账号或会话与受理来源不符')
    control = execution.control
    if not getattr(control, 'durable_owner', False):
        return
    from src.local_tools import repository
    invocation_id = result.get('invocation_id') or fact.invocation_id
    if not invocation_id:
        raise CheckpointFailure('WECOM_INVOCATION_FACT_MISSING')
    try:
        invocation = await asyncio.to_thread(repository.get_invocation,
                                             str(invocation_id), execution.identity.tenant_id)
    except Exception as error:
        raise CheckpointFailure('WECOM_INVOCATION_FACT_UNAVAILABLE') from error
    if (invocation is None or str(invocation.get('device_id')) != str(descriptor.get('device'))
            or invocation.get('user_id') != execution.identity.user_id
            or invocation.get('tool_name') != READ_TOOL
            or invocation.get('session_id') != execution.identity.session_id):
        raise WecomSourceError('WECOM_SOURCE_MISMATCH', '日期读取执行设备与受理来源不符')


async def _read_tool_page(execution, local_adapter, control, state, base_context, args,
                          source=None, expected_locator=None):
    """经既有工具端口执行一次日期读取，建立完整工具调用事实与身份。

    沿用引擎/派发器的调用生命周期（评审 P1-1）：注册 ToolFact → 持久化
    dispatch 意图 → 以派生 context（携带 tool_call_id）执行 LocalToolAdapter
    → invocation/结果回写事实并逐段保存。权威存储（CheckpointFailure）与
    所有权异常原样上抛，不当作会话失败吞掉。
    """
    call_id = 'call_' + uuid.uuid4().hex
    call = ToolCall(call_id, READ_TOOL, dict(args))
    if call_id in state.tools:
        raise CheckpointFailure('TOOL_CALL_ID_ALREADY_USED')
    state.tools[call_id] = ToolFact(call)
    fact = state.tools[call_id]
    fact.phase = 'dispatching'
    await _save_fact(control, state, 'before_tool')
    context = base_context.derive(tool_call_id=call_id, agent_execution_id=state.execution_id)
    cancel_check = lambda: control.command() in ('cancel', 'pause')  # noqa: E731
    result = None
    verification = None
    async for kind, payload in local_adapter._run_local_required_tool(
            READ_TOOL, call.arguments, state.identity.tenant_id, state.identity.user_id,
            cancel_check=cancel_check, context=context):
        if kind == 'invocation':
            fact.invocation_id = payload
            try:
                await _save_fact(control, state, 'tool_invocation')
            except Exception as error:
                raise CheckpointFailure('INVOCATION_CHECKPOINT_FAILED') from error
        elif kind == 'result':
            result = payload
        elif kind == 'verification':
            verification = payload
    if result is None:
        if verification is not None:
            # 本地工具要求恢复续跑（durable continuation）：业务分支无引擎等待
            # 协议，按真实缺口失败，交由原 Runner 恢复语义处理。
            raise WecomBusinessFlowError(
                str(verification.get('error_code') or 'LOCAL_TOOL_CONTINUATION_REQUIRED'),
                '日期读取需要设备侧恢复续跑；本次按缺口失败，恢复后重新执行')
        raise CheckpointFailure('LOCAL_TOOL_MISSING_RESULT')
    if isinstance(result, dict):
        result.pop('_no_truncate', None)
    fact.phase = 'completed'
    fact.success = bool(result.get('success')) if isinstance(result, dict) else True
    fact.result = result
    # 配对消息（审计/历史）：assistant tool_calls + tool 回执
    state.messages.append({'role': 'assistant', 'content': '',
                           'tool_calls': [call.message_call()]})
    state.messages.append({'role': 'tool', 'tool_call_id': call_id,
                           'content': json.dumps(result, ensure_ascii=False, default=str)[:2000]})
    fact.result_recorded = True
    await _save_fact(control, state, 'tool_completed')
    if source and source.get('schema_version') == 2:
        await _verify_read_source(execution, source, result, fact, args, expected_locator)
    return result


async def _iterate_day_pages(execution, local_adapter, control, state, base_context,
                             observer, locator, business_date, tz_name, source=None):
    """顺序遍历一个会话的业务日分页；只有明确排空才正常结束（评审 P1-8）。"""
    args = {'session': locator, 'date': business_date, 'timezone': tz_name,
            'include_context': True, 'limit': PAGE_LIMIT}
    pages = 0
    while True:
        if control.command() in ('cancel', 'pause'):
            raise _StopFlow(control.command())
        result = await _read_tool_page(execution, local_adapter, control, state,
                                       base_context, args, source, locator)
        command = control.command()
        if command in ('cancel', 'pause'):
            raise _StopFlow(command)
        if not isinstance(result, dict) or not result.get('success'):
            code = result.get('code') if isinstance(result, dict) else None
            raise WecomBusinessFlowError(
                str(code) if isinstance(code, str) and code else 'WECOM_DAY_READ_FAILED',
                f'会话日期读取失败：{result.get("message") if isinstance(result, dict) else "无结果"}')
        data = result.get('data') or {}
        if not isinstance(data, dict) or _window_of(data) is None:
            # 非日期响应（能力未生效/旧插件）：真实失败，不当作成功日期读取。
            raise WecomBusinessFlowError(
                'WECOM_DAY_MODE_UNAVAILABLE',
                '读取结果缺少日期窗口（read_window）；请确认 WeCom 插件支持 date 参数后重试')
        pages += 1
        yield data
        if data.get('has_more') is not True:
            return
        cursor = data.get('cursor')
        if not isinstance(cursor, str) or not cursor:
            raise WecomBusinessFlowError(
                'WECOM_DAY_READ_INCOMPLETE',
                f'日期分页未排空但缺少续页游标（第 {pages} 页）；本次按缺口失败，不生成完整日报')
        args = {'session': locator, 'cursor': cursor, 'limit': PAGE_LIMIT}


async def _model_call(execution, system_prompt, user_text):
    """复用所选智能体模型端口的单次总结调用；用量沿原 observer 记账。"""
    state, control, observer = execution.state, execution.control, execution.observer
    state.iteration += 1
    state.resources['business_iterations'] = state.resources.get('business_iterations', 0) + 1
    call_id = f'model_{uuid.uuid4().hex}'
    fact = {'call_id': call_id, 'execution_id': state.execution_id,
            'iteration': state.iteration, 'phase': 'started'}
    state.model_calls.append(fact)
    await _save_fact(control, state, 'before_model')
    from ..usage_context import execution_call
    with execution_call(state.execution_id, call_id):
        response = await execution.resources.llm.chat_with_tools(
            system_prompt=system_prompt,
            messages=[{'role': 'user', 'content': user_text}], tools=[])
    response = dict(response)
    response.setdefault('model', execution.resources.llm.get_model_name())
    response.setdefault('provider', execution.resources.llm.get_provider_name())
    fact.update(phase='completed', usage=response.get('usage') or {},
                model=response.get('model', ''), provider=response.get('provider', ''),
                response=response, applied=True)
    await _save_fact(control, state, 'model_usage')
    try:
        await observer.model_usage(state, dict(fact))
    except CheckpointFailure:
        raise
    except Exception as error:
        raise CheckpointFailure('WECOM_USAGE_FACT_FAILED') from error
    if response.get('finish_reason') != 'stop':
        raise WecomBusinessFlowError('WECOM_SUMMARY_TRUNCATED',
                                     '日报模型未正常完成，未保存不完整输出')
    return response.get('content') or ''


def _parse_summary_json(text, *, chunk=False):
    """Accept only complete structured JSON; partial text is not a saved report."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        value = json.loads(text.strip())
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    if chunk:
        if (not isinstance(value.get('digest'), str) or not value['digest'].strip()
                or not isinstance(value.get('items'), list)):
            return None
        return value
    if not isinstance(value.get('overview'), str) or not value['overview'].strip():
        return None
    if any(not isinstance(value.get(key), list)
           for key in ('categories', 'handled', 'pending_human', 'noteworthy')):
        return None
    if 'notes' in value and not isinstance(value['notes'], str):
        return None
    for key in ('categories', 'handled', 'pending_human', 'noteworthy'):
        for item in value[key]:
            if not isinstance(item, dict) or not isinstance(item.get('topic'), str) \
                    or not isinstance(item.get('detail'), str):
                return None
            if key == 'categories':
                if type(item.get('count')) is not int or item['count'] < 0:
                    return None
            elif not isinstance(item.get('evidence'), list):
                return None
    return value


def _requirements_text(descriptor):
    requirements = descriptor.get('requirements')
    return requirements.strip() if isinstance(requirements, str) else ''


def _bounded_background_feed(background_messages, tz_name, budget=BACKGROUND_FEED_CHAR_LIMIT):
    """背景上下文提要（评审二 P2-9）：渲染背景正文并有界截取；截断如实标注。"""
    if not background_messages:
        return '无'
    feed = '\n'.join(_render_message(message, tz_name, background=True)
                     for message in background_messages)
    if len(feed) > budget:
        return feed[:budget] + '\n（背景正文超长，已按有界预算截取；完整背景见会话记录）'
    return feed


async def _analyze_conversation(execution, descriptor, conversation, business_date,
                                tz_name, pages, notes=''):
    """一次会话的日报分析：短内容直接总结；超过阈值按页分组后汇总。

    长短路径共用同一背景来源（评审二 P2-9）：背景正文以有界提要进入最终
    合成输入，分组只分析当日消息、不逐组重复背景；合成输入按构造性预算
    受控（评审二 P2-10），无法受控时如实报内容缺口，不用切片伪装完整。
    """
    name = conversation.get('name') or conversation.get('session_locator')
    day_messages, background_messages = [], []
    for data in pages:
        _window, day, background = _split_day_background(data)
        day_messages.extend(day)
        background_messages.extend(background)
    requirements = _requirements_text(descriptor)
    direct_input = _summary_input(name, business_date, tz_name, day_messages,
                                  background_messages, notes)
    if requirements:
        direct_input += f'\n计划特殊关注要求：{requirements}'
    if not day_messages:
        # 成功空日（设计 §5.2）：整段日期范围排空且无当日记录。
        return {'summary': {'overview': '当日无消息', 'categories': [],
                            'handled': [], 'pending_human': [], 'noteworthy': [],
                            'notes': '日期范围已排空，无当日消息'},
                'day_count': 0, 'background_count': len(background_messages)}
    if len(direct_input) <= DIRECT_SUMMARY_CHAR_LIMIT:
        content = await _model_call(execution, SUMMARY_SYSTEM_TEMPLATE, direct_input)
        summary = _parse_summary_json(content)
        if summary is not None:
            return {'summary': summary, 'day_count': len(day_messages),
                    'background_count': len(background_messages)}
        raise WecomBusinessFlowError('WECOM_SUMMARY_INVALID', '日报模型输出为空')
    # 分组分析（设计 §4.3）：按页分组保留证据与中间结论，再合成报告。
    digests = []
    for index, data in enumerate(pages):
        _window, group, _background = _split_day_background(data)
        if not group:
            continue
        chunk = _summary_input(name, business_date, tz_name, group, [],
                               notes + f'（分组 {index + 1}/{len(pages)}，仅分析本组）' if notes
                               else f'分组 {index + 1}/{len(pages)}，仅分析本组')
        if requirements:
            chunk += f'\n计划特殊关注要求：{requirements}'
        content = await _model_call(execution, CHUNK_SYSTEM_TEMPLATE, chunk)
        parsed = _parse_summary_json(content, chunk=True)
        if not isinstance(parsed, dict):
            raise WecomBusinessFlowError('WECOM_SUMMARY_INVALID', f'分组 {index + 1} 模型输出无效')
        digests.append(json.dumps(parsed, ensure_ascii=False))
    # 有界背景提要（长短路径一致的理解输入；评审二 P2-9）。
    background_feed = _bounded_background_feed(background_messages, tz_name)
    synthesis_header = (f'会话：{name}\n业务日期：{business_date}（时区 {tz_name}）\n'
                        f'当日消息总数：{len(day_messages)}\n'
                        f'背景（早于当日窗口，仅上下文，有界提要）：\n{background_feed}\n'
                        + (f'计划特殊关注要求：{requirements}\n' if requirements else '')
                        + '\n以下是按时间分组的中间结论（含证据 msg_id）：\n')
    # 构造性合成预算（评审三 P1-1）：所有中间结论（初始压缩、归并输出）统一
    # 受 per-item 预算约束——每条 ≤ per_item 且归并组预算 = 2×per_item，故每组
    # 至少合 2 条，组数每轮严格减半；归并输出若超 per-item 同样先压缩、仍超
    # 即如实报内容缺口（保留旧日报），绝不无限调用模型。
    notes_parts = [notes] if notes else []
    per_item_cap = SYNTHESIS_INPUT_CHAR_LIMIT // 4
    group_budget = per_item_cap * 2
    for index, digest in enumerate(digests):
        if len(digest) <= per_item_cap:
            continue
        content = await _model_call(execution, CHUNK_SYSTEM_TEMPLATE,
                                    f'以下中间结论超出单组预算（{per_item_cap} 字符），'
                                    '请压缩为同结构 JSON（保留证据 msg_id 与关键事实，丢弃冗余引述），'
                                    '总长不超过预算：\n' + digest)
        parsed = _parse_summary_json(content, chunk=True)
        if not isinstance(parsed, dict):
            raise WecomBusinessFlowError('WECOM_SUMMARY_INVALID', '中间结论压缩输出无效')
        compressed = json.dumps(parsed, ensure_ascii=False)
        if len(compressed) > per_item_cap:
            raise WecomBusinessFlowError(
                'WECOM_SUMMARY_INPUT_OVERSIZE',
                '中间结论压缩后仍超出合成输入预算；本次按内容缺口失败，已保留旧日报')
        digests[index] = compressed
    while len(digests) > 1 and len(synthesis_header) + sum(len(item) for item in digests) \
            > SYNTHESIS_INPUT_CHAR_LIMIT:
        merged = []
        current, used = [], 0
        for digest in digests:
            if current and used + len(digest) > group_budget:
                merged.append(current)
                current, used = [], 0
            current.append(digest)
            used += len(digest)
        if current:
            merged.append(current)
        if len(merged) >= len(digests):
            # 归并未产生有效进展（评审三 P1-1）：按内容缺口结束，保留旧日报，
            # 不无限消耗模型积分。
            raise WecomBusinessFlowError(
                'WECOM_SUMMARY_INPUT_OVERSIZE',
                '中间结论归并未有效缩小；本次按内容缺口失败，已保留旧日报')
        round_inputs = ['\n---\n'.join(group) for group in merged]
        digests = []
        for merge_input in round_inputs:
            content = await _model_call(execution, CHUNK_SYSTEM_TEMPLATE,
                                        '以下同会话中间结论需要合并压缩为一份同结构 JSON'
                                        f'（保留证据 msg_id 与关键事实），总长不超过 {per_item_cap} 字符：\n'
                                        + merge_input)
            parsed = _parse_summary_json(content, chunk=True)
            if not isinstance(parsed, dict):
                raise WecomBusinessFlowError('WECOM_SUMMARY_INVALID', '中间结论归并输出无效')
            merged_digest = json.dumps(parsed, ensure_ascii=False)
            if len(merged_digest) > per_item_cap:
                # 归并输出与初始压缩同约束：超 per-item 先再压缩一次，仍超即缺口。
                content = await _model_call(execution, CHUNK_SYSTEM_TEMPLATE,
                                            f'以下合并结论仍超出单组预算（{per_item_cap} 字符），'
                                            '请进一步压缩为同结构 JSON（保留证据 msg_id），'
                                            '总长不超过预算：\n' + merged_digest)
                parsed = _parse_summary_json(content, chunk=True)
                if not isinstance(parsed, dict):
                    raise WecomBusinessFlowError('WECOM_SUMMARY_INVALID', '中间结论压缩输出无效')
                merged_digest = json.dumps(parsed, ensure_ascii=False)
            if len(merged_digest) > per_item_cap:
                raise WecomBusinessFlowError(
                    'WECOM_SUMMARY_INPUT_OVERSIZE',
                    '中间结论归并后仍超出单组预算；本次按内容缺口失败，已保留旧日报')
            digests.append(merged_digest)
    synthesis = synthesis_header + '\n---\n'.join(digests) \
        + '\n\n请合成该会话本日的最终结构化日报（沿用输出 JSON 结构）。'
    content = await _model_call(execution, SUMMARY_SYSTEM_TEMPLATE, synthesis)
    summary = _parse_summary_json(content)
    if summary is None:
        raise WecomBusinessFlowError('WECOM_SUMMARY_INVALID', '日报模型输出为空')
    for note in notes_parts:
        if note and note not in (summary.get('notes') or ''):
            summary['notes'] = (summary.get('notes') + '；' if summary.get('notes') else '') + note
    return {'summary': summary, 'day_count': len(day_messages),
            'background_count': len(background_messages)}


def _resolve_conversations(execution, descriptor):
    """会话清单：手动指定 conversation_ids 或计划清单（设计 §5.1）。"""
    from src.wecom_conversation import service
    identity = execution.identity
    conversation_ids = descriptor.get('conversation_ids')
    conversations = []
    if descriptor.get('schema_version') == 2 and (not isinstance(conversation_ids, list)
                                                  or not conversation_ids):
        raise WecomBusinessFlowError('WECOM_BUSINESS_TARGET_EMPTY', '本次执行缺少冻结会话清单')
    if isinstance(conversation_ids, list) and conversation_ids:
        for item in conversation_ids:
            row = service.get_conversation(identity.tenant_id, identity.user_id, int(item))
            if row is None:
                raise WecomBusinessFlowError('CONVERSATION_NOT_FOUND', f'会话不存在：{item}')
            if descriptor.get('schema_version') == 2 and (
                    row.get('source_account_id') != descriptor.get('account')
                    or str(row.get('device_id')) != str(descriptor.get('device'))):
                raise WecomBusinessFlowError('WECOM_SOURCE_MISMATCH', '会话来源与冻结计划不符')
            conversations.append(row)
        return conversations
    task_id = descriptor.get('task_id')
    if isinstance(task_id, str) and task_id:
        rows = service.plan_conversations(identity.tenant_id, identity.user_id, task_id)
        if rows:
            return rows
    raise WecomBusinessFlowError('WECOM_BUSINESS_TARGET_EMPTY', '日报计划当前没有管理任何会话')


async def _save_report(execution, conversation, business_date, analysis, snapshot_at, runner_id):
    from src.wecom_conversation import service
    identity = execution.identity
    generated_at = datetime.now(_tz('UTC'))
    return await asyncio.to_thread(
        service.save_daily_report, identity.tenant_id, identity.user_id, conversation['id'],
        business_date,
        {'summary': analysis['summary'],
         'coverage': {'day_count': analysis.get('day_count'),
                      'background_count': analysis.get('background_count'),
                      'snapshot_at': snapshot_at.isoformat() if snapshot_at else None,
                      'source_runner_id': runner_id}},
        snapshot_at=snapshot_at, generated_at=generated_at, source_runner_id=runner_id)


async def _run_backfill(execution, descriptor, local_adapter, base_context):
    """首录补录（设计 §3.1）：只读当天＋零点前100条背景，不调用总结模型。"""
    state, control, observer = execution.state, execution.control, execution.observer
    locator = descriptor.get('session')
    if not isinstance(locator, str) or not locator:
        raise WecomBusinessFlowError('WECOM_BUSINESS_TARGET_EMPTY', '补录缺少会话标识')
    business_date = descriptor.get('date') or _descriptor_date(descriptor)
    tz_name = _tz_name(descriptor)
    pages = 0
    async for data in _iterate_day_pages(execution, local_adapter, control, state,
                                         base_context, observer, locator,
                                         business_date, tz_name):
        pages += 1
    await observer.event(state, make_event('progress', data=f'补录完成：{business_date} 共读取 {pages} 页'))


async def _run_daily(execution, descriptor, local_adapter, base_context):
    """多会话日报（设计 §5.1）：逐会话读取→分析→保存；一个会话失败继续其他。"""
    state, control, observer = execution.state, execution.control, execution.observer
    identity = execution.identity
    tz_name = _tz_name(descriptor)
    business_date = _descriptor_date(descriptor)
    conversations = await asyncio.to_thread(_resolve_conversations, execution, descriptor)
    if descriptor.get('schema_version') == 2:
        await _verify_device_before_daily(execution, descriptor)
    runner_id = None
    root = control
    while hasattr(root, 'parent_control'):
        root = root.parent_control
    attempt = getattr(root, 'attempt', None)
    if attempt is not None:
        runner_id = getattr(attempt, 'runner_id', None)
    progress = {'schema_version': 1, 'business_date': business_date,
                'scope_conversation_ids': [row['id'] for row in conversations],
                'items': [{'conversation_id': row['id'], 'status': 'not_started',
                           'phase': 'queued', 'started_at': None, 'finished_at': None,
                           'error_code': None} for row in conversations]}
    state.resources['wecom_daily_progress'] = progress
    await _save_fact(control, state, 'daily_scope')
    completed, failed, superseded = [], [], []
    for index, conversation in enumerate(conversations):
        if control.command() in ('cancel', 'pause'):
            raise _StopFlow(control.command())
        name = conversation.get('name') or conversation.get('session_locator')
        item = progress['items'][index]
        item.update(status='running', phase='reading',
                    started_at=datetime.now(_tz('UTC')).isoformat())
        await _save_fact(control, state, 'daily_conversation_started')
        try:
            await _emit_progress(observer, state, f'开始生成会话「{name}」{business_date} 的日报')
            pages, notes = [], ''
            snapshot_at = None
            async for data in _iterate_day_pages(execution, local_adapter, control,
                                                 state, base_context, observer,
                                                 conversation['session_locator'],
                                                 business_date, tz_name, descriptor):
                pages.append(data)
                window = data.get('read_window') or {}
                if snapshot_at is None and isinstance(window.get('snapshot_at'), int):
                    snapshot_at = datetime.fromtimestamp(window['snapshot_at'], tz=_tz('UTC'))
                if data.get('naming_incomplete') is True:
                    notes = '部分发送者姓名未能解析'
                if data.get('invalid_time_count'):
                    notes = (notes + '; ' if notes else '') + f"{data['invalid_time_count']} 条记录无有效时间未纳入"
            if snapshot_at is None:
                # 排空但缺首轮快照：按缺口记录，不用当前时间补造（评审 P1-8）。
                raise WecomBusinessFlowError(
                    'WECOM_DAY_SNAPSHOT_MISSING', '日期读取结果缺少首轮快照时点（snapshot_at）')
            item['phase'] = 'analyzing'
            await _save_fact(control, state, 'daily_conversation_analyzing')
            analysis = await _analyze_conversation(execution, descriptor, conversation,
                                                   business_date, tz_name, pages, notes)
            item['phase'] = 'saving'
            await _save_fact(control, state, 'daily_conversation_saving')
            saved = await _save_report(execution, conversation, business_date, analysis,
                                       snapshot_at, runner_id)
            if not saved['applied']:
                superseded.append({'conversation_id': conversation['id'], 'name': name})
                item.update(status='superseded', phase='superseded',
                            finished_at=datetime.now(_tz('UTC')).isoformat())
                await _save_fact(control, state, 'daily_conversation_superseded')
                await _emit_progress(observer, state,
                                     f'会话「{name}」{business_date} 已有更新的日报，本次未覆盖')
                continue
            completed.append((conversation, name))
            item.update(status='completed', phase='completed',
                        finished_at=datetime.now(_tz('UTC')).isoformat())
            await _save_fact(control, state, 'daily_conversation_completed')
            await _emit_progress(observer, state, f'会话「{name}」{business_date} 日报已保存')
        except _StopFlow:
            raise
        except (CheckpointFailure, asyncio.CancelledError):
            raise  # 权威存储/取消异常不当作单会话失败吞掉（评审 P1-1）
        except WecomSourceError:
            raise
        except WecomBusinessFlowError as error:
            # 单会话业务缺口：记录并继续其他会话（设计 §5.1）；失败不冒充空日。
            failed.append({'conversation_id': conversation['id'], 'name': name,
                           'code': error.code})
            item.update(status='failed', finished_at=datetime.now(_tz('UTC')).isoformat(),
                        error_code=error.code)
            await _save_fact(control, state, 'daily_conversation_failed')
            logger.warning(f'后端日志：企微日报单会话失败 conversation={conversation.get("id")} '
                           f'runner={runner_id} date={business_date} phase={item["phase"]} code={error.code}')
            await _emit_progress(observer, state,
                                 f'会话「{name}」日报生成失败（{error.code}），继续其他会话')
        except Exception as error:
            if getattr(error, 'authoritative_storage_failure', False):
                raise
            code = type(error).__name__
            failed.append({'conversation_id': conversation['id'], 'name': name,
                           'code': code})
            item.update(status='failed', finished_at=datetime.now(_tz('UTC')).isoformat(),
                        error_code=code)
            await _save_fact(control, state, 'daily_conversation_failed')
            logger.opt(exception=True).warning(
                f'后端日志：企微日报单会话失败 conversation={conversation.get("id")} '
                f'runner={runner_id} date={business_date} phase={item["phase"]} code={code}')
            await _emit_progress(observer, state,
                                 f'会话「{name}」日报生成失败（{code}），继续其他会话')
    # 本次执行事实（评审三 P1-4）：稳定 conversation_id + 本次目标范围 + 逐会话
    # 结果；成功、部分失败、全部失败都先落事实再终态，通知/页面/投影共用。
    execution.state.resources['wecom_daily_result'] = {
        'business_date': business_date,
        'task_id': descriptor.get('task_id') if isinstance(descriptor.get('task_id'), str) else None,
        'scope_conversation_ids': [conversation['id'] for conversation in conversations],
        'completed': [{'conversation_id': conversation['id'], 'name': name}
                      for conversation, name in completed],
        'failed': failed,
        'superseded': superseded,
        'runner_id': runner_id,
    }
    await _save_fact(control, state, 'daily_result')
    if conversations and not completed and not superseded:
        # 全部失败也是执行事实（已先落 checkpoint）；终态如实失败，旧日报保留。
        raise WecomBusinessFlowError('WECOM_DAILY_ALL_FAILED',
                                     f'全部会话日报生成失败：{json.dumps(failed, ensure_ascii=False)[:500]}')
    # 部分失败：整体按设计继续完成（设计 §5.1），事实中的 failed 明细供通知/
    # 页面区分"本次未完成、展示旧报告"。


async def run_wecom_business(execution, descriptor, local_adapter, request_context=None):
    """业务分支入口：完成/取消/失败沿原 Runner 协议收尾（engine 同款语义）。"""
    state, control, observer = execution.state, execution.control, execution.observer
    state.outcome = Outcome.RUNNING
    state.waiting = None
    kind = descriptor.get('kind')
    base_context = await execution._tool_context(request_context)
    try:
        if kind == 'backfill':
            await _run_backfill(execution, descriptor, local_adapter, base_context)
        elif kind == 'daily':
            await _run_daily(execution, descriptor, local_adapter, base_context)
        else:
            raise WecomBusinessFlowError('WECOM_BUSINESS_DESCRIPTOR_INVALID',
                                         f'未知业务描述 kind={kind}')
    except _StopFlow as stop:
        if stop.command == 'cancel':
            state.outcome = Outcome.CANCELLED
            await control.save(state, 'cancel')
            yield make_event('cancelled')
            return
        state.outcome = Outcome.PAUSED
        await control.save(state, 'pause')
        yield make_event('paused')
        return
    state.outcome = Outcome.COMPLETED
    state.waiting = None
    await control.save(state, 'completed')
    yield make_event('execution_completed')
