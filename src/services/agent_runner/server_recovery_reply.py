"""Replies to an interrupted server tool, bound to its original owned wait."""

import hashlib
import json
import re
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

from pydantic import ValidationError

from src.core.agent_engine.contracts import CheckpointFailure, DispatchResult, Outcome
from src.core.storage import resolve_scoped_uploaded_file
from src.tools.base import ExecutionTarget
from .contracts import RunnerError, canonical_json


FILE_WAIT = 'replacement_file'
EXTERNAL_WAIT = 'external_effect'
_LABELS = {
    'confirmed_done': '已完成',
    'retry': '未完成，按原操作重试',
    'skipped': '无法确认，跳过此步骤',
}
_READ_TOOLS = {
    'read', 'grep', 'image_read', 'get_channel_user_info', 'knowledge_base_search',
    'knowledge_file_search', 'attraction_search', 'hotel_search', 'web_search',
    'web_fetch', 'wechat_mp_sync_status',
}


def interactive_recovery(state):
    return (state.identity.source == 'chat' and state.identity.session_kind == 'web'
            and state.identity.user_id is not None and state.outcome in {
                Outcome.RUNNING, Outcome.WAITING, Outcome.PAUSED})


def external_recovery(state, fact, tool):
    call = fact.call
    if call.name in _READ_TOOLS:
        return False
    if call.name == 'email_process' and call.arguments.get('action') == 'read':
        return False
    if call.name == 'session_task_manage' and call.arguments.get('action') in ('list','get'):
        return False
    return (interactive_recovery(state) and tool is not None
            and tool.execution_target == ExecutionTarget.SERVER and not fact.invocation_id
            and fact.phase == 'dispatching' and fact.result is None and not fact.result_recorded)


def server_skill_recovery(state, fact, skill):
    from .server_tool_recovery import QUOTATION_DIRECTORY
    # Only code-shipped server skills have a stable route across old checkpoints.
    metadata = getattr(skill, 'metadata', None)
    return (fact.call.name == 'skill_execute' and skill is not None
            and Path(skill.dir).resolve().is_relative_to(QUOTATION_DIRECTORY.parent)
            and (not isinstance(metadata,dict) or metadata.get('execution') != 'device')
            and not any(item.get('tool_call_id') == fact.call.id for item in
                        state.resources.get('local_invocations', {}).values()))


def registered_server_tool(name):
    from src.tools.registry import discover_tool_classes
    return discover_tool_classes().get(name)


def recovery_wait(wait):
    return (wait or {}).get('recovery_reply') or {}


def _wait(state, call, attempt, purpose, question, *, form=None, choices=()):
    from .wait_identity import owned_wait_id
    wait = {'kind': 'clarification', 'tool_call_id': call.id, 'question': question,
            'target_execution_id': state.execution_id,
            'recovery_reply': {'purpose': purpose, 'choices': list(choices)}}
    # A reply is single-use. A new interruption of this same call after an
    # accepted reply must get a fresh wait under the same Runner and call.
    wait['wait_id'] = owned_wait_id(attempt.runner_id, state.execution_id,
                                  {**wait, 'assistance_id': attempt.number})
    if form is not None:
        wait['form'] = form
    return DispatchResult(None, wait=wait)


def request_workbook(state, call, attempt):
    return _wait(state, call, attempt, FILE_WAIT,
                 '任务中断后，原 Excel 文件无法读取。请上传一份要用于这一步的 .xlsx 文件并发送，我会从原步骤继续。')


def _operation_label(call, name, task_id):
    if task_id:
        return f'发布会话任务（任务编号 {task_id}）'
    arguments = call.arguments
    if call.name == 'email_process' and str(arguments.get('action') or '').strip().lower() == 'send':
        from src.tools.email.email_lib import split_addresses
        recipients = split_addresses(arguments.get('to'))[1]
        address = parseaddr(recipients[0])[1] if recipients else ''
        subject = arguments.get('subject') or ''
        if not isinstance(subject,str) or re.search(
                r'password|passwd|secret|token|api[_ -]?key|authorization|bearer|凭据|密钥|密码|令牌|\$\{',
                subject,re.IGNORECASE):
            subject = ''
        else:
            subject = ' '.join(subject.split())[:60]
        details = []
        if re.fullmatch(r'[\w.+-]+@[\w.-]+',address) and len(address) <= 100:
            details.append(f'收件人 {address}' + (f' 等 {len(recipients)} 人' if len(recipients)>1 else ''))
        if subject:
            details.append(f'主题《{subject}》')
        return '发送邮件' + (f'（{"，".join(details)}）' if details else '')
    if call.name == 'http_api':
        method = str(arguments.get('method') or 'GET').upper()
        if method in {'GET','HEAD','POST','PUT','DELETE','PATCH','OPTIONS'}:
            try:
                host = urlsplit(str(arguments.get('url') or '')).hostname or ''
            except ValueError:
                host = ''
            if re.fullmatch(r'[\w.:-]{1,120}',host):
                return f'发送 HTTP {method} 请求（{host}）'
            return f'发送 HTTP {method} 请求'
    return name


