"""
Stitch Path Engine (Phase 9): StitchPlan = actual, ordered machine commands
built from real per-object geometry, real connector routing, and a
dependency-respecting sequence. Includes the StitchGeometryValidator and a
DST exporter (DST is a documented machine format; EMB is NOT produced here).

Every number in `stats()` is computed from the generated stitches. Time and
thread-length are ESTIMATES from stated assumptions (see StatsAssumptions),
not measurements of a real machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .connector import ConnectorCostWeights, route_connector, route_hidden_path
from .domain import EmbroideryObject
from .geometry import point_in_region, rasterize
from .profiles import ProductionProfile, get_profile
from .sequence import SeqNode, order_objects
from .strategies import ObjectStitches, StrategyLimits, _join_is_inside, generate_object

STITCH, JUMP, TRIM, COLOR_CHANGE, STOP, END = "STITCH", "JUMP", "TRIM", "COLOR_CHANGE", "STOP", "END"


@dataclass
class Stitch:
    x: float
    y: float
    command: str = STITCH
    color: str = ""
    object_id: str = ""
    layer: int = 0
    part: str = ""  # underlay:* / cover:* / connector:*

    def to_dict(self):
        return {"x": round(self.x, 3), "y": round(self.y, 3), "command": self.command,
                "color": self.color, "object_id": self.object_id, "layer": self.layer, "part": self.part}


@dataclass
class Finding:
    severity: str  # VALID | WARNING | ERROR
    code: str
    message: str
    object_id: str | None = None
    count: int = 1

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class StitchPlan:
    canvas_mm: tuple[float, float]
    stitches: list[Stitch] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    connectors: list[dict] = field(default_factory=list)
    object_status: dict = field(default_factory=dict)  # id -> {"strategy","ok","errors","warnings"}
    skipped: list[str] = field(default_factory=list)
    profile: str = "balanced"

    def to_dict(self, include_stitches: bool = True) -> dict:
        d = {"canvas_mm": self.canvas_mm, "order": self.order, "connectors": self.connectors,
             "object_status": self.object_status, "skipped": self.skipped, "profile": self.profile,
             "stats": self.stats()}
        if include_stitches:
            d["stitches"] = [s.to_dict() for s in self.stitches]
        return d

    # ------------------------------------------------------------------ stats
    def stats(self, spm: float = 700.0, trim_s: float = 6.0, color_change_s: float = 30.0, jump_s: float = 0.4) -> dict:
        n = {STITCH: 0, JUMP: 0, TRIM: 0, COLOR_CHANGE: 0, STOP: 0}
        thread = 0.0
        prev = None
        for s in self.stitches:
            if s.command in n:
                n[s.command] += 1
            if s.command == STITCH and prev is not None and prev.command == STITCH:
                thread += math.dist((prev.x, prev.y), (s.x, s.y))
            prev = s
        colors = len({s.color for s in self.stitches if s.command == STITCH})
        minutes = n[STITCH] / spm + (n[TRIM] * trim_s + (n[COLOR_CHANGE] + n[STOP]) * color_change_s + n[JUMP] * jump_s) / 60.0
        return {"stitch_count": n[STITCH], "jump_count": n[JUMP], "trim_count": n[TRIM],
                "color_changes": n[COLOR_CHANGE], "stops": n[STOP], "color_count": colors,
                "top_thread_length_mm": round(thread, 1),
                "estimated_minutes": round(minutes, 2),
                "ESTIMATE_ASSUMPTIONS": {"spm": spm, "trim_s": trim_s, "color_change_s": color_change_s,
                                         "jump_s": jump_s, "note": "top-thread length only; no bobbin, no satin take-up"}}


def _resample_path(path, step):
    """Points at most `step` apart that ALWAYS include every polyline vertex, so corners are never cut
    (a straight chord between two resampled points on either side of a corner would leave the path)."""
    out = [tuple(path[0])]
    for a, b in zip(path, path[1:]):
        d = math.dist(a, b)
        n = max(1, int(math.ceil(d / step)))
        for k in range(1, n + 1):
            t = k / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _rgb(color_group: str) -> tuple[int, int, int]:
    try:
        r, g, b = (int(v) for v in color_group[4:-1].split(","))
        return r, g, b
    except Exception:
        return 128, 128, 128


# ------------------------------------------------------------------------ build
def build_stitch_plan(objects: list[EmbroideryObject], tech_blueprint, canvas_mm: tuple[float, float],
                      dependencies: dict[str, list[str]] | None = None,
                      profile: ProductionProfile | str | None = None,
                      limits: StrategyLimits | None = None, connector_weights: ConnectorCostWeights | None = None,
                      hidden_routing: bool = True) -> tuple[StitchPlan, dict[str, ObjectStitches]]:
    prof = get_profile(profile)
    lim = limits or StrategyLimits()
    plans = {p.object_id: p for p in tech_blueprint.plans}
    plan = StitchPlan(canvas_mm=canvas_mm, profile=prof.name)
    gen: dict[str, ObjectStitches] = {}
    for o in objects:
        tp = plans.get(o.object_id)
        if tp is None:
            plan.skipped.append(o.object_id)
            plan.object_status[o.object_id] = {"strategy": None, "ok": False, "errors": ["NO_TECHNICAL_PLAN"], "warnings": []}
            continue
        res = generate_object(o, tp, prof, canvas_mm, lim)
        rec = o.params.get("_recommended_stitch")
        if not res.ok and rec and rec != tp.stitch_type:
            # a user override that this geometry cannot support must not silently drop the object
            import copy as _c
            tp2 = _c.copy(tp); tp2.stitch_type = rec
            res2 = generate_object(o, tp2, prof, canvas_mm, lim)
            if res2.ok:
                res2.warnings.append(f"OVERRIDE_FAILED_FELL_BACK: {tp.stitch_type} -> {rec} ({'; '.join(res.errors)})")
                res2.fallback_from = tp.stitch_type
                res = res2
        gen[o.object_id] = res
        plan.object_status[o.object_id] = {"strategy": res.strategy, "ok": res.ok, "errors": res.errors, "warnings": res.warnings}
        if not res.ok:
            plan.skipped.append(o.object_id)  # never fill a failed object with guessed stitches
    good = [o for o in objects if o.object_id in gen and gen[o.object_id].ok]
    byid = {o.object_id: o for o in good}
    nodes = [SeqNode(o.object_id, o.color_group, gen[o.object_id].entry, gen[o.object_id].exit, o.z_order) for o in good]
    deps = {k: [d for d in v if d in byid] for k, v in (dependencies or {}).items() if k in byid}
    plan.order = order_objects(nodes, deps)

    # real region masks for connector routing
    scale = min(6.0, 500.0 / max(canvas_mm))
    size = (int(math.ceil(canvas_mm[0] * scale)) + 2, int(math.ceil(canvas_mm[1] * scale)) + 2)
    masks = {i: rasterize(byid[i].geometry, byid[i].holes_mm, scale, size) > 0 for i in plan.order}
    import cv2 as _cv2
    inner = {i: _cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2) > 0 for i, m in masks.items()}  # keeps edge travel off the boundary
    union = np.zeros((size[1], size[0]), bool)
    for m in masks.values():
        union |= m

    def cover(mask):
        def f(x, y):
            px, py = int(round(x * scale)), int(round(y * scale))
            return 0 <= py < mask.shape[0] and 0 <= px < mask.shape[1] and bool(mask[py, px])
        return f

    weights = connector_weights or ConnectorCostWeights.from_profile(prof)
    later_cache: dict[int, object] = {}

    def later_mask(idx):
        if idx not in later_cache:
            m = np.zeros_like(union)
            for j in plan.order[idx:]:
                m |= masks[j]
            later_cache[idx] = m
        return later_cache[idx]
    cur_color, cur = None, None
    st = plan.stitches
    for idx, oid in enumerate(plan.order):
        o, r = byid[oid], gen[oid]
        col = o.color_group
        entry = r.entry
        if cur is None:
            st.append(Stitch(entry[0], entry[1], JUMP, col, oid, o.layer, "connector:start"))
        else:
            if col != cur_color:
                st.append(Stitch(cur[0], cur[1], TRIM, cur_color, oid, o.layer, "connector:trim"))
                st.append(Stitch(cur[0], cur[1], COLOR_CHANGE, col, oid, o.layer, "connector:color_change"))
                st.append(Stitch(entry[0], entry[1], JUMP, col, oid, o.layer, "connector:jump"))
                plan.connectors.append({"from": plan.order[idx - 1], "to": oid, "strategy": "COLOR_CHANGE_TRIM_JUMP",
                                        "distance_mm": round(math.dist(cur, entry), 2)})
            else:
                routed = None
                if hidden_routing and math.dist(cur, entry) > 3.0:
                    routed = route_hidden_path(cur, entry, later_mask(idx), scale)
                rd = route_connector(cur, entry, cover(masks[oid]), cover(union), weights, routed_path=routed)
                plan.connectors.append({"from": plan.order[idx - 1], "to": oid, "strategy": rd.strategy, "cost": rd.cost,
                                        "distance_mm": rd.distance_mm, "visible_fraction": rd.visible_fraction,
                                        "candidates": rd.candidates, "reason": rd.reason})
                if rd.strategy == "TRIM_JUMP":
                    st.append(Stitch(cur[0], cur[1], TRIM, col, oid, o.layer, "connector:trim"))
                    st.append(Stitch(entry[0], entry[1], JUMP, col, oid, o.layer, "connector:jump"))
                elif rd.strategy == "JUMP":
                    st.append(Stitch(entry[0], entry[1], JUMP, col, oid, o.layer, "connector:jump"))
                elif rd.strategy == "HIDDEN_ROUTED":  # routed around open fabric, entirely under later stitching
                    for (px_, py_) in _resample_path(rd.path, 2.5)[1:]:
                        st.append(Stitch(px_, py_, STITCH, col, oid, o.layer, "connector:hidden_routed_travel"))
                else:  # hidden travel stitches under the next object
                    n = max(1, int(math.ceil(rd.distance_mm / 2.5)))
                    for k in range(1, n + 1):
                        t = k / n
                        st.append(Stitch(cur[0] + (entry[0] - cur[0]) * t, cur[1] + (entry[1] - cur[1]) * t,
                                         STITCH, col, oid, o.layer, "connector:hidden_travel"))
        prev_pt = None
        prev_kind = None
        for part in r.parts:
            if part.command:  # machine stop (operator action): needle stays where it is
                lx, ly = (st[-1].x, st[-1].y) if st else (0.0, 0.0)
                st.append(Stitch(lx, ly, part.command, col, oid, o.layer, part.name))
                continue
            for k, (x, y) in enumerate(part.points):
                if k == 0:
                    if prev_pt is None:  # very first penetration of this object
                        last = st[-1] if st else None
                        if not (last and last.command == STITCH and math.dist((last.x, last.y), (x, y)) < 1e-6):
                            st.append(Stitch(x, y, STITCH, col, oid, o.layer, part.name))
                        prev_pt = (x, y)
                        continue
                    d = math.dist(prev_pt, (x, y))
                    if d <= 0.05:
                        continue  # part boundary already at this point
                    rings = [o.geometry] + list(o.holes_mm)
                    inside = _join_is_inside(prev_pt, (x, y), rings)
                    if not inside or (d > lim.max_stitch_mm and prev_kind != "underlay"):
                        # hop leaves the region: try to walk along the object's own edge (travel stitches inside the
                        # region) before resorting to a jump
                        walk = route_hidden_path(prev_pt, (x, y), inner[oid], scale, max_detour=3.0, max_abs_detour_mm=12.0, endpoint_mask=masks[oid]) if d > 0.8 else None
                        if walk:
                            wp = _resample_path(walk, 2.0)
                            # strict re-check against the exact polygon; only the first/last 1mm (where the path meets the
                            # boundary) may sit within 0.15mm of it
                            chk = _resample_path(walk, 0.4)
                            ok_ = True
                            for q in chk[1:-1]:
                                near_end = math.dist(q, prev_pt) < 1.0 or math.dist(q, (x, y)) < 1.0
                                if not (point_in_region(q, o.geometry, o.holes_mm) or (near_end and _near(q, o, 0.15))):
                                    ok_ = False; break
                            if not ok_:
                                walk = None
                        if walk:
                            last = (st[-1].x, st[-1].y)
                            for (wx, wy) in wp[1:]:
                                if math.dist(last, (wx, wy)) > 0.05:
                                    st.append(Stitch(wx, wy, STITCH, col, oid, o.layer, "connector:edge_travel")); last = (wx, wy)
                            prev_pt = (x, y)
                            continue  # the anchor stitch at (x, y) was just emitted by the walk
                        if True:
                            st.append(Stitch(x, y, JUMP, col, oid, o.layer, "connector:intra_object_jump"))
                    elif d > lim.max_stitch_mm:
                        # underlay -> next part inside the region: travel stitches, hidden by the cover sewn afterwards
                        n_ = int(math.ceil(d / 3.0))
                        for k_ in range(1, n_):
                            t_ = k_ / n_
                            st.append(Stitch(prev_pt[0] + (x - prev_pt[0]) * t_, prev_pt[1] + (y - prev_pt[1]) * t_, STITCH, col, oid, o.layer, "underlay:hidden_travel"))
                prev_pt = (x, y)
                st.append(Stitch(x, y, STITCH, col, oid, o.layer, part.name))
            prev_kind = part.kind
        cur, cur_color = r.exit, col
    if st:
        st.append(Stitch(cur[0], cur[1], TRIM, cur_color, "", 0, "connector:end_trim"))
        st.append(Stitch(cur[0], cur[1], END, cur_color, "", 0, "connector:end"))
    return plan, gen


# --------------------------------------------------------------------- validate
@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(f.severity == "ERROR" for f in self.findings):
            return "ERROR"
        return "WARNING" if any(f.severity == "WARNING" for f in self.findings) else "VALID"

    def to_dict(self):
        return {"status": self.status, "findings": [f.to_dict() for f in self.findings]}


def validate_stitch_plan(plan: StitchPlan, objects: list[EmbroideryObject], dependencies=None,
                         max_stitch_mm: float = 12.1, warn_stitch_mm: float = 7.5, min_stitch_mm: float = 0.2,
                         satin_warn_mm: float = 10.6, max_jump_mm: float = 30.0, margin_mm: float = 2.0, region_tol_mm: float = 0.6) -> ValidationReport:
    rep = ValidationReport()
    add = rep.findings.append
    byid = {o.object_id: o for o in objects}
    W, H = plan.canvas_mm
    bad = [s for s in plan.stitches if not (math.isfinite(s.x) and math.isfinite(s.y))]
    if bad:
        add(Finding("ERROR", "NON_FINITE_COORDINATE", f"{len(bad)} stitch(es) have NaN/inf coordinates", bad[0].object_id, len(bad)))
    oob = {}
    for s in plan.stitches:
        if math.isfinite(s.x) and not (-margin_mm <= s.x <= W + margin_mm and -margin_mm <= s.y <= H + margin_mm):
            oob[s.object_id] = oob.get(s.object_id, 0) + 1
    for oid, c in oob.items():
        add(Finding("ERROR", "OUT_OF_BOUNDS", f"{c} stitch(es) outside the {W:.0f}x{H:.0f}mm canvas", oid, c))
    # stitch length, zero length, color continuity
    prev = None; cur_color = None; zero = {}; short = {}; long_w = {}; long_e = {}; jumps = {}
    for s in plan.stitches:
        if s.command == COLOR_CHANGE:
            cur_color = s.color
        if s.command == STITCH:
            if cur_color is None:
                cur_color = s.color
            elif s.color != cur_color:
                add(Finding("ERROR", "MISSING_COLOR_CHANGE", f"stitch of color {s.color} sewn while machine has {cur_color}", s.object_id))
                cur_color = s.color
            if prev is not None and prev.command in (STITCH, JUMP, COLOR_CHANGE):
                d = math.dist((prev.x, prev.y), (s.x, s.y))
                if prev.command == STITCH:
                    if d < 1e-6:
                        zero[s.object_id] = zero.get(s.object_id, 0) + 1
                    elif d < min_stitch_mm:
                        short[s.object_id] = short.get(s.object_id, 0) + 1
                    elif d > max_stitch_mm:
                        long_e[s.object_id] = long_e.get(s.object_id, 0) + 1
                    elif d > (satin_warn_mm if "satin" in s.part else warn_stitch_mm):
                        long_w[s.object_id] = long_w.get(s.object_id, 0) + 1
        elif s.command == JUMP and prev is not None:
            d = math.dist((prev.x, prev.y), (s.x, s.y))
            if d > max_jump_mm:
                jumps[s.object_id] = jumps.get(s.object_id, 0) + 1
        if s.command not in (TRIM,):
            prev = s
    for oid, c in zero.items():
        add(Finding("WARNING", "ZERO_LENGTH_STITCH", f"{c} duplicate-position stitch(es)", oid, c))
    for oid, c in short.items():
        add(Finding("WARNING", "SHORT_STITCH", f"{c} stitch(es) shorter than {min_stitch_mm}mm", oid, c))
    for oid, c in long_w.items():
        add(Finding("WARNING", "LONG_STITCH", f"{c} stitch(es) longer than {warn_stitch_mm}mm (snag risk)", oid, c))
    for oid, c in long_e.items():
        add(Finding("ERROR", "STITCH_EXCEEDS_MACHINE_LIMIT", f"{c} stitch(es) longer than {max_stitch_mm}mm", oid, c))
    for oid, c in jumps.items():
        add(Finding("WARNING", "LONG_JUMP", f"{c} jump(s) longer than {max_jump_mm}mm", oid, c))
    # unknown object ids
    for s in plan.stitches:
        if s.object_id and s.object_id not in byid:
            add(Finding("ERROR", "UNKNOWN_OBJECT_ID", f"stitch references unknown object {s.object_id}", s.object_id)); break
    # containment: cover stitches of fills/satin must sit in/near their region
    scale = min(8.0, 800.0 / max(plan.canvas_mm))
    for oid in plan.order:
        o = byid.get(oid)
        if not o:
            continue
        pts = [(s.x, s.y) for s in plan.stitches if s.object_id == oid and s.part.startswith("cover") and "border" not in s.part and s.command == STITCH]
        if not pts and any(s.object_id == oid and "border" in s.part for s in plan.stitches):
            continue  # object consists of border stitches only (e.g. some applique parts)
        if not pts:
            add(Finding("ERROR", "NO_COVER_STITCHES", "object was ordered but has no cover stitches", oid)); continue
        # SEGMENT-level check: a stitch may have both ends in the region yet bridge a hole.
        bridged = 0
        stream = [q for q in plan.stitches if q.command != TRIM]
        for a_, b_ in zip(stream, stream[1:]):  # true neighbours in the machine stream (a JUMP in between breaks adjacency)
            if (b_.object_id == oid and a_.object_id == oid and a_.command == STITCH and b_.command == STITCH
                    and b_.part.startswith("cover") and math.dist((a_.x, a_.y), (b_.x, b_.y)) > 0.6):
                mid = ((a_.x + b_.x) / 2, (a_.y + b_.y) / 2)
                # applique/puff borders straddle the outline by design (half border width + margin)
                tol_ = 1.8 if "border" in b_.part else region_tol_mm
                if not (point_in_region(mid, o.geometry, o.holes_mm) or _near(mid, o, tol_)):
                    bridged += 1
        if bridged:
            add(Finding("ERROR", "STITCH_CROSSES_HOLE_OR_OUTSIDE", f"{bridged} stitch segment(s) pass over a hole or outside the region", oid, bridged))
        near = 0
        for p in pts:
            if point_in_region(p, o.geometry, o.holes_mm) or _near(p, o, region_tol_mm):
                near += 1
        frac = near / len(pts)
        # satin rails are widened by pull compensation; allow the tolerance
        if frac < 0.9:
            add(Finding("ERROR", "STITCHES_OUTSIDE_REGION", f"only {frac*100:.0f}% of cover stitches lie inside the object's region (tol {region_tol_mm}mm)", oid))
        elif frac < 0.98:
            add(Finding("WARNING", "STITCHES_NEAR_BOUNDARY", f"{(1-frac)*100:.1f}% of cover stitches outside region", oid))
        if not any(s.object_id == oid and s.part.startswith("underlay") for s in plan.stitches) and _min_width(o) > 2.0:
            add(Finding("WARNING", "NO_UNDERLAY", f"no underlay on a {_min_width(o):.1f}mm-wide object", oid))
    # sequence dependencies
    pos = {oid: i for i, oid in enumerate(plan.order)}
    for k, v in (dependencies or {}).items():
        for d in v:
            if k in pos and d in pos and pos[d] > pos[k]:
                add(Finding("ERROR", "DEPENDENCY_VIOLATED", f"{d} must be sewn before {k}", k))
    for oid in plan.skipped:
        st = plan.object_status.get(oid, {})
        add(Finding("ERROR", "OBJECT_NOT_GENERATED", "; ".join(st.get("errors", ["not generated"])), oid))
    if not rep.findings:
        add(Finding("VALID", "ALL_CHECKS_PASSED", "no findings"))
    return rep


def _min_width(o):
    return (o.width_stats_mm or {}).get("median", 0.0)


def _near(p, o, tol):
    from .geometry import dist_point_to_polyline
    d = dist_point_to_polyline(p, o.geometry, True)
    for h in o.holes_mm:
        d = min(d, dist_point_to_polyline(p, h, True))
    return d <= tol


# ------------------------------------------------------------------ DST export
def to_dst_design(plan: StitchPlan):
    """Convert to the project's DST Design model (0.1 mm units, y flipped:
    DST y is up, image y is down). Long moves are split (<=12.1mm/record).
    TRIM has no DST record type: it is encoded as three zero-length jumps,
    the common convention that many machines/software read as a trim
    (machine-specific; verify on your target machine)."""
    from .models import Design, Record, StitchKind

    recs = []
    first = next((q for q in plan.stitches if q.command in (STITCH, JUMP)), None)
    # DST is relative: start the needle AT the first point (no travel from an arbitrary origin)
    x, y = (round(first.x * 10), round(-first.y * 10)) if first else (0, 0)
    def emit_jump(nx, ny):
        nonlocal x, y
        x0, y0 = x, y
        dx, dy = nx - x0, ny - y0
        steps = max(1, int(math.ceil(max(abs(dx), abs(dy)) / 121.0)))
        for k in range(1, steps + 1):
            tx = nx if k == steps else x0 + round(dx * k / steps)
            ty = ny if k == steps else y0 + round(dy * k / steps)
            recs.append(Record(tx, ty, tx - x, ty - y, StitchKind.JUMP))
            x, y = tx, ty
    for s in plan.stitches:
        nx, ny = round(s.x * 10), round(-s.y * 10)
        if s.command == STITCH:
            if max(abs(nx - x), abs(ny - y)) > 121:
                raise ValueError("stitch exceeds 12.1mm; validator should have blocked export")
            recs.append(Record(nx, ny, nx - x, ny - y, StitchKind.STITCH)); x, y = nx, ny
        elif s.command == JUMP:
            emit_jump(nx, ny)
        elif s.command == TRIM:
            for _ in range(3):
                recs.append(Record(x, y, 0, 0, StitchKind.JUMP))
        elif s.command == COLOR_CHANGE:
            recs.append(Record(x, y, 0, 0, StitchKind.COLOR_CHANGE))
        elif s.command == STOP:  # DST has no separate stop record: encoded as a colour-change/stop code
            recs.append(Record(x, y, 0, 0, StitchKind.COLOR_CHANGE))
        elif s.command == END:
            recs.append(Record(x, y, 0, 0, StitchKind.END))
    return Design(records=recs, header={}, source="embcopilot-generated")
