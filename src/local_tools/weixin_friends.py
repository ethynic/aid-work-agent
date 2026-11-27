"""Connection Center friend-request imports and owner-scoped batch history."""

import csv
import re
from collections import Counter
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import PureWindowsPath
from uuid import UUID
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from psycopg2.extras import Json
from pydantic import BaseModel, ConfigDict, Field

from src.config.settings import settings
from src.db.database import get_db_connection
from src.local_tools import catalog, device_policy, repository, weixin_tracking
from src.local_tools.plugin_manifests import MANIFEST_DIGESTS, load_device_tools
from src.saas.services.credit_gate import check_tenant_credit_blocked

TOOL = catalog.WEIXIN_FRIEND_ADD
KIND = catalog.WEIXIN_FRIEND_BATCH_KIND
PROVIDER = 'weixin'
MAX_ROWS = 200
MAX_FILE_BYTES = 2 * 1024 * 1024
IDENTIFIER = re.compile(r'(?:\+?[0-9]+|[A-Za-z0-9_-]+)')
CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f]')
CONFIRMED = {'request_sent': 'applied', 'friend_added': 'applied',
             'already_friend': 'none', 'already_requested': 'none'}
BLOCKING = {
    'queued': 'REQUEST_IN_PROGRESS', 'running': 'REQUEST_IN_PROGRESS',
    'request_sent': 'PREVIOUS_REQUEST', 'already_requested': 'PREVIOUS_REQUEST',
    'friend_added': 'PREVIOUS_FRIEND', 'already_friend': 'PREVIOUS_FRIEND',
    'unknown': 'PREVIOUS_UNKNOWN',
}
ACTIVE = {'queued', 'running'}
DEFINITE_FAILURES = {
    'BAD_ARGUMENT', 'INVALID_ARGUMENT', 'BUSY', 'CONFIG_MISSING', 'DESKTOP_NOT_INTERACTIVE', 'FOREGROUND_LOST',
    'NOT_LOGGED_IN', 'PROVIDER_NOT_AVAILABLE', 'TARGET_NOT_FOUND', 'TOOL_NOT_ALLOWED',
    'WEIXIN_NOT_FOUND', 'WINDOW_AMBIGUOUS', 'WINDOW_NOT_FOUND', 'UI_CHANGED', 'NO_CREDIT',
    'PROTOCOL_NOT_SUPPORTED', 'DESKTOP_RESOURCE_BUSY',
    repository.PLUGIN_CHANGED_CODE,
}


class Unavailable(ValueError):
    pass


