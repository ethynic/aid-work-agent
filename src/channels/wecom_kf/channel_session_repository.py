"""KF same-cursor session binding; no cache, registration, execution or commit."""

import uuid

from .ingress_auth import AccountProof, KfIngressError, encoded, text


def find_or_bind_in_tx(cursor, proof: AccountProof, actor_id: str, *, user_id=None):
    text(actor_id)
    if user_id is not None:
        text(user_id)
    route_id = str(uuid.uuid5(uuid.NAMESPACE_URL, encoded([
        proof.account_id, actor_id, "kf_direct", proof.open_kfid, proof.profile_id])))
    cursor.execute("""SELECT * FROM channel_session_routes WHERE route_id=%s
        AND tenant_id=%s FOR UPDATE""", (route_id, proof.tenant_id))
    route = cursor.fetchone()
    if route:
        return _validate_bound(cursor, proof, actor_id, route, lock=True)
    # Empty default selector was used by legacy KF, whereas Runner calls it
    # main. Read both only for that exact default, never a different account.
    cursor.execute("""SELECT * FROM channel_sessions WHERE tenant_id=%s AND channel_type='wecom_kf'
        AND channel_user_id=%s AND channel_chat_id=%s AND COALESCE(subagent_id,'') IN (%s,%s)
        ORDER BY id LIMIT 2 FOR UPDATE""",
        (proof.tenant_id, actor_id, proof.open_kfid, "" if proof.profile_id == "main" else proof.profile_id, proof.profile_id))
    matches = cursor.fetchall()
    if len(matches) > 1:
        raise KfIngressError("KF_INGRESS_SESSION_AMBIGUOUS")
    legacy = bool(matches)
    if legacy:
        session = matches[0]
        sid, bound_user = session["session_id"], session["user_id"]
    else:
        sid, bound_user = "kf_" + route_id, user_id
        cursor.execute("""INSERT INTO channel_sessions(session_id,tenant_id,channel_type,
            channel_user_id,subagent_id,channel_chat_id,user_id,title)
            VALUES(%s,%s,'wecom_kf',%s,%s,%s,%s,'wecom_kf会话')""",
            (sid, proof.tenant_id, actor_id, proof.raw_profile, proof.open_kfid, bound_user))
    cursor.execute("""INSERT INTO channel_session_routes(route_id,tenant_id,source,config_id,
        corp_id,open_kfid,actor_id,chat_kind,chat_id,profile_id,raw_profile,session_id,user_id,
        legacy_shared,config_version) VALUES(%s,%s,'wecom_kf',%s,%s,%s,%s,'kf_direct',%s,%s,%s,%s,%s,%s,%s)
        RETURNING *""", (route_id, proof.tenant_id, proof.config_id, proof.corp_id, proof.open_kfid,
        actor_id, proof.open_kfid, proof.profile_id, proof.raw_profile, sid, bound_user, legacy, proof.config_version))
    return cursor.fetchone()


def read_bound_in_tx(cursor, proof: AccountProof, actor_id: str, route_id: str, *, lock=False):
    """Validate immutable received route/session facts without creating them."""
    text(actor_id); text(route_id)
    cursor.execute("SELECT * FROM channel_session_routes WHERE route_id=%s AND tenant_id=%s"
                   + (" FOR UPDATE" if lock else ""),
                   (route_id, proof.tenant_id))
    route = cursor.fetchone()
    if route is None:
        raise KfIngressError("KF_INGRESS_ROUTE_UNAVAILABLE")
    return _validate_bound(cursor, proof, actor_id, route, lock=lock)


def _validate_bound(cursor, proof, actor_id, route, *, lock):
    expected = ("wecom_kf", proof.config_id, proof.corp_id, proof.open_kfid,
                actor_id, "kf_direct", proof.open_kfid, proof.profile_id)
    if tuple(route[key] for key in ("source", "config_id", "corp_id", "open_kfid",
            "actor_id", "chat_kind", "chat_id", "profile_id")) != expected:
        raise KfIngressError("KF_INGRESS_ROUTE_INVALID")
    if (route["raw_profile"] or "main") != proof.profile_id:
        raise KfIngressError("KF_INGRESS_ROUTE_INVALID")
    cursor.execute("""SELECT * FROM channel_sessions WHERE session_id=%s AND tenant_id=%s
        AND channel_type='wecom_kf' AND channel_user_id=%s AND channel_chat_id=%s
        AND COALESCE(subagent_id,'') IN (%s,%s)""" + (" FOR UPDATE" if lock else ""),
        (route["session_id"], proof.tenant_id, actor_id, proof.open_kfid,
         "" if proof.profile_id == "main" else proof.profile_id, proof.profile_id))
    session = cursor.fetchone()
    if session is None or session["user_id"] != route["user_id"]:
        raise KfIngressError("KF_INGRESS_SESSION_LOST")
    return route
