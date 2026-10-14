"""Risk assessment + approval modes.  Policy is decided HERE, never by the model."""
from __future__ import annotations

from dataclasses import dataclass, field

RISK = {"set_density": "low", "set_pull_comp": "low", "set_underlay": "low", "set_angle": "low", "auto_split": "low", "min_stitch": "low",
        "optimize": "low", "qc": "low", "set_color": "low", "protect": "low", "emb_info": "low",
        "set_stitch_type": "medium", "set_width": "medium", "set_fabric": "medium", "export": "medium", "delete_object": "high"}
ORDER = {"low": 0, "medium": 1, "high": 2}
MODES = ("SUGGEST", "CONFIRM", "AUTO", "MANUAL")


@dataclass
class Decision:
    risk: str
    requires_approval: bool
    auto_apply: bool
    blocked: bool
    reason: str
    step_risks: list = field(default_factory=list)

    def to_dict(self):
        return self.__dict__.copy()


def assess(steps, mode: str = "CONFIRM") -> Decision:
    mode = mode.upper()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    risks = [RISK.get(s.op, "high") for s in steps]
    top = max(risks, key=lambda r: ORDER[r], default="low")
    edits = [s for s in steps if s.op not in ("qc", "emb_info", "protect")]
    if mode == "MANUAL" and edits:
        return Decision(top, True, False, True, "MANUAL mode: the AI may not change geometry or parameters; use the inspector controls", risks)
    if mode == "AUTO" and top == "low" and edits:
        return Decision(top, False, True, False, "AUTO mode: all steps are low risk", risks)
    if not edits:
        return Decision(top, False, True, False, "read-only request", risks)
    why = {"SUGGEST": "SUGGEST mode: AI proposes, you approve", "CONFIRM": "CONFIRM mode: approval required",
           "AUTO": f"AUTO mode: {top}-risk step needs approval"}[mode]
    return Decision(top, True, False, False, why, risks)
