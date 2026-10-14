"""
Standard action schema (project brief section 46) shared by every
automation adapter, plus the confirmation-level policy (section 3) that
decides whether an action may run without asking a human first.

NEVER let a caller skip this schema to send "raw" commands to an adapter —
see wilcom_adapter.py and mock_adapter.py, both of which only accept
ActionRequest objects, not arbitrary dicts or strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import ActionStatus, RiskLevel


@dataclass
class ActionRequest:
    action: str  # e.g. "select_tool", "create_object", "set_property"
    target: str | None = None
    params: dict = field(default_factory=dict)
    confidence: float = 0.0  # 0.0-1.0
    risk: str = RiskLevel.MEDIUM.value
    requires_confirmation: bool = True

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "target": self.target,
            "params": self.params,
            "confidence": round(self.confidence, 3),
            "risk": self.risk,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass
class ActionResult:
    request: ActionRequest
    status: str  # ActionStatus value
    detail: str = ""
    screen_state_verified: bool = False

    def to_dict(self) -> dict:
        return {
            "request": self.request.to_dict(),
            "status": self.status,
            "detail": self.detail,
            "screen_state_verified": self.screen_state_verified,
        }


class AutomationPolicy:
    """Decides whether an ActionRequest may run without asking a human.

    NEVER perform an irreversible or high-impact action without
    confirmation unless the caller explicitly enabled an automation policy
    that allows it (section 3). The default policy here requires
    confirmation for everything except SAFE actions — it does not get more
    permissive on its own.
    """

    def __init__(self, allow_unattended_up_to: str = RiskLevel.SAFE.value):
        order = [r.value for r in RiskLevel]
        if allow_unattended_up_to not in order:
            raise ValueError(f"Unknown risk level: {allow_unattended_up_to!r}")
        self._ceiling_index = order.index(allow_unattended_up_to)
        self._order = order

    def may_run_unattended(self, request: ActionRequest) -> bool:
        if request.requires_confirmation:
            return False
        if request.risk not in self._order:
            return False  # unknown risk label: never unattended
        return self._order.index(request.risk) <= self._ceiling_index
