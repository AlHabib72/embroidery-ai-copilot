"""
Action executor.

Every action goes through: policy check (may this run unattended, or does
it need a human?) -> adapter call -> logged result. Never marks an action
SUCCESS unless the adapter actually reported and verified it (project brief
section 51: "No fake success").

Emergency stop (section 27): once `stop()` is called, `run()` refuses every
further request until `reset()` is called, and does not attempt to
"gracefully finish" whatever was queued.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..domain import ActionStatus
from .schema import ActionRequest, ActionResult, AutomationPolicy
from .wilcom_adapter import WilcomAdapter


@dataclass
class LoggedAction:
    timestamp: str
    result: ActionResult

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "result": self.result.to_dict()}


class EmergencyStopActive(Exception):
    """Raised by run() when the executor is stopped — never silently
    swallowed; the caller must call reset() to resume."""


class ActionExecutor:
    def __init__(self, adapter: WilcomAdapter, policy: AutomationPolicy | None = None):
        self.adapter = adapter
        self.policy = policy or AutomationPolicy()
        self.log: list[LoggedAction] = []
        self._stopped = False

    def stop(self) -> None:
        """Emergency stop: no further queued action will run until reset()."""
        self._stopped = True

    def reset(self) -> None:
        self._stopped = False

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(self, request: ActionRequest, approved: bool = False) -> ActionResult:
        """Execute one action.

        `approved` represents an explicit human approval already obtained
        for THIS request (e.g. from a UI's [Approve] button). If the policy
        would not allow the action unattended and `approved` is False, the
        action is not sent to the adapter at all — it comes back as
        PLANNED, not SUCCESS or FAILED, so the caller can distinguish
        "didn't run because it needs your OK" from an actual failure.
        """
        if self._stopped:
            raise EmergencyStopActive(
                "ActionExecutor is stopped (emergency stop active). Call reset() to resume."
            )

        if not approved and not self.policy.may_run_unattended(request):
            result = ActionResult(
                request,
                ActionStatus.PLANNED.value,
                "Requires human approval before execution (policy/confirmation not satisfied).",
                screen_state_verified=False,
            )
            self._log(result)
            return result

        try:
            result = self.adapter.execute_action(request)
        except Exception as exc:  # noqa: BLE001 - executor must never crash the caller
            result = ActionResult(
                request, ActionStatus.FAILED.value, f"adapter raised: {exc}", screen_state_verified=False
            )
            self._log(result)
            self.stop()  # section 52: detect failure -> stop further actions
            return result

        self._log(result)
        if result.status == ActionStatus.FAILED.value:
            self.stop()  # never keep running queued actions after a failure
        return result

    def _log(self, result: ActionResult) -> None:
        self.log.append(LoggedAction(datetime.now(timezone.utc).isoformat(timespec="seconds"), result))

    def log_as_dicts(self) -> list[dict]:
        return [entry.to_dict() for entry in self.log]
