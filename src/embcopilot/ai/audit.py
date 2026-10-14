"""Append-only audit log (JSONL).  Every record is passed through the secret redactor."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from .providers import redact_obj


class AuditLog:
    def __init__(self, path: str | None = None):
        self.path = path
        self.records: list[dict] = []
        self._lock = threading.Lock()
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def record(self, **kw) -> dict:
        rec = redact_obj({"command_id": kw.pop("command_id", None) or uuid.uuid4().hex[:12],
                          "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw})
        with self._lock:
            self.records.append(rec)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
        return rec
