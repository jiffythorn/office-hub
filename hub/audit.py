"""
hub/audit.py - append-only audit trail for the Office Hub.

Why: officers need to answer "who changed what, and when?" - both for their
own peace of mind and to trust the Officer AI. Every privileged action gets
one line of JSON in data/audit.jsonl: timestamp, actor, action, outcome,
details. The file is append-only; nothing in the hub ever rewrites it.

The audit trail is itself included in every backup, so even a deleted file
can be recovered from yesterday's snapshot.
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT_FILE = ROOT / "data" / "audit.jsonl"
MAX_BYTES = 2 * 1024 * 1024          # rotate the JSONL at 2 MB
KEEP_ROTATIONS = 4                   # keep data/audit.jsonl.{1..4}


def record(action: str, actor: str = "system", outcome: str = "ok",
           **details) -> dict:
    """Append one audit event. Never raises - auditing must not break the hub."""
    try:
        AUDIT_FILE.parent.mkdir(exist_ok=True)
        # Rotate when the file grows past the cap: audit.jsonl -> .1 -> ... .4
        try:
            if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > MAX_BYTES:
                for i in range(KEEP_ROTATIONS, 0, -1):
                    src = AUDIT_FILE.with_suffix(f".jsonl.{i}")
                    dst = AUDIT_FILE.with_suffix(f".jsonl.{i + 1}")
                    if src.exists():
                        src.replace(dst)
                AUDIT_FILE.replace(AUDIT_FILE.with_suffix(".jsonl.1"))
        except OSError:
            pass
        event = {
            "ts": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "actor": actor,
            "action": action,
            "outcome": outcome,
        }
        if details:
            event["details"] = details
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event
    except Exception:
        return {}


def recent(limit: int = 30) -> list[dict]:
    """The newest audit events, newest first (for /status and /admin)."""
    try:
        if not AUDIT_FILE.exists():
            return []
        lines = AUDIT_FILE.read_text(encoding="utf-8",
                                     errors="replace").splitlines()
        out = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []
