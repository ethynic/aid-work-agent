"""Evidence of complete knowledge results still present in the model context."""

import hashlib
import json


FORMAT = 1


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def chunk_fingerprint(chunk):
    return fingerprint({"text": chunk["text"], "metadata": chunk["metadata"]})


def context_inventory(messages):
    """Only paired, unmodified full bodies count; omitted bodies cannot prove reuse."""
    calls = {}
    chunks, documents, scopes = set(), set(), set()
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict) and call.get("id"):
                    function = call.get("function") or {}
                    calls[call["id"]] = function.get("name") if isinstance(function, dict) else None
        if message.get("role") != "tool" or calls.get(message.get("tool_call_id")) != "knowledge_base_search":
            continue
        try:
            payload = json.loads(message.get("content") or "")
            if not isinstance(payload, dict) or not payload.get("success"):
                continue
            # Legacy results have no fingerprints, but their visible document and
            # chunk metadata can still identify a category whose access was revoked.
            if payload.get("_knowledge_result_format") != FORMAT:
                for doc_id, document in (payload.get("documents") or {}).items():
                    if not isinstance(document, dict) or not isinstance(document.get("source_type"), str):
                        continue
                    for chunk in payload.get("results") or []:
                        if isinstance(chunk, dict) and str(chunk.get("doc_id")) == str(doc_id) and isinstance(chunk.get("metadata"), dict):
                            scopes.add((str(doc_id), chunk["metadata"].get("owner_tenant_id"), document["source_type"]))
                            break
                continue
            for chunk in payload.get("results") or []:
                if (isinstance(chunk, dict) and isinstance(chunk.get("text"), str)
                        and isinstance(chunk.get("metadata"), dict)
                        and chunk.get("fingerprint") == chunk_fingerprint(chunk)):
                    chunks.add((str(chunk["doc_id"]), chunk["chunk_index"], chunk["fingerprint"]))
            for doc_id, document in (payload.get("documents") or {}).items():
                if (isinstance(document, dict) and
                        (payload.get("document_fingerprints") or {}).get(str(doc_id)) == fingerprint(document)):
                    documents.add((str(doc_id), fingerprint(document)))
            for doc_id, scope in (payload.get("knowledge_scopes") or {}).items():
                if (isinstance(scope, dict) and isinstance(scope.get("source_type"), str)
                        and (any(item[0] == str(doc_id) for item in chunks)
                             or any(item[0] == str(doc_id) for item in documents))):
                    scopes.add((str(doc_id), scope.get("owner_tenant_id"), scope["source_type"]))
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
    return frozenset(chunks), frozenset(documents), frozenset(scopes)
