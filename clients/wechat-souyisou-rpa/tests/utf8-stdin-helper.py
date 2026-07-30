"""Strict UTF-8 stdin round-trip helper for the PowerShell process test."""

import json
import sys


sys.stdout.reconfigure(encoding="utf-8")
payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="strict"))
assert payload["association_name"] == "中国缝制机械协会"
assert payload["person_name"] == "陈戟"
assert payload["text"] == "第一行\n第二行　陈戟 13912345678"
print(
    json.dumps(
        {
            "matched": True,
            "person_name": payload["person_name"],
            "mobile": "13912345678",
            "evidence_quote": payload["text"].splitlines()[1],
            "confidence": 1,
            "reason": "utf8_roundtrip",
        },
        ensure_ascii=False,
    )
)
