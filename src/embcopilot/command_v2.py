"""Multi-step command parser (v0.6) - English + Hinglish (Roman Hindi).  Parses ONLY; never executes anything.

"Delete the background, make the outer ring 3mm satin, ..."  ->  ordered, schema-validated steps.
"Outer ring ko 3.5mm Input C satin karo"                      ->  one modify_object step.
Targets are validated against the roles that were actually detected; a target that does not exist is reported
(`not_found`), never invented.  There is no code generation and no shell: output is plain data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TARGETS = [
    ("background", r"\b(background|bg|backdrop|peeche ki|piche ki)\b"),
    ("outer_ring", r"\b(outer ring|outer border|outer circle|bahar(i|ki)? ring|bahari border|border|outer)\b"),
    ("inner_ring", r"\b(inner ring|inner circle|andar(uni)? ring|andar ki ring)\b"),
    ("circular_text", r"\b(circular text|ring text|badge text|text|lettering|letters|akshar|likhawat)\b"),
    ("center_logo", r"\b(central logo|center logo|centre logo|monogram|emblem|logo|center|centre)\b"),
]
STITCHES = [
    ("input_c", r"\binput\s*-?\s*c\b|\bcolumn\s*c\b"),
    ("triple_run", r"\btriple\s*run\b"),
    ("run", r"\brun\b"),
    ("tatami", r"\btatami\b|\bfill\b"),
    ("puff", r"\bpuff\b|\b3d\b"),
    ("applique", r"\bappliqu[eé]\b"),
    ("satin", r"\bsatin\b"),
    ("cross_stitch", r"\bcross[ -]?stitch\b"),
]
FABRICS = ("denim", "towel", "t_shirt", "t-shirt", "tshirt", "jersey", "pique", "twill", "canvas", "fleece", "cap", "patch", "stretch", "leather", "woven")
COLORS = {"gold": "#C9A227", "black": "#000000", "white": "#FFFFFF", "red": "#C8102E", "blue": "#1F4E9E", "green": "#2E7D32",
          "silver": "#C0C0C0", "kala": "#000000", "safed": "#FFFFFF", "lal": "#C8102E", "neela": "#1F4E9E", "hara": "#2E7D32", "sunehri": "#C9A227"}
DELETE = r"\b(delete|remove|hata|hatao|hata do|hatado|nikal|nikaalo|mita|mitao|exclude)\b"
OPTIMIZE_JUMPS = r"\b(optimi[sz]e|reduce|kam karo|kam kar|theek karo|fix)\b.*\b(jumps?|trims?|travel|connectors?)\b|\b(jumps?|trims?)\b.*\b(optimi[sz]e|kam)\b"
SPLIT = re.compile(r"\s*(?:,|;|\band\b|\baur\b|\bphir\b|\bthen\b|\bfir\b|\bwith\b|\bsaath\b)\s*", re.I)
MM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm|millimet\w*)", re.I)


@dataclass
class Step:
    n: int
    intent: str                  # modify_object | delete_object | apply_fabric | recolor | optimize | regenerate | qc
    target: str | None = None
    changes: dict = field(default_factory=dict)
    risk: str = "low"
    text: str = ""
    found: bool | None = None    # None = not checked
    note: str = ""

    def to_dict(self):
        d = {"intent": self.intent, "target": self.target, "changes": self.changes, "risk": self.risk, "source_text": self.text}
        if self.found is not None:
            d["target_found"] = self.found
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class CommandPlan:
    raw: str
    steps: list[Step]
    needs_approval: bool
    unrecognised: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"raw": self.raw, "steps": [{"n": s.n, **s.to_dict()} for s in self.steps], "needs_approval": self.needs_approval,
                "unrecognised": self.unrecognised, "executes_code": False,
                "auto_steps_after": ["replan underlay", "replan compensation", "optimize connectors", "QC"]}


def _find(patterns, text):
    for name, pat in patterns:
        if re.search(pat, text, re.I):
            return name
    return None


def parse_multi_command(text: str, detected_roles: list[str] | None = None) -> CommandPlan:
    clauses = [c.strip() for c in SPLIT.split(text) if c and c.strip()]
    steps: list[Step] = []
    unknown: list[str] = []
    last_target: str | None = None
    for c in clauses:
        low = c.lower()
        tgt = _find(TARGETS, low)
        # "Outer ring ko ..." / bare clause inherits the previous target
        eff_tgt = tgt or (last_target if re.search(r"\b(mm|satin|input|tatami|run|puff|applique)\b", low) else None)
        made = False
        if re.search(DELETE, low) and tgt:
            steps.append(Step(len(steps) + 1, "delete_object", tgt, {}, "medium" if tgt != "background" else "low", c)); made = True
        elif re.search(OPTIMIZE_JUMPS, low):
            steps.append(Step(len(steps) + 1, "optimize", "design", {"what": "connectors_jumps_trims"}, "low", c)); made = True
        else:
            ch: dict = {}
            st = _find(STITCHES, low)
            if st:
                ch["stitch_type"] = st
            mm = MM.search(low)
            if mm:
                ch["width_mm"] = float(mm.group(1))
            col = next((v for k, v in COLORS.items() if re.search(rf"\b{k}\b", low)), None)
            fab = next((f for f in FABRICS if re.search(rf"\b{re.escape(f)}\b", low)), None)
            if fab and re.search(r"preset|profile|fabric|par|pe|use|lagao|laga", low):
                steps.append(Step(len(steps) + 1, "apply_fabric", "design", {"fabric": fab.replace("-", "_").upper().replace("TSHIRT", "T_SHIRT")}, "medium", c)); made = True
                ch = {k: v for k, v in ch.items()} if False else {}
            if col and eff_tgt:
                steps.append(Step(len(steps) + 1, "recolor", eff_tgt, {"color_hex": col}, "low", c)); made = True
            if ch and eff_tgt:
                if ch.get("stitch_type") in ("puff", "applique"):
                    pass
                steps.append(Step(len(steps) + 1, "modify_object", eff_tgt, ch, "medium" if ch.get("stitch_type") in ("puff", "applique", "tatami") else "low", c)); made = True
        if tgt:
            last_target = tgt
        if not made:
            unknown.append(c)
    for s in steps:
        if detected_roles is not None and s.target not in (None, "design"):
            s.found = s.target in detected_roles
            if not s.found:
                s.note = f"NOT_FOUND: '{s.target}' was not detected in this design; nothing will be invented"
    needs = any(s.risk != "low" for s in steps) or len(steps) > 2
    return CommandPlan(text, steps, needs, unknown)
