"""Runner storage transactions. No execution tasks or transport objects live here."""

import json
import uuid

from src.db.database import get_db_connection
from .contracts import RunnerError, RunnerStatus, scope_key


from .persistence_limits import capped_checkpoint_dumps, capped_snapshot_dumps
from .public_view import decoded, public_runner
from .event_repository import EventRepository


class RunnerRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def assert_schema(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            for name in ("agent_runners", "agent_runner_session_claims", "agent_runner_usage_receipts",'agent_runner_controls',
                         'agent_runner_events'):
                cursor.execute("SELECT to_regclass(%s) AS name", (name,))
                if not cursor.fetchone()["name"]:
                    raise RuntimeError("AGENT_RUNNER_SCHEMA_REQUIRED")
            cursor.execute('SELECT event_seq,event_floor_seq FROM agent_runners LIMIT 0')

    def submit(self, principal, request, profile_fingerprint, *, resolved_profile_id=None, execution_context=None):
        with self.connection_factory() as conn:
            row,created=self.submit_in_tx(conn.cursor(),principal,request,profile_fingerprint,
                resolved_profile_id=resolved_profile_id,execution_context=execution_context)
            conn.commit()
            return row,created

    def submit_in_tx(self, cursor, principal, request, profile_fingerprint, *, resolved_profile_id=None,
                     execution_context=None, initial_checkpoint=None):
        identity=principal.identity
        runner_id='runner_'+uuid.uuid4().hex
        # 公开快照只存附件展示字段（control_repository.supplementalInputs 先例）；
        # 内联 content 仍在私有 input 列供 worker 装配，接口形状不变。
        snapshot={'input':{'message_id':runner_id+':user','text':request.text,
            'attachments':[{key:item.model_dump(mode='json')[key]
                            for key in ('file_id','name','type','mime_type','size')}
                           for item in request.attachments]},
            'progress':None,'output':'','images':[]}
        cursor.execute("""INSERT INTO agent_runners
            (runner_id, tenant_id, scope_key, session_kind, session_id, actor_kind, actor_id, user_id,
             service_id, source, client_request_id, input_digest, input, profile_id, profile_fingerprint,
             record_id, public_snapshot, checkpoint)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s::jsonb)
            ON CONFLICT (scope_key, actor_kind, actor_id, source, client_request_id) DO NOTHING
            RETURNING *""", (runner_id, identity.tenant_id, principal.scope_key,
            identity.session_kind, identity.session_id, principal.actor_kind, principal.actor_id,
            identity.user_id, principal.service_id, identity.source, request.client_request_id,
            request.digest(), json.dumps(request.intent(), ensure_ascii=False), resolved_profile_id or request.profile_id,
            profile_fingerprint, "record_" + uuid.uuid4().hex, capped_snapshot_dumps(snapshot),
            capped_checkpoint_dumps(initial_checkpoint if initial_checkpoint is not None else ({'execution_context':execution_context} if execution_context is not None else {}))))
        row = cursor.fetchone()
        created = row is not None
        if row is None:
            cursor.execute("""SELECT * FROM agent_runners WHERE scope_key=%s AND actor_kind=%s
                AND actor_id=%s AND source=%s AND client_request_id=%s""",
                (principal.scope_key, principal.actor_kind, principal.actor_id, identity.source, request.client_request_id))
            row = cursor.fetchone()
            if row is None:
                raise RunnerError("IDEMPOTENCY_CONFLICT", 409)
            if row["input_digest"] != request.digest():
                raise RunnerError("IDEMPOTENCY_INPUT_MISMATCH", 409)
            self.assert_owner(principal, row)
        if created:
            row = EventRepository.notify_in_tx(cursor, None, after=decoded(row), kind="created")
        return decoded(row),created

    def get(self, runner_id):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_runners WHERE runner_id=%s", (runner_id,))
            row = decoded(cursor.fetchone())
            if row is None:
                raise RunnerError("RUNNER_NOT_FOUND", 404)
            return row

    def find_request(self, principal, request):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT * FROM agent_runners WHERE scope_key=%s AND actor_kind=%s
                AND actor_id=%s AND source=%s AND client_request_id=%s""",
                (principal.scope_key, principal.actor_kind, principal.actor_id,
                 principal.identity.source, request.client_request_id))
            row = decoded(cursor.fetchone())
            if row and row["input_digest"] != request.digest():
                raise RunnerError("IDEMPOTENCY_INPUT_MISMATCH", 409)
            if row:
                self.assert_owner(principal, row)
            return row

    def list_session(self, principal, limit=100, before_runner_id=None):
        identity = principal.identity
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            where = """scope_key=%s AND session_kind=%s AND session_id=%s
                AND actor_kind=%s AND actor_id=%s AND user_id IS NOT DISTINCT FROM %s AND source=%s"""
            parameters = (principal.scope_key, identity.session_kind, identity.session_id,
                          principal.actor_kind, principal.actor_id, identity.user_id, identity.source)
            before = None
            if before_runner_id:
                cursor.execute("SELECT queue_order FROM agent_runners WHERE " + where + " AND runner_id=%s",
                               (*parameters, before_runner_id))
                previous = cursor.fetchone()
                if not previous:
                    raise RunnerError("CURSOR_NOT_FOUND", 404)
                before = previous["queue_order"]
            page_where = where + (" AND queue_order<%s" if before is not None else "")
            page_parameters = parameters + ((before,) if before is not None else ())
            cursor.execute("SELECT * FROM agent_runners WHERE " + page_where + " ORDER BY queue_order DESC LIMIT %s",
                           (*page_parameters, limit + 1))
            rows = [decoded(row) for row in cursor.fetchall()]
            has_more = len(rows) > limit
            page = rows[:limit]
            # Active ownership is returned independently of history pagination.
            # A queue backlog cannot hide the runner a refreshed page must follow.
            cursor.execute("SELECT * FROM agent_runners WHERE " + where + """ AND status IN
                ('running','waiting','paused','interrupted','finalizing') ORDER BY queue_order DESC""", parameters)
            active = [decoded(row) for row in cursor.fetchall()]
            return {"rows": page, "active": active, "has_more": has_more,
                    "next_cursor": page[-1]["runner_id"] if has_more else None}

    def cancel(self, principal, runner_id):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE", (runner_id,))
            row = decoded(cursor.fetchone())
            if row is None:
                raise RunnerError("RUNNER_NOT_FOUND", 404)
            self.assert_owner(principal, row)
            previous = row
            view_advanced=False
            if row["status"] == RunnerStatus.QUEUED.value:
                # No execution/claim/usage existed. Keep accepted input in its
                # projection; no chat/record/debit is fabricated for unstarted work.
                cursor.execute("""UPDATE agent_runners SET status='cancelled', cancel_requested=TRUE,
                    settlement_status='settled', revision=revision+1, view_revision=view_revision+1,
                    control_revision=control_revision+1, updated_at=CURRENT_TIMESTAMP,
                    finished_at=CURRENT_TIMESTAMP WHERE runner_id=%s RETURNING *""", (runner_id,))
                row = decoded(cursor.fetchone())
                if (row.get('checkpoint') or {}).get('source_initial_ref'):
                    cursor.execute("UPDATE agent_runner_inputs SET phase='cancelled' WHERE current_runner_id=%s AND phase IN ('accepted','attached','deferred')",(runner_id,))
                view_advanced=True
            elif row["status"] not in ("completed", "failed", "cancelled") and not row["cancel_requested"]:
                cursor.execute("""UPDATE agent_runners SET cancel_requested=TRUE,
                    view_revision=view_revision+1, control_revision=control_revision+1,
                    updated_at=CURRENT_TIMESTAMP WHERE runner_id=%s RETURNING *""", (runner_id,))
                row = decoded(cursor.fetchone())
                view_advanced=True
            if row['cancel_requested'] or row['status'] in ('completed','failed','cancelled'):
                from .control_repository import close_pending_controls
                close_pending_controls(cursor,runner_id,'CONTROL_EXECUTION_CLOSED')
                cursor.execute('''UPDATE agent_runners SET resume_control_id=NULL,pause_requested=FALSE
                    WHERE runner_id=%s RETURNING *''',(runner_id,))
                row = decoded(cursor.fetchone())
            from .application_public_projection import sync_public_display
            row=sync_public_display(cursor,row,advance_view=not view_advanced)
            row=EventRepository.notify_in_tx(cursor,previous,after=row)
            conn.commit()
            return row

    @staticmethod
    def assert_owner(principal, row):
        identity = principal.identity
        expected = (principal.scope_key, identity.session_kind, identity.session_id,
                    principal.actor_kind, principal.actor_id, identity.user_id, identity.source)
        actual = (row["scope_key"], row["session_kind"], row["session_id"], row["actor_kind"],
                  row["actor_id"], row["user_id"], row["source"])
        if expected != actual or scope_key(row["tenant_id"]) != row["scope_key"]:
            raise RunnerError("RUNNER_NOT_FOUND", 404)
