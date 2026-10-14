"""
Audit log: every AI decision should be explainable (project brief section
50) and every log stays local by default (section 39/78 privacy rules —
never upload silently).

Format is JSON Lines (one JSON object per line) so it's append-only,
diff-friendly, and trivially parseable without a database.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

from .domain import Decision


class AuditLog:
    def __init__(self, path: str = "logs/copilot.log"):
        self.path = path

    def _write(self, record: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        record = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=self._default) + "\n")

    @staticmethod
    def _default(obj):
        if is_dataclass(obj):
            return asdict(obj)
        return str(obj)

    def log_decision(self, decision: Decision, user_response: str | None = None) -> None:
        self._write(
            {
                "type": "decision",
                "decision_id": decision.decision_id,
                "subject_object_id": decision.subject_object_id,
                "action": decision.action,
                "reason": decision.reason,
                "confidence_pct": decision.confidence.pct,
                "risk": decision.risk.value if hasattr(decision.risk, "value") else decision.risk,
                "approval_state": decision.approval_state,
                "user_response": user_response,
            }
        )

    def log_action_result(self, result) -> None:
        self._write({"type": "action_result", **result.to_dict()})

    def log_event(self, message: str, **fields) -> None:
        self._write({"type": "event", "message": message, **fields})

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
