"""QC v2 (v0.6): category-tagged rules, release gates and error-level export blocking.

Complements stitch.validate_stitch_plan (15 geometry checks).  Everything is measured from the SAME
StitchPlan that is exported; nothing here guarantees sew-out quality.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .machine import MachineProfile, get_machine
from .stitchclean import classify_short_stitches

CATEGORIES = ("Geometry QC", "Vision QC", "Object QC", "Stitch QC", "Pathing QC", "Fabric QC", "Thread QC", "Export QC", "Reference QC")
RULE_CATEGORY = {
    "BACKGROUND_AS_STITCH": "Vision QC", "CENTER_CROSSING_TRAVEL": "Pathing QC", "EXCESSIVE_JUMP": "Pathing QC",
    "UNNECESSARY_TRIM": "Pathing QC", "SHORT_STITCH_CLUSTER": "Stitch QC", "ZERO_LENGTH_STITCH": "Stitch QC",
    "EXCESSIVE_DENSITY": "Stitch QC", "MISSING_UNDERLAY": "Object QC", "INVALID_HOLE": "Geometry QC",
    "SELF_INTERSECTION": "Geometry QC", "SATIN_TOO_WIDE": "Fabric QC", "UNSAFE_TURN": "Stitch QC",
    "BAD_ENTRY_EXIT": "Object QC", "BAD_SEQUENCE": "Pathing QC", "THREAD_COLOR_UNMAPPED": "Thread QC",
    "SCALE_UNKNOWN": "Geometry QC", "OUT_OF_HOOP": "Geometry QC", "FORMAT_VALIDATION_FAILED": "Export QC",
}
FILLISH = {"satin", "tatami", "complex_fill", "applique", "puff", "text", "gradient"}


@dataclass
class QCIssue:
    code: str
    severity: str            # ERROR | WARNING | INFO
    category: str
    message: str
    object_id: str | None = None
    count: int = 1
    regions: list = field(default_factory=list)   # [(x, y)] points for the GUI overlay

    def to_dict(self):
        return {"code": self.code, "severity": self.severity, "category": self.category, "message": self.message,
                "object_id": self.object_id, "count": self.count, "regions": [list(map(lambda v: round(v, 2), r)) for r in self.regions[:20]]}


@dataclass
class QCReport:
    issues: list[QCIssue]
    metrics: dict
    machine: str

    def by_severity(self, sev):
        return [i for i in self.issues if i.severity == sev]

    @property
    def errors(self):
        return self.by_severity("ERROR")

    def by_category(self) -> dict:
        out = {c: {"errors": 0, "warnings": 0} for c in CATEGORIES}
        for i in self.issues:
            slot = out.setdefault(i.category, {"errors": 0, "warnings": 0})
            slot["errors" if i.severity == "ERROR" else "warnings"] += 1
        return out

    def to_dict(self):
        return {"machine": self.machine, "issues": [i.to_dict() for i in self.issues], "metrics": self.metrics,
                "categories": self.by_category(), "error_count": len(self.errors),
                "wording": "QC validated; requires sew-out validation (no production guarantee)"}


def plan_metrics(plan, zones=None) -> dict:
    """Regression metrics (section 116): same numbers for before/after comparison."""
    from .negative_space import find_protected_crossings
    st = plan.stitches
    s = plan.stats()
    xs = [p.x for p in st if p.command == "STITCH"]; ys = [p.y for p in st if p.command == "STITCH"]
    short = sum(1 for _, c in classify_short_stitches(st) if c not in ("intentional", "corner", "curve_control"))
    # a detour is several JUMP records but ONE travel move: report both so rerouting is not mis-read as "more jumps"
    moves, prev_j = 0, False
    for p_ in st:
        isj = p_.command == "JUMP"
        moves += 1 if (isj and not prev_j) else 0
        prev_j = isj
    return {"object_count": len(plan.order), "stitch_count": s["stitch_count"], "jump_count": s["jump_count"],
            "jump_moves": moves, "trim_count": s["trim_count"], "color_count": s["color_count"],
            "bbox_mm": [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)] if xs else None,
            "center_crossing_travel": len(find_protected_crossings(plan, zones)) if zones else 0,
            "short_stitch_count": short, "estimated_minutes": s["estimated_minutes"]}


def run_qc(plan, objects=None, zones=None, machine: MachineProfile | str | None = None, scale_known: bool = True,
           max_jumps_per_1000: float = 12.0, density_cell_mm: float = 1.0, max_stitches_per_cell: int = 14) -> QCReport:
    m = get_machine(machine)
    objects = objects or []
    byid = {o.object_id: o for o in objects}
    issues: list[QCIssue] = []
    st = plan.stitches

    def add(code, sev, msg, oid=None, count=1, regions=None):
        issues.append(QCIssue(code, sev, RULE_CATEGORY.get(code, "Stitch QC"), msg, oid, count, regions or []))

    if not scale_known:
        add("SCALE_UNKNOWN", "ERROR", "design scale (mm per pixel) is unknown; sizes and densities are meaningless")
    # background must never be stitched
    bg = [o.object_id for o in objects if getattr(o, "category", "") == "background"]
    hit = {s.object_id for s in st if s.command == "STITCH" and s.object_id in bg}
    for oid in sorted(hit):
        add("BACKGROUND_AS_STITCH", "ERROR", f"background object {oid} contains stitches (background is not an embroidery object unless approved)", oid)
    # zero length + short clusters
    zero = [(st[i].x, st[i].y) for i in range(1, len(st))
            if st[i].command == st[i - 1].command == "STITCH" and st[i].object_id == st[i - 1].object_id
            and math.dist((st[i].x, st[i].y), (st[i - 1].x, st[i - 1].y)) < 1e-6]
    if zero:
        add("ZERO_LENGTH_STITCH", "WARNING", f"{len(zero)} zero-length stitch(es)", None, len(zero), zero)
    cl = [(st[i].x, st[i].y) for i, c in classify_short_stitches(st, m.min_safe_stitch_mm) if c in ("accidental_cluster",)]
    if cl:
        add("SHORT_STITCH_CLUSTER", "WARNING", f"{len(cl)} accidental short stitches in clusters (thread-break/bulk risk)", None, len(cl), cl)
    # jumps and trims
    s = plan.stats()
    n_st = max(s["stitch_count"], 1)
    jumps = [(st[i].x, st[i].y) for i in range(len(st)) if st[i].command == "JUMP"]
    from .negative_space import _preceded_by_trim
    # a trimmed jump drags no thread, so only UNTRIMMED long travel is a defect
    long_jumps = [(st[i].x, st[i].y) for i in range(1, len(st)) if st[i].command == "JUMP" and st[i - 1].command != "JUMP"
                  and not _preceded_by_trim(st, i) and math.dist((st[i].x, st[i].y), (st[i - 1].x, st[i - 1].y)) > m.max_jump_mm]
    if long_jumps:
        add("EXCESSIVE_JUMP", "ERROR", f"{len(long_jumps)} untrimmed jump(s) longer than {m.max_jump_mm:g} mm (thread dragged across the design)", None, len(long_jumps), long_jumps)
    elif len(jumps) * 1000.0 / n_st > max_jumps_per_1000 and len(jumps) > 5:
        add("EXCESSIVE_JUMP", "WARNING", f"{len(jumps)} jumps for {n_st} stitches ({len(jumps)*1000.0/n_st:.1f}/1000, limit {max_jumps_per_1000:g})", None, len(jumps), jumps)
    unn = []
    for i, p in enumerate(st):
        if p.command == "TRIM" and not p.part.endswith("end_trim"):
            nxt = next((q for q in st[i + 1:] if q.command in ("JUMP", "STITCH")), None)
            if nxt is not None and math.dist((p.x, p.y), (nxt.x, nxt.y)) < m.visible_trim_min_mm * 0.5:
                unn.append((p.x, p.y))
    if unn:
        add("UNNECESSARY_TRIM", "WARNING", f"{len(unn)} trim(s) before a very short travel", None, len(unn), unn)
    # protected crossings
    if zones:
        from .negative_space import find_protected_crossings
        cr = find_protected_crossings(plan, zones)
        if cr:
            add("CENTER_CROSSING_TRAVEL", "ERROR", f"{len(cr)} travel segment(s) cross a protected region", None, len(cr), [c.start for c in cr])
    # underlay per fill-ish object
    parts_by_obj: dict[str, set] = {}
    for p in st:
        if p.command == "STITCH":
            parts_by_obj.setdefault(p.object_id, set()).add(p.part.split(":")[0])
    for oid, o in byid.items():
        if oid not in parts_by_obj:
            continue
        stype = str(getattr(o, "stitch_type", ""))
        ws = getattr(o, "width_stats_mm", None) or {}
        # measured stroke width (distance transform) when available; bbox min is wrong for rings/curved bands
        w = float(ws.get("median") or ws.get("p95") or 0.0) or (min(getattr(o, "width_mm", 0.0), getattr(o, "height_mm", 0.0))
                                                                  if not getattr(o, "holes_mm", None) and len(getattr(o, "geometry", [])) < 40 else 0.0)
        if w and stype in FILLISH and "underlay" not in parts_by_obj[oid] and w >= 3.0 and getattr(o, "underlay", "none") in ("none", None):
            add("MISSING_UNDERLAY", "WARNING", f"{oid} ({stype}, {w:.1f} mm) has no underlay", oid)
        if w and stype == "satin" and w > 12.0:
            add("SATIN_TOO_WIDE", "WARNING", f"{oid}: satin {w:.1f} mm exceeds safe width; split or convert", oid)
        for h in getattr(o, "holes_mm", []) or []:
            if len(h) < 3:
                add("INVALID_HOLE", "ERROR", f"{oid} has a degenerate hole", oid)
    # density hotspots (top stitches only)
    grid: dict = {}
    for p in st:
        if p.command == "STITCH" and not p.part.startswith(("underlay", "connector")):
            grid[(int(p.x // density_cell_mm), int(p.y // density_cell_mm))] = grid.get((int(p.x // density_cell_mm), int(p.y // density_cell_mm)), 0) + 1
    hot = [(k[0] * density_cell_mm, k[1] * density_cell_mm) for k, v in grid.items() if v > max_stitches_per_cell]
    if hot:
        add("EXCESSIVE_DENSITY", "WARNING", f"{len(hot)} cell(s) exceed {max_stitches_per_cell} top stitches per {density_cell_mm:g} mm cell (heuristic)", None, len(hot), hot)
    # hoop
    xs = [p.x for p in st if p.command == "STITCH"]; ys = [p.y for p in st if p.command == "STITCH"]
    if xs and m.hoop:
        r = m.hoop.check(min(xs), min(ys), max(xs), max(ys))
        if not r["fits"]:
            add("OUT_OF_HOOP", "ERROR", f"design {r['design_w_mm']}x{r['design_h_mm']} mm exceeds usable {r['usable_w_mm']}x{r['usable_h_mm']} mm")
    if any(p.command == "STITCH" and not p.color for p in st):
        add("THREAD_COLOR_UNMAPPED", "WARNING", "some stitches have no colour/thread assigned")
    return QCReport(issues, plan_metrics(plan, zones), m.machine_name)


# --------------------------------------------------------------------------- gates + export blocking
GATES = [("Gate 1 - Geometry", ("Geometry QC",)), ("Gate 2 - Object", ("Object QC", "Vision QC")),
         ("Gate 3 - Stitch", ("Stitch QC",)), ("Gate 4 - Pathing", ("Pathing QC",)),
         ("Gate 5 - QC", None)]


def release_gates(report: QCReport) -> list[dict]:
    out = []
    for name, cats in GATES:
        errs = [i for i in report.errors if cats is None or i.category in cats]
        out.append({"gate": name, "passed": not errs, "errors": [i.code for i in errs]})
    return out


@dataclass
class ExportDecision:
    allowed: bool
    reason: str
    override_logged: bool = False

    def to_dict(self):
        return self.__dict__.copy()


class OverrideLog:
    """Expert-mode override of an export block is always logged (who/why/when/what)."""
    def __init__(self):
        self.entries: list[dict] = []

    def record(self, user: str, reason: str, codes: list[str]):
        self.entries.append({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": user, "reason": reason, "blocked_codes": codes})


def export_gate(report: QCReport, expert_override: dict | None = None, log: OverrideLog | None = None) -> ExportDecision:
    """QC ERROR blocks machine export unless the user explicitly chose an expert override (logged, reason required)."""
    errs = report.errors
    if not errs:
        return ExportDecision(True, "no QC errors")
    codes = sorted({e.code for e in errs})
    if expert_override and expert_override.get("confirm") is True and str(expert_override.get("reason", "")).strip():
        if log is not None:
            log.record(expert_override.get("user", "unknown"), expert_override["reason"], codes)
        return ExportDecision(True, f"EXPERT_OVERRIDE for {codes}", True)
    return ExportDecision(False, f"EXPORT_BLOCKED_BY_QC_ERRORS: {codes}")