async def request_external_check(state, call, attempt, name, *, task_id=None, can_retry=False):
    from src.tools.agent.ask_user_tool import AskUserTool
    operation = _operation_label(call,name,task_id)
    question = f'「{operation}」执行时服务发生中断。我无法确认操作是否成功，请检查结果后选择如何继续。'
    if call.name == 'http_api':
        identity = canonical_json([attempt.runner_id,state.execution_id,call.id])
        reference = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:10]
        question += f' 原请求编号 {reference}。'
    if task_id is not None and not can_retry:
        question += ' 如果需要重试，请先在工作台核对任务状态和发布授权。'
    choices = ('confirmed_done', 'retry', 'skipped') if can_retry else ('confirmed_done', 'skipped')
    result = await AskUserTool().execute(allow_skip=False, questions=[{
        'prompt': question, 'type': 'single', 'required': True, 'allow_custom_text': False,
        'options': [{'label': _LABELS[choice]} for choice in choices],
    }])
    if not result['success']:
        raise CheckpointFailure('RECOVERY_QUESTION_UNAVAILABLE')
    return _wait(state, call, attempt, EXTERNAL_WAIT, question, form=result['form'], choices=choices)


def _workbook_asset(cursor, tenant_id, user_id, file_id, *, verify=False):
    cursor.execute("""SELECT file_id, name, path FROM file_assets WHERE file_id=%s
        AND tenant_id IS NOT DISTINCT FROM %s AND user_id=%s AND source='upload' AND type='file'""",
        (file_id, tenant_id, user_id))
    row = cursor.fetchone()
    if not row or not row['path'] or Path(row['path']).suffix.lower() != '.xlsx':
        return None
    # The owner row authorizes the ID. The scoped resolver additionally guards
    # filesystem roots, and may otherwise fall back to another discovered path.
    file = resolve_scoped_uploaded_file(file_id, tenant_id, metadata=row)
    if file is None or file['path'] != str(Path(row['path']).resolve()):
        return None
    if verify:
        try:
            with ZipFile(file['path']) as workbook:
                workbook.getinfo('[Content_Types].xml')
                workbook.getinfo('xl/workbook.xml')
        except (OSError, BadZipFile, KeyError):
            return None
    return file


def replacement_workbook(connection_factory, identity, file_id):
    with connection_factory() as connection, connection.cursor() as cursor:
        return _workbook_asset(cursor,identity.tenant_id,identity.user_id,file_id)


def validate_reply(cursor, row, wait, payload):
    binding = recovery_wait(wait)
    attachments = payload.get('attachments') or []
    if binding['purpose'] == FILE_WAIT:
        if (len(attachments) != 1 or not attachments[0].get('file_id')
                or attachments[0].get('type') != 'file' or attachments[0].get('content') is not None):
            raise RunnerError('RECOVERY_EXCEL_REQUIRED', 422)
        file_id = attachments[0]['file_id']
        if _workbook_asset(cursor, row['tenant_id'], row['user_id'], file_id, verify=True) is None:
            raise RunnerError('RECOVERY_EXCEL_UNAVAILABLE', 422)
        return {'file_id': file_id}
    choices = binding['choices']
    if not attachments:
        answers = payload.get('answers')
        if answers is not None:
            if len(answers) == 1 and len(answers[0]['selected_keys']) == 1:
                key = answers[0]['selected_keys'][0]
                for index, choice in enumerate(choices, 1):
                    if key == f'q1o{index}':
                        return {'decision': choice}
        else:
            text = payload['answer'].strip()
            from .ask_form import canonical_text
            for index, choice in enumerate(choices, 1):
                answer = [{'question_id':'q1','selected_keys':[f'q1o{index}'],'text':None}]
                if text in {str(index), _LABELS[choice], canonical_text(wait['form'], answer)}:
                    return {'decision': choice}
    raise RunnerError('RECOVERY_CHOICE_REQUIRED', 422)


def apply_reply(row, control, state, wait, fact):
    payload = control['payload']
    binding = recovery_wait(wait)
    reply = payload.get('recovery_reply') or {}
    if ((binding['purpose'] == FILE_WAIT and not reply.get('file_id'))
            or (binding['purpose'] == EXTERNAL_WAIT and reply.get('decision') not in binding['choices'])):
        raise RunnerError('CONTROL_WAIT_STALE',409)
    state.resources.setdefault('server_recovery_replies', {})[fact.call.id] = {
        **reply, 'control_id': control['control_id'], 'wait_id': wait['wait_id'],
        'attempt': row['attempt'] + 1,
    }
    fact.phase = 'dispatching'
    state.waiting = None


def external_reply_result(decision):
    done = decision == 'confirmed_done'
    message = ('用户确认该操作已完成，系统没有可核验的操作回执。需要具体结果时请向用户补充核对。'
               if done else
               '用户选择跳过，操作结果仍未知。请继续独立步骤，勿重复发起此操作；依赖结果的步骤请说明原因。')
    return DispatchResult({
        'success': done, 'error' if not done else 'message': message,
        'recovery': {'source': 'user', 'decision': decision,
                     'external_status': 'confirmed_by_user' if done else 'unknown'},
    }, success=done, preserve_content=True)


