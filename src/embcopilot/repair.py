"""
QC rules + auto-repair loop (spec 43).

    generate -> QC -> issue -> diagnosis -> repair proposal -> SIMULATE on a copy -> re-QC -> before/after -> human approval

Nothing here mutates a design in place. `propose_repairs` only simulates each candidate on a deep copy and reports
measured before/after numbers; `apply_repairs` returns a NEW plan and only for repairs the caller names (i.e. approved).
A repair that makes a metric worse or introduces a validation ERROR is reported as REJECTED_BY_QC and cannot be applied.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from .connector import ConnectorCostWeights
from .stitch import (COLOR_CHANGE, JUMP, STITCH, TRIM, Stitch, StitchPlan, build_stitch_plan, validate_stitch_plan)

SEVERITIES = ("INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class QCTargets:
    max_jumps: int = 40
    max_trims: int = 12
    max_color_changes: int = 12
    min_stitch_mm: float = 0.3
    max_stitch_mm: float = 7.5
    max_travel_fraction: float = 0.12   # jump distance / total stitched distance
    min_fill_density_mm: float = 0.25   # rows tighter than this risk thread break
    max_fill_density_mm: float = 0.70   # rows sparser than this show fabric


@dataclass
class QCIssue:
    code: str
    severity: str
    message: str
    diagnosis: str = ""
    object_id: str | None = None
    metric: float | None = None
    target: float | None = None

    def to_dict(self):
        return self.__dict__.copy()


def metrics(plan: StitchPlan) -> dict:
    st = plan.stats()
    tot = travel = 0.0
    prev = None
    short = long_ = 0
    for s in plan.stitches:
        if s.command == TRIM:
            continue
        if prev is not None:
            d = math.dist((prev.x, prev.y), (s.x, s.y))
            if s.command == STITCH and prev.command == STITCH:
                tot += d
                short += d < 0.3
                long_ += (d > 7.5 and "satin" not in s.part)
            elif s.command == JUMP:
                travel += d
        prev = s
    return {"stitches": st["stitch_count"], "jumps": st["jump_count"], "trims": st["trim_count"], "color_changes": st["color_changes"],
            "stops": st.get("stops", 0), "short_stitches": int(short), "long_stitches": int(long_), "travel_mm": round(travel, 1),
            "stitched_mm": round(tot, 1), "travel_fraction": round(travel / max(tot + travel, 1e-9), 4),
            "estimated_minutes": st["estimated_minutes"]}


def run_qc(plan: StitchPlan, objects, validation, targets: QCTargets | None = None, technical=None) -> list[QCIssue]:
    t = targets or QCTargets()
    m = metrics(plan)
    out: list[QCIssue] = []
    for f in validation.findings:
        if f.severity in ("ERROR", "WARNING"):
            out.append(QCIssue(f.code, "ERROR" if f.severity == "ERROR" else "WARNING", f.message, "", f.object_id))
    if m["jumps"] > t.max_jumps:
        out.append(QCIssue("EXCESSIVE_JUMPS", "WARNING", f"{m['jumps']} jumps (target <= {t.max_jumps})",
                           "jumps come from fill runs split by holes/concavities and from object-to-object moves", None, m["jumps"], t.max_jumps))
    if m["trims"] > t.max_trims:
        out.append(QCIssue("EXCESSIVE_TRIMS", "WARNING", f"{m['trims']} trims (target <= {t.max_trims})", "each long jump is trimmed; connector weights may favour trims", None, m["trims"], t.max_trims))
    if m["color_changes"] > t.max_color_changes:
        out.append(QCIssue("EXCESSIVE_COLOR_CHANGES", "WARNING", f"{m['color_changes']} colour changes", "sequence could group colours when dependencies allow", None, m["color_changes"], t.max_color_changes))
    if m["travel_fraction"] > t.max_travel_fraction:
        out.append(QCIssue("UNNECESSARY_TRAVEL", "WARNING", f"{m['travel_fraction']*100:.1f}% of movement is jump travel", "long jumps between distant objects", None, m["travel_fraction"], t.max_travel_fraction))
    if m["short_stitches"]:
        out.append(QCIssue("TOO_SHORT_STITCHES", "WARNING", f"{m['short_stitches']} stitches shorter than {t.min_stitch_mm}mm", "curve resampling or fill row ends create near-duplicate points", None, m["short_stitches"], 0))
    if m["long_stitches"]:
        out.append(QCIssue("TOO_LONG_STITCHES", "WARNING", f"{m['long_stitches']} stitches longer than {t.max_stitch_mm}mm", "snag risk", None, m["long_stitches"], 0))
    if technical is not None:
        for p in technical.plans:
            if p.stitch_type in ("tatami", "complex_fill", "puff"):
                if p.density_mm < t.min_fill_density_mm:
                    out.append(QCIssue("DENSITY_TOO_HIGH", "WARNING", f"{p.object_name}: rows {p.density_mm}mm apart", "risk of thread break / stiff fill", p.object_id, p.density_mm, t.min_fill_density_mm))
                if p.density_mm > t.max_fill_density_mm:
                    out.append(QCIssue("DENSITY_TOO_SPARSE", "WARNING", f"{p.object_name}: rows {p.density_mm}mm apart", "fabric will show through", p.object_id, p.density_mm, t.max_fill_density_mm))
            for w in p.warnings:
                out.append(QCIssue("FABRIC_WARNING", "INFO", f"{p.object_name}: {w}", "", p.object_id))
    status = plan.object_status
    for oid, st in status.items():
        for w in st.get("warnings", []):
            if w.startswith("FALLBACK"):
                out.append(QCIssue("STRATEGY_FALLBACK", "INFO", w, "requested strategy was invalid for this geometry", oid))
    return out


# ------------------------------------------------------------------ repairs
@dataclass
class RepairProposal:
    name: str
    fixes: list
    description: str
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    status: str = "PROPOSED"  # PROPOSED | REJECTED_BY_QC | NO_EFFECT
    delta: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self):
        d = self.__dict__.copy()
        return d


def _cleanup_short(plan: StitchPlan, min_mm=0.3) -> StitchPlan:
    p = copy.deepcopy(plan)
    out: list[Stitch] = []
    for s in p.stitches:
        if (s.command == STITCH and out and out[-1].command == STITCH and out[-1].object_id == s.object_id
                and ((out[-1].part == s.part and math.dist((out[-1].x, out[-1].y), (s.x, s.y)) < min_mm)
                     or math.dist((out[-1].x, out[-1].y), (s.x, s.y)) < 1e-6)):
            continue  # drop a near-duplicate inside a part, or an exact duplicate across parts (same object)
        out.append(s)
    p.stitches = out
    return p


def _split_long(plan: StitchPlan, max_mm=7.0) -> StitchPlan:
    p = copy.deepcopy(plan)
    out: list[Stitch] = []
    for s in p.stitches:
        if s.command == STITCH and out and out[-1].command == STITCH:
            d = math.dist((out[-1].x, out[-1].y), (s.x, s.y))
            if d > max_mm and "satin" not in s.part:
                n = math.ceil(d / max_mm)
                a = out[-1]
                for k in range(1, n):
                    t = k / n
                    out.append(Stitch(a.x + (s.x - a.x) * t, a.y + (s.y - a.y) * t, STITCH, s.color, s.object_id, s.layer, s.part))
        out.append(s)
    p.stitches = out
    return p


def _rebuild_with_weights(ctx, w: ConnectorCostWeights) -> StitchPlan:
    plan, _ = build_stitch_plan(ctx["objects"], ctx["technical"], ctx["canvas"], ctx["deps"], ctx["profile"], connector_weights=w)
    return plan


def _candidates(ctx, plan):
    from .profiles import get_profile
    base = ConnectorCostWeights.from_profile(ctx["profile"])
    fewer_trims = ConnectorCostWeights(base.jump_distance_weight, base.trim_weight * 3.0, base.visible_travel_weight, base.direction_penalty_weight)
    fewer_jumps = ConnectorCostWeights(base.jump_distance_weight * 2.5, base.trim_weight * 0.7, base.visible_travel_weight, base.direction_penalty_weight)
    return [
        ("cleanup_short_stitches", ["TOO_SHORT_STITCHES"], "remove near-duplicate points inside a part", lambda: _cleanup_short(plan)),
        ("split_long_stitches", ["TOO_LONG_STITCHES"], "subdivide non-satin stitches over 7mm", lambda: _split_long(plan)),
        ("reduce_trims", ["EXCESSIVE_TRIMS"], "re-plan connectors with a higher trim cost (prefer jumps / hidden travel)", lambda: _rebuild_with_weights(ctx, fewer_trims)),
        ("reduce_travel", ["EXCESSIVE_JUMPS", "UNNECESSARY_TRAVEL"], "re-plan connectors with a higher travel cost (prefer hidden routed travel)", lambda: _rebuild_with_weights(ctx, fewer_jumps)),
    ]


def propose_repairs(plan: StitchPlan, issues: list[QCIssue], ctx: dict, objects, targets=None) -> list[RepairProposal]:
    """Simulate each applicable repair on a copy; measure; never apply."""
    codes = {i.code for i in issues}
    before = metrics(plan)
    val0 = validate_stitch_plan(plan, objects, ctx["deps"])
    errs0 = sum(f.severity == "ERROR" for f in val0.findings)
    props = []
    for name, fixes, desc, fn in _candidates(ctx, plan):
        if not (set(fixes) & codes):
            continue
        cand = fn()
        after = metrics(cand)
        val = validate_stitch_plan(cand, objects, ctx["deps"])
        errs = sum(f.severity == "ERROR" for f in val.findings)
        delta = {k: round(after[k] - before[k], 3) for k in ("stitches", "jumps", "trims", "color_changes", "short_stitches", "long_stitches", "travel_mm", "estimated_minutes")}
        p = RepairProposal(name, sorted(set(fixes) & codes), desc, before, after, "PROPOSED", delta)
        improved = any(delta[k] < 0 for k in ("jumps", "trims", "short_stitches", "long_stitches", "travel_mm"))
        worse = errs > errs0 or delta["jumps"] > 0 and name == "reduce_travel" or delta["trims"] > 0 and name == "reduce_trims" and delta["jumps"] > 2 * abs(delta["trims"]) + 10
        if errs > errs0:
            p.status, p.note = "REJECTED_BY_QC", f"would introduce {errs-errs0} new validation error(s)"
        elif not improved:
            p.status, p.note = "NO_EFFECT", "no measured improvement on the targeted metrics"
        elif worse:
            p.status, p.note = "REJECTED_BY_QC", "targeted metric improved but another got clearly worse"
        props.append(p)
    return props


def apply_repairs(plan: StitchPlan, proposals: list[RepairProposal], approved: list[str], ctx: dict) -> tuple[StitchPlan, list[str]]:
    """Apply ONLY approved, non-rejected repairs, in order; returns a new plan + the list actually applied."""
    fns = {n: fn for n, _f, _d, fn in _candidates(ctx, plan)}
    applied, cur = [], plan
    okay = {p.name for p in proposals if p.status == "PROPOSED"}
    for name in approved:
        if name not in okay:
            continue
        ctx2 = ctx
        cand = {n: fn for n, _f, _d, fn in _candidates(ctx2, cur)}[name]()
        cur = cand
        applied.append(name)
    return cur, applied
