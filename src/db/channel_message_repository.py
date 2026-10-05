"""Channel-message persistence primitives without channel execution/SDK imports."""

import json


def insert_message(cursor, *, message_id, session_id, tenant_id, role, content,
                   message_type='text', attachments=None, metadata=None, created_at=None,
                   is_recalled=False, idempotent=False):
    columns = ['message_id','session_id','tenant_id','role','content','message_type','attachments','metadata']
    values = [message_id,session_id,tenant_id,role,content,message_type,
              json.dumps(attachments,ensure_ascii=False,default=str) if attachments else None,
              json.dumps(metadata,ensure_ascii=False,default=str) if metadata else None]
    if created_at is not None:
        columns.append('created_at')
        values.append(created_at)
    sql_values = ['%s'] * len(columns)
    if is_recalled:
        columns.extend(['is_recalled','recalled_at'])
        sql_values.extend(['TRUE','NOW()'])
    suffix = ' ON CONFLICT(message_id) DO NOTHING' if idempotent else ''
    cursor.execute('INSERT INTO channel_messages ('+','.join(columns)+') VALUES ('+','.join(sql_values)+')'+suffix,values)
    return message_id


def touch_session(cursor, session_id, when=None):
    if when is None:
        cursor.execute('UPDATE channel_sessions SET last_message_at=clock_timestamp(),updated_at=clock_timestamp() WHERE session_id=%s', (session_id,))
    else:
        cursor.execute('UPDATE channel_sessions SET last_message_at=%s,updated_at=%s WHERE session_id=%s', (when,when,session_id))