def publication_args(call):
    if call.name == 'session_task_publish':
        from src.tools.weixin.session_task_tools import SessionTaskPublishInput
        try:
            return SessionTaskPublishInput.model_validate(call.arguments)
        except ValidationError:
            return None
    return None


def _effect_digest(call):
    arguments = call.arguments
    if call.name == 'email_process' and str(arguments.get('action') or '').strip().lower() == 'send':
        from src.tools.email.email_lib import split_addresses
        arguments = {
            'action': 'send',
            'to': split_addresses(arguments.get('to'))[1],
            'cc': split_addresses(arguments.get('cc'))[1],
            'subject': arguments.get('subject') or '',
            'body': arguments.get('body') or '',
        }
    elif call.name == 'http_api':
        import httpx
        from src.tools.network.http_api import HttpApiInput
        try:
            request = HttpApiInput.model_validate(arguments)
            url = request.url
            query_params = request.query_params or None
            if '${' not in url:
                # The tool passes nonempty params to HTTPX, where they replace
                # an embedded query. Compare the URL that would actually be sent.
                url = str(httpx.Request(request.method,url,params=query_params).url.copy_with(fragment=None))
                query_params = None
            headers = {}
            for name,value in httpx.Headers(request.headers).multi_items():
                headers.setdefault(name,[]).append(value)
            arguments = {
                'method': request.method.upper(), 'url': url,
                'headers': headers,
                'query_params': query_params or {},
                'files': request.files or {},
                'form_data': request.form_data or {},
                'body': None if request.form_data else request.body,
                'follow_redirects': request.follow_redirects,
            }
        except (ValidationError,httpx.InvalidURL,UnicodeError):
            pass
    else:
        publication = publication_args(call)
        if publication is not None:
            arguments = publication.model_dump(mode='json')
    return hashlib.sha256(canonical_json([call.name, arguments]).encode('utf-8')).hexdigest()


def remember_resolved_effect(state, call, decision):
    # Paired tool facts can be archived. Keep only this Runner's exact
    # effect identity and the decision, never the old parameters or tool result.
    state.resources.setdefault('server_resolved_effects',{})[_effect_digest(call)] = decision


def original_http_call(state, call):
    # JSONB can reorder argument keys. Use the matching serialized declaration
    # for both sending and fingerprinting so HTTP's ordered values stay intact.
    from src.core.agent_engine.contracts import ToolCall
    for message in reversed(state.messages[state.initial_len:]):
        for item in message.get('tool_calls') or []:
            if item.get('id') != call.id:
                continue
            function = item.get('function') or {}
            serialized = function.get('arguments')
            if function.get('name') != call.name or not isinstance(serialized,str):
                continue
            try:
                original = json.loads(serialized)
            except ValueError:
                continue
            if original == call.arguments:
                return ToolCall(call.id,call.name,original)
    return call


def previously_resolved_effect(state, call):
    effects = state.resources.get('server_resolved_effects') or {}
    if effects and (decision := effects.get(_effect_digest(call))):
        result = external_reply_result(decision)
        field = 'message' if result.success else 'error'
        result.content[field] += ' 如需重新发起相同操作，请结束本任务后另开任务明确授权。'
        return result
    return None


def publication_recovery(connection_factory, identity, args):
    from src.session_tasks.config import tenant_allowed
    from src.session_tasks.service import publication_identity
    from src.session_tasks.texts import digest_payload
    route, key, body = publication_identity(args.task_id, args.expected_version, args.confirmation_id)
    digest = digest_payload({'route': route, 'body': body})
    with connection_factory() as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT request_digest, status, response_json
            FROM session_tasks_idempotency_keys
            WHERE tenant_id=%s AND user_id=%s AND route=%s AND idempotency_key=%s""",
            (identity.tenant_id, identity.user_id, route, key))
        row = cursor.fetchone()
        if row is not None:
            if row['request_digest'] == digest and row['status'] == 'completed' and row['response_json']:
                return {'success': True, **json.loads(row['response_json'])}, False
            # An original reservation may still be active after a Runner lease expires.
            return None, False
        if not tenant_allowed(identity.tenant_id):
            return None, False
        cursor.execute("""SELECT task.scenario_key FROM session_tasks task
            JOIN session_task_confirmations confirmation ON confirmation.tenant_id=task.tenant_id
                AND confirmation.task_id=task.id AND confirmation.user_id=task.user_id
                AND confirmation.spec_revision=task.version AND confirmation.spec_digest=task.draft_digest
            WHERE task.tenant_id=%s AND task.user_id=%s AND task.id=%s AND task.version=%s
                AND task.status IN ('draft','paused') AND confirmation.confirmation_id=%s
                AND confirmation.consumed_at IS NULL AND confirmation.expires_at>clock_timestamp()""",
            (identity.tenant_id, identity.user_id, args.task_id, args.expected_version, args.confirmation_id))
        task = cursor.fetchone()
    if task and task['scenario_key'] == 'weixin.conversation.v1':
        from src.weixin_conversation.config import scenario_enabled
        return None, scenario_enabled(identity.tenant_id)
    return None, False