class Contact(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str = Field(default='', max_length=128)
    identifier: str = Field(default='', max_length=256)
    verify_message: str = Field(default='', max_length=256)
    selected: bool


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: UUID
    device_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    verify_message: str = Field(default='', max_length=60)
    contacts: list[Contact] = Field(min_length=1, max_length=MAX_ROWS)


def _file_name(filename):
    return CONTROL.sub('', PureWindowsPath(filename).name)[:255]


def _valid_message(value):
    return len(value) <= 60 and not CONTROL.search(value)


def _worksheet_rows(content):
    with ZipFile(BytesIO(content)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024:
            raise Unavailable('FILE_TOO_LARGE')
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        if not workbook.worksheets:
            raise Unavailable('INVALID_FILE')
        sheet = workbook.worksheets[0]
        # The XLSX dimension can misrepresent the rows a producer actually wrote.
        sheet.reset_dimensions()
        yield from sheet.iter_rows(values_only=True)
    finally:
        workbook.close()


def _cell(value):
    if value is None:
        return ''
    return str(value).strip()


def parse_file(filename, content):
    filename = _file_name(filename)
    extension = PureWindowsPath(filename).suffix.lower()
    if len(content) > MAX_FILE_BYTES:
        raise Unavailable('FILE_TOO_LARGE')
    if extension not in {'.xlsx', '.csv'}:
        raise Unavailable('INVALID_FILE')
    try:
        if extension == '.xlsx':
            records = _worksheet_rows(content)
        else:
            records = csv.reader(StringIO(content.decode('utf-8-sig')), strict=True)
        header = next(records, ())
        fields = {'手机号或微信号': 'identifier', '称呼': 'name', '验证语': 'verify_message'}
        columns = {}
        for index, column in enumerate(header):
            field = fields.get(_cell(column))
            if field in columns:
                raise Unavailable('DUPLICATE_HEADER')
            if field is not None:
                columns[field] = index
        if 'identifier' not in columns:
            raise Unavailable('MISSING_HEADER')
        rows = []
        for record in records:
            if not any(_cell(value) for value in record):
                continue
            cells = {key: _cell(record[index]) if index < len(record) else ''
                     for key, index in columns.items()}
            rows.append(Contact(
                name=CONTROL.sub('', cells.get('name', ''))[:128],
                identifier=cells['identifier'][:256],
                verify_message=cells.get('verify_message', '')[:256],
                selected=True,
            ))
            if len(rows) > MAX_ROWS:
                raise Unavailable('TOO_MANY_CONTACTS')
    except Unavailable:
        raise
    except (BadZipFile, InvalidFileException, ValueError, csv.Error, ParseError, KeyError, OSError) as error:
        raise Unavailable('INVALID_FILE') from error
    if not rows:
        raise Unavailable('EMPTY_FILE')
    return filename, rows


def project_outcome(invocation, now):
    if not invocation:
        return 'unknown', 'MISSING_RECEIPT', None
    inv = repository.observed_invocation(invocation)
    state = inv['state']
    started = inv['started_at'] is not None
    if state in {'queued', 'claimed'} and inv['deadline_at'] <= now:
        return 'failed', 'RESULT_TIMEOUT', None
    if state in {'claimed', 'running', 'cancel_requested'}:
        lease = inv['lease_expires_at']
        if lease is None or lease <= now:
            return ('unknown' if started else 'failed'), 'RESULT_TIMEOUT', None
    if state == 'queued':
        return 'queued', None, None
    if state in {'claimed', 'running', 'cancel_requested'}:
        return 'running', None, None
    result = inv['result_json'] or {}
    data = result.get('data') or {}
    status = data.get('status')
    if state == 'succeeded' and started and CONFIRMED.get(status) == inv['effect'] and status in CONFIRMED:
        return status, None, data.get('confirmed_at') if status == 'request_sent' else None
    if not started or (state in {'failed', 'cancelled', 'expired'} and inv['effect'] == 'none'):
        return 'failed', inv['error_code'] or 'NOT_SUBMITTED', None
    return 'unknown', inv['error_code'] or 'EXECUTION_UNKNOWN', None


_INVOCATION_FIELDS = """id,state,effect,error_code,result_json,started_at,deadline_at,
                         lease_expires_at::timestamptz AS lease_expires_at"""


def _previous(cursor, tenant_id, user_id, contacts, now):
    targets = list({target for item in contacts if
                    len(target := item.identifier.strip()) <= 64 and IDENTIFIER.fullmatch(target)})
    cursor.execute(f"""SELECT {_INVOCATION_FIELDS}, arguments_json->>'identifier' AS identifier
                       FROM local_tool_invocations WHERE tenant_id=%s AND user_id=%s
                        AND business_kind=%s AND arguments_json->>'identifier'=ANY(%s)
                       ORDER BY created_at,id""", (tenant_id, user_id, KIND, targets))
    previous = {}
    for row in cursor.fetchall():
        status, _, _ = project_outcome(row, now)
        if status in BLOCKING:
            previous[row['identifier']] = BLOCKING[status]
    return previous


def classify(contacts, previous):
    seen = set()
    for contact in contacts:
        identifier = contact.identifier.strip()
        verification = contact.verify_message.strip()
        exclusion = None
        if not identifier:
            exclusion = 'MISSING_IDENTIFIER'
        elif len(identifier) > 64 or not IDENTIFIER.fullmatch(identifier):
            exclusion = 'INVALID_IDENTIFIER'
        elif not _valid_message(verification):
            exclusion = 'INVALID_VERIFICATION'
        elif identifier in seen:
            exclusion = 'DUPLICATE'
        else:
            seen.add(identifier)
            exclusion = previous.get(identifier)
        yield contact, identifier, verification, exclusion


def preview(tenant_id, user_id, filename, content):
    filename, contacts = parse_file(filename, content)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        previous = _previous(cursor, tenant_id, user_id, contacts, weixin_tracking.clock(cursor))
    rows = [{**contact.model_dump(), 'exclusion': exclusion}
            for contact, _, _, exclusion in classify(contacts, previous)]
    return {'filename': filename, 'contacts': rows}


def _check_device(cursor, tenant_id, user_id, device_id):
    if not repository.lock_selected_device(cursor, tenant_id, user_id, device_id):
        raise Unavailable('DEVICE_NOT_SELECTED')
    cursor.execute('''SELECT selected,status,last_seen_at,capabilities_json,clock_timestamp() AS now,
                             clock_timestamp()::timestamp AS local_now
                      FROM local_tool_devices WHERE id=%s AND tenant_id=%s AND user_id=%s''',
                   (device_id, tenant_id, user_id))
    device = cursor.fetchone()
    capabilities = device['capabilities_json'] or {}
    seen = device['last_seen_at']
    readiness_clock = device['now'] if seen and seen.tzinfo else device['local_now']
    readiness = device_policy.device_ready_error(device, TOOL, readiness_clock, declared_provider=PROVIDER)
    if readiness == 'offline':
        raise Unavailable('DEVICE_OFFLINE')
    declared = catalog.declared_tools_for_device(capabilities) or {}
    tools = load_device_tools(settings.agent_runner.plugin_trust_roots, capabilities,
                              {PROVIDER: declared.get(PROVIDER)})
    parallel = catalog.parallel_tools_for_device(capabilities).get(PROVIDER, [])
    if readiness or TOOL in parallel or not any(
        provider == PROVIDER and tool['name'] == TOOL
        and (tool.get('annotations') or {}).get('readOnlyHint') is False
        for provider, tool in tools
    ):
        raise Unavailable('WEIXIN_FRIEND_PLUGIN_UNAVAILABLE')
    return capabilities[MANIFEST_DIGESTS][PROVIDER]


def _load_request(cursor, tenant_id, user_id, request_id):
    cursor.execute('''SELECT * FROM bs_weixin_friend_batches
                      WHERE tenant_id=%s AND user_id=%s AND id=%s''',
                   (tenant_id, user_id, str(request_id)))
    return cursor.fetchone()


def create_batch(tenant_id, user_id, body):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        repository.lock_operation_admission(cursor, tenant_id)
        row = _load_request(cursor, tenant_id, user_id, body.request_id)
        now = weixin_tracking.clock(cursor)
        if row:
            return _project_batch(cursor, row, now)
        default_message = body.verify_message.strip()
        filename = _file_name(body.filename)
        if not filename or not _valid_message(default_message):
            raise Unavailable('INVALID_VERIFICATION')
        device_id = str(body.device_id)
        digest = _check_device(cursor, tenant_id, user_id, device_id)
        if check_tenant_credit_blocked(tenant_id) is not None:
            raise Unavailable('NO_CREDIT')
        previous = _previous(cursor, tenant_id, user_id, body.contacts, now)
        contacts = []
        enqueued = 0
        for index, (contact, identifier, verify, exclusion) in enumerate(classify(body.contacts, previous)):
            record = {'name': CONTROL.sub('', contact.name), 'identifier': CONTROL.sub('', identifier)}
            if exclusion or not contact.selected:
                record['exclusion'] = exclusion or 'NOT_SELECTED'
            else:
                verify = verify or default_message
                args = {'identifier': identifier, **({'verify_message': verify} if verify else {})}
                record['invocation_id'] = repository.create_invocation_in_tx(
                    cursor, tenant_id, user_id, device_id, TOOL, args, provider_key=PROVIDER,
                    business_kind=KIND, business_ref={'version': 1, 'batch_id': str(body.request_id), 'position': index,
                                                      'plugin_manifest_digest': digest},
                    dedupe_key=f'{user_id}:{body.request_id}:{index}',
                    deadline_at=now + timedelta(minutes=5 + 3 * enqueued),
                )
                enqueued += 1
            contacts.append(record)
        cursor.execute('''INSERT INTO bs_weixin_friend_batches (id,tenant_id,user_id,device_id,filename,contacts_json)
                          VALUES (%s,%s,%s,%s,%s,%s) RETURNING *''',
                       (str(body.request_id), tenant_id, user_id, device_id, filename, Json(contacts)))
        result = _project_batch(cursor, cursor.fetchone(), now)
        conn.commit()
        return result


def _load_invocations(cursor, rows, tenant_id, user_id):
    ids = [item['invocation_id'] for row in rows for item in row['contacts_json'] if item.get('invocation_id')]
    cursor.execute(f"""SELECT {_INVOCATION_FIELDS} FROM local_tool_invocations
                       WHERE id=ANY(%s::uuid[]) AND tenant_id=%s AND user_id=%s AND business_kind=%s""",
                   (ids, tenant_id, user_id, KIND))
    return {str(row['id']): row for row in cursor.fetchall()}


def _project_batch(cursor, row, now, *, invocations=None, detail=True):
    if invocations is None:
        invocations = _load_invocations(cursor, [row], row['tenant_id'], row['user_id'])
    contacts = []
    for item in row['contacts_json']:
        if item.get('exclusion'):
            status, reason, timestamp = 'skipped', item['exclusion'], None
        else:
            status, reason, timestamp = project_outcome(invocations.get(item['invocation_id']), now)
        contacts.append({'name': item['name'], 'identifier': item['identifier'], 'status': status,
                         'reason': reason, 'submitted_at': timestamp})
    counts = Counter(item['status'] for item in contacts)
    state = 'running' if counts.keys() & ACTIVE else 'completed'
    return {'id': row['id'], 'filename': row['filename'], 'created_at': row['created_at'],
            'state': state, 'total': len(contacts), 'counts': dict(counts),
            **({'contacts': contacts} if detail else {})}


def get_batch(tenant_id, user_id, batch_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = _load_request(cursor, tenant_id, user_id, batch_id)
        if not row:
            raise Unavailable('FRIEND_BATCH_NOT_FOUND')
        return _project_batch(cursor, row, weixin_tracking.clock(cursor))


def list_batches(tenant_id, user_id, offset):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT * FROM bs_weixin_friend_batches WHERE tenant_id=%s AND user_id=%s
                          ORDER BY created_at DESC,id DESC LIMIT 31 OFFSET %s''', (tenant_id, user_id, offset))
        rows = cursor.fetchall()
        now = weixin_tracking.clock(cursor)
        invocations = _load_invocations(cursor, rows[:30], tenant_id, user_id)
        return {'batches': [_project_batch(cursor, row, now, invocations=invocations, detail=False)
                            for row in rows[:30]], 'has_more': len(rows) > 30}
