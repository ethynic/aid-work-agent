"""KF account pull lease and page facts, committed together on one cursor."""

from dataclasses import dataclass, field
import hashlib

from src.db.database import get_db_connection

from .channel_session_repository import find_or_bind_in_tx, read_bound_in_tx
from .ingress_auth import (AccountProof, KfIngressError, VerifiedPage, assert_same,
                           callback_account_in_tx, current_config_in_tx, encoded, text)


@dataclass(frozen=True)
class PullLease:
    proof: AccountProof
    worker_id: str
    epoch: int
    generation: int
    cursor: str = field(repr=False)
    account_order: int


class KfIngressRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    @staticmethod
    def _cursor(connection):
        # Owned short transaction only. The enclosing connection scope rolls
        # back/commits these LOCAL settings; the shared pool policy is unchanged.
        cursor = connection.cursor()
        cursor.execute("SET LOCAL statement_timeout='5s'")
        cursor.execute("SET LOCAL lock_timeout='3s'")
        return cursor

    def accept_callback(self, tenant_id, config_id, query, body):
        with self.connection_factory() as connection:
            cursor = self._cursor(connection)
            account, lifecycle = callback_account_in_tx(cursor, tenant_id, config_id, query, body)
            proof = account.proof
            cursor.execute("""INSERT INTO wecom_kf_account_sync(account_id,tenant_id,config_id,
                corp_id,open_kfid,profile_id,raw_profile,config_version,requested_generation)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,1)
                ON CONFLICT(account_id) DO UPDATE SET requested_generation=wecom_kf_account_sync.requested_generation+1,
                    updated_at=clock_timestamp()
                WHERE wecom_kf_account_sync.tenant_id=EXCLUDED.tenant_id
                  AND wecom_kf_account_sync.config_id=EXCLUDED.config_id
                  AND wecom_kf_account_sync.corp_id=EXCLUDED.corp_id
                  AND wecom_kf_account_sync.open_kfid=EXCLUDED.open_kfid
                  AND wecom_kf_account_sync.profile_id=EXCLUDED.profile_id
                RETURNING account_id,requested_generation""", (proof.account_id, proof.tenant_id,
                proof.config_id, proof.corp_id, proof.open_kfid, proof.profile_id,
                proof.raw_profile, proof.config_version))
            accepted = cursor.fetchone()
            if accepted is None:
                raise KfIngressError("KF_INGRESS_ACCOUNT_CONFLICT")
            if lifecycle is not None:
                self._insert_message(cursor, proof, lifecycle)
            connection.commit()
            return dict(accepted)

    def claim(self, worker_id: str, *, after: int = 0, lease_seconds: int = 30):
        text(worker_id)
        if type(after) is not int or after < 0 or type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
            raise KfIngressError("KF_INGRESS_INVALID_LEASE")
        # Candidate lookup is bounded/keyset. No locks survive this read.
        with self.connection_factory() as connection:
            cursor = self._cursor(connection)
            cursor.execute("""SELECT * FROM wecom_kf_account_sync WHERE account_order>%s
                AND requested_generation>completed_generation
                AND (lease_until IS NULL OR lease_until<=clock_timestamp())
                ORDER BY account_order LIMIT 1""", (after,))
            candidate = cursor.fetchone()
        if candidate is None:
            return None, 0
        next_cursor = candidate["account_order"]
        with self.connection_factory() as connection:
            cursor = self._cursor(connection)
            try:
                account = current_config_in_tx(cursor, candidate["tenant_id"], candidate["config_id"],
                                              candidate["open_kfid"], lock=True)
            except KfIngressError as error:
                # Current config cannot authorize this exact original account.
                # Preserve all intent/cursor facts and expose finite verification.
                cursor.execute("""UPDATE wecom_kf_account_sync SET verification_code=%s
                    WHERE account_id=%s AND (lease_until IS NULL OR lease_until<=clock_timestamp())""",
                    (error.code, candidate["account_id"]))
                connection.commit()
                return None, next_cursor
            cursor.execute("SELECT * FROM wecom_kf_account_sync WHERE account_id=%s FOR UPDATE SKIP LOCKED",
                           (candidate["account_id"],))
            row = cursor.fetchone()
            if row is None:
                return None, next_cursor
            try:
                self._account_matches(row, account.proof, require_version=False)
            except KfIngressError:
                cursor.execute("""UPDATE wecom_kf_account_sync SET verification_code='KF_INGRESS_ACCOUNT_CHANGED'
                    WHERE account_id=%s""", (row["account_id"],))
                connection.commit()
                return None, next_cursor
            cursor.execute("SELECT clock_timestamp() AS now")
            now = cursor.fetchone()["now"]
            if row["requested_generation"] <= row["completed_generation"] or row["lease_until"] is not None and row["lease_until"] > now:
                return None, next_cursor
            cursor.execute("""UPDATE wecom_kf_account_sync SET worker_id=%s,claim_epoch=claim_epoch+1,
                lease_until=clock_timestamp()+(%s * interval '1 second'),config_version=%s,
                verification_code=NULL,updated_at=clock_timestamp()
                WHERE account_id=%s RETURNING *""", (worker_id, lease_seconds, account.proof.config_version, row["account_id"]))
            claimed = cursor.fetchone()
            connection.commit()
            return PullLease(account.proof, worker_id, claimed["claim_epoch"],
                             claimed["requested_generation"], claimed["cursor"], next_cursor), next_cursor

    @staticmethod
    def _account_matches(row, proof, *, require_version=True):
        expected = (proof.account_id, proof.tenant_id, proof.config_id, proof.corp_id,
                    proof.open_kfid, proof.profile_id)
        if tuple(row[key] for key in ("account_id", "tenant_id", "config_id", "corp_id",
                                     "open_kfid", "profile_id")) != expected:
            raise KfIngressError("KF_INGRESS_ACCOUNT_CHANGED")
        if (row["raw_profile"] or "main") != proof.profile_id:
            raise KfIngressError("KF_INGRESS_ACCOUNT_CHANGED")
        if require_version and row["config_version"] != proof.config_version:
            raise KfIngressError("KF_INGRESS_CONFIG_CHANGED")

    def _owned(self, cursor, lease):
        fresh = current_config_in_tx(cursor, lease.proof.tenant_id, lease.proof.config_id,
                                     lease.proof.open_kfid, lock=True)
        assert_same(fresh.proof, lease.proof)
        cursor.execute("SELECT * FROM wecom_kf_account_sync WHERE account_id=%s FOR UPDATE",
                       (lease.proof.account_id,))
        row = cursor.fetchone()
        if row is None:
            raise KfIngressError("KF_INGRESS_LEASE_LOST")
        self._account_matches(row, lease.proof)
        self._live(cursor, row, lease)
        return row

    @staticmethod
    def _live(cursor, row, lease):
        # Independent clock after all potentially waiting row locks.
        cursor.execute("SELECT clock_timestamp() AS now")
        now = cursor.fetchone()["now"]
        if row["worker_id"] != lease.worker_id or row["claim_epoch"] != lease.epoch or row["lease_until"] is None or row["lease_until"] <= now:
            raise KfIngressError("KF_INGRESS_LEASE_LOST")
        if row["cursor"] != lease.cursor or row["requested_generation"] < lease.generation:
            raise KfIngressError("KF_INGRESS_CURSOR_CHANGED")

    def current_account(self, proof):
        with self.connection_factory() as connection:
            account = current_config_in_tx(self._cursor(connection), proof.tenant_id, proof.config_id, proof.open_kfid)
            assert_same(account.proof, proof)
            return account

    def renew(self, lease, lease_seconds=30):
        with self.connection_factory() as connection:
            cursor = self._cursor(connection)
            self._owned(cursor, lease)
            cursor.execute("""UPDATE wecom_kf_account_sync SET lease_until=clock_timestamp()+(%s*interval '1 second')
                WHERE account_id=%s""", (lease_seconds, lease.proof.account_id))
            connection.commit()

    def commit_page(self, lease: PullLease, page: VerifiedPage):
        if not isinstance(page, VerifiedPage):
            raise KfIngressError("KF_INGRESS_PAGE_INVALID")
        if page.has_more and page.next_cursor == lease.cursor:
            raise KfIngressError("KF_INGRESS_CURSOR_STALLED")
        with self.connection_factory() as connection:
            cursor = self._cursor(connection)
            row = self._owned(cursor, lease)
            saved = 0
            # A verified page is the only platform-clock ordering boundary.
            # Keep every event/employee/unsupported fact as a batch barrier;
            # stable sorting preserves original positions at equal timestamps.
            # Existing receipts retain their original sequence in _insert_message.
            messages = sorted(page.messages, key=lambda message: message.send_time)
            for message in messages:
                self._live(cursor, row, lease)
                saved += self._insert_message(cursor, lease.proof, message)
                self._live(cursor, row, lease)
            # Session/route locks and inbox conflicts may have waited. Recheck
            # lease immediately before the cursor/page commit, not old SQL projections.
            self._live(cursor, row, lease)
            cursor.execute("""UPDATE wecom_kf_account_sync SET cursor=%s,
                completed_generation=CASE WHEN %s THEN completed_generation ELSE GREATEST(completed_generation,%s) END,
                worker_id=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE account_id=%s""",
                (page.next_cursor or lease.cursor, page.has_more, lease.generation, lease.proof.account_id))
            connection.commit()
            return {"received": saved, "has_more": page.has_more}

    @staticmethod
    def _insert_message(cursor, proof, message):
        cursor.execute('SELECT 1 FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
                       (proof.account_id,message.namespace,message.message_id))
        existing=cursor.fetchone() is not None
        receipt_seq=None
        if not existing:
            cursor.execute('UPDATE wecom_kf_account_sync SET inbox_seq=inbox_seq+1 WHERE account_id=%s RETURNING inbox_seq',
                           (proof.account_id,))
            receipt_seq=cursor.fetchone()['inbox_seq']
        route = find_or_bind_in_tx(cursor, proof, message.actor_id) if message.actor_id else None
        cursor.execute("""INSERT INTO wecom_kf_inbox(account_id,namespace,message_id,tenant_id,config_id,
            corp_id,open_kfid,actor_id,route_id,origin,message_type,send_time,payload,payload_digest,
            capability_ciphertext,config_version,receive_seq)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
            ON CONFLICT(account_id,namespace,message_id) DO NOTHING RETURNING message_id""",
            (proof.account_id, message.namespace, message.message_id, proof.tenant_id, proof.config_id,
             proof.corp_id, proof.open_kfid, message.actor_id, route["route_id"] if route else None,
             message.origin, message.message_type, message.send_time, encoded(message.payload), message.digest,
             message.capability_ciphertext, proof.config_version,receipt_seq))
        saved = bool(cursor.fetchone())
        cursor.execute("""SELECT payload_digest,payload,tenant_id,config_id,corp_id,open_kfid,actor_id,capability_ciphertext,
            route_id,origin,message_type,send_time
            FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s""",
            (proof.account_id, message.namespace, message.message_id))
        original = cursor.fetchone()
        if original is None:
            raise KfIngressError("KF_INGRESS_MESSAGE_CONFLICT")
        digest_matches=original['payload_digest']==message.digest
        if not digest_matches and message.namespace=='sync' and message.message_type=='voice':
            old_payload=original['payload']
            old_voice=old_payload.get('voice') if isinstance(old_payload,dict) else None
            new_voice=message.payload.get('voice')
            if (isinstance(old_voice,dict) and set(old_voice)=={'media_id'}
                    and isinstance(new_voice,dict) and set(new_voice)=={'media_id','recognition'}
                    and hashlib.sha256(encoded(old_payload).encode()).hexdigest()==original['payload_digest']):
                candidate={**message.payload,'voice':{'media_id':new_voice['media_id']}}
                digest_matches=candidate==old_payload
        if (not digest_matches or tuple(original[key] for key in
                ('tenant_id','config_id','corp_id','open_kfid','actor_id','origin','message_type','send_time','route_id')) != (
                proof.tenant_id,proof.config_id,proof.corp_id,proof.open_kfid,message.actor_id,
                message.origin,message.message_type,message.send_time,route['route_id'] if route else None)):
            raise KfIngressError("KF_INGRESS_MESSAGE_CONFLICT")
        if not saved:
            old, new = original["capability_ciphertext"], message.capability_ciphertext
            if bool(old) != bool(new):
                raise KfIngressError("KF_INGRESS_MESSAGE_CONFLICT")
            if old:
                from src.core.secret_crypto import decrypt_secret
                if decrypt_secret(old) != decrypt_secret(new):
                    raise KfIngressError("KF_INGRESS_MESSAGE_CONFLICT")
        return int(saved)

    def read_received(self, proof, namespace, message_id):
        """Internal fresh original inbox/route lookup, not a public Runner identity DTO."""
        if namespace not in ("sync", "callback"):
            raise KfIngressError("KF_INGRESS_MESSAGE_INVALID")
        text(message_id)
        with self.connection_factory() as connection:
            cursor = self._cursor(connection)
            current = current_config_in_tx(cursor, proof.tenant_id, proof.config_id, proof.open_kfid, lock=True)
            assert_same(current.proof, proof)
            cursor.execute("""SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s
                AND message_id=%s AND tenant_id=%s AND config_id=%s AND corp_id=%s AND open_kfid=%s""",
                (proof.account_id, namespace, message_id, proof.tenant_id, proof.config_id, proof.corp_id, proof.open_kfid))
            inbox = cursor.fetchone()
            if inbox is None:
                raise KfIngressError("KF_INGRESS_MESSAGE_UNAVAILABLE")
            if inbox["actor_id"]:
                route = read_bound_in_tx(cursor, proof, inbox["actor_id"], inbox["route_id"])
                if route["route_id"] != inbox["route_id"]:
                    raise KfIngressError("KF_INGRESS_ROUTE_INVALID")
            return dict(inbox)

    def release(self, lease):
        """After actual IO/drain only; release physical lease, never generation/cursor."""
        with self.connection_factory() as connection:
            cursor = self._cursor(connection)
            cursor.execute("""UPDATE wecom_kf_account_sync SET worker_id=NULL,lease_until=NULL
                WHERE account_id=%s AND tenant_id=%s AND worker_id=%s AND claim_epoch=%s""",
                (lease.proof.account_id, lease.proof.tenant_id, lease.worker_id, lease.epoch))
            connection.commit()
