"""
Stitch strategies (Phases 8/9): turn one object + its technical plan into
ACTUAL needle-penetration points. Units mm, y down (image space).

IMPLEMENTED (tested): run / double / triple run along a centerline or
outline; variable-width satin column from centerline + width profile with
real left/right rails and validation (rail crossing, self-intersection,
width limits); closed satin ring from outer + hole contours; tatami-style
staggered fill with holes (even-odd scanlines at an arbitrary angle);
underlay (center run, edge run, zigzag, tatami) as separate parts.

NOT modelled: physical thread behaviour, machine-specific push/pull, Wilcom's
own fill algorithms. Pull compensation is applied as a simple rail widening.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .domain import EmbroideryObject, StitchType, new_id
from .geometry import (Point, dist_point_to_polyline, normals, polygon_area, polyline_length,
                       polylines_intersect, rasterize, resample_polyline, smooth_polyline)
from .profiles import ProductionProfile


@dataclass
class StitchPart:
    name: str  # "underlay:edge_run", "cover:satin", ...
    points: list[Point]
    kind: str = "underlay"  # underlay | cover | control
    command: str | None = None  # "STOP" = machine stop (e.g. place applique fabric / puff foam)


@dataclass
class ObjectStitches:
    object_id: str
    strategy: str
    parts: list[StitchPart] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # geometry could not be generated
    rails: dict | None = None  # {"left":[...], "right":[...]} for satin
    fallback_from: str | None = None  # set when the requested strategy was invalid

    @property
    def ok(self) -> bool:
        return not self.errors and any(p.points for p in self.parts if p.kind != "control")

    @property
    def entry(self) -> Point | None:
        for p in self.parts:
            if p.points:
                return p.points[0]
        return None

    @property
    def exit(self) -> Point | None:
        for p in reversed(self.parts):
            if p.points:
                return p.points[-1]
        return None


@dataclass
class StrategyLimits:
    max_stitch_mm: float = 7.0
    min_stitch_mm: float = 0.3
    max_satin_width_mm: float = 10.0
    min_satin_width_mm: float = 0.8
    fill_stitch_len_mm: float = 3.5
    underlay_inset_mm: float = 0.4
    underlay_spacing_mm: float = 2.0


# --------------------------------------------------------------------- helpers
def _inset_contours(outer, holes, dist_mm, canvas_mm) -> list[list[Point]]:
    """Inset region boundary by dist_mm via raster erosion (robust for concave shapes)."""
    import cv2

    scale = min(12.0, 1400.0 / max(canvas_mm))
    size = (int(math.ceil(canvas_mm[0] * scale)) + 4, int(math.ceil(canvas_mm[1] * scale)) + 4)
    m = rasterize(outer, holes, scale, size)
    r = max(1, int(round(dist_mm * scale)))
    m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    cs, _ = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    out = []
    for c in cs:
        if len(c) >= 6:
            eps = 0.4 * scale / 10
            c = cv2.approxPolyDP(c, max(0.5, eps), True)
            out.append([(float(p[0][0]) / scale, float(p[0][1]) / scale) for p in c])
    return out


def _dedupe(pts: list[Point], tol=1e-6) -> list[Point]:
    out = []
    for p in pts:
        if not out or math.dist(p, out[-1]) > tol:
            out.append(p)
    return out


def _cap_stitches(pts: list[Point], max_len: float) -> list[Point]:
    """Subdivide any step longer than max_len (keeps original vertices)."""
    out: list[Point] = []
    for p in pts:
        if out:
            d = math.dist(out[-1], p)
            if d > max_len:
                n = int(math.ceil(d / max_len))
                for k in range(1, n):
                    t = k / n
                    out.append((out[-1][0] + (p[0] - out[-1][0]) * t, out[-1][1] + (p[1] - out[-1][1]) * t))
        out.append(p)
    return out


# ------------------------------------------------------------------------- run
def run_path(obj: EmbroideryObject, stitch_type: str, prof: ProductionProfile) -> ObjectStitches:
    res = ObjectStitches(obj.object_id, stitch_type)
    if obj.centerline_mm and len(obj.centerline_mm) >= 2:
        path = smooth_polyline(list(obj.centerline_mm), 5)
    elif len(obj.geometry) >= 3:
        path = list(obj.geometry) + [obj.geometry[0]]
        res.warnings.append("no centerline: run stitches follow the outline")
    else:
        res.errors.append("GEOMETRY_UNAVAILABLE: no centerline or outline")
        return res
    pts = resample_polyline(path, prof.run_stitch_length_mm)
    if stitch_type == StitchType.TRIPLE_RUN.value:
        pts = pts + pts[::-1][1:] + pts[1:]
    res.parts.append(StitchPart(f"cover:{stitch_type}", _dedupe(pts), "cover"))
    return res


# ----------------------------------------------------------------------- satin
def build_rails(centerline: list[Point], widths: list[float], pull_mm: float = 0.0,
                smooth: int = 5, step_mm: float = 0.5) -> tuple[list[Point], list[Point], list[Point], list[float]]:
    """Left/right rails offset by half the local width (+ pull compensation).

    The centerline is first resampled at a fixed `step_mm` of arc length and
    the width profile interpolated onto it, so smoothing and curvature tests
    mean the same thing at every image resolution (raster skeletons are
    stair-stepped and unevenly spaced, which used to create false rail
    self-intersections)."""
    c0 = np.asarray(centerline, float)
    w0 = np.asarray(widths[: len(c0)] if len(widths) >= len(c0) else list(widths) + [widths[-1]] * (len(c0) - len(widths)), float)
    seg = np.hypot(*np.diff(c0, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(3, int(cum[-1] / step_mm) + 1)
    t = np.linspace(0, cum[-1], n)
    cx, cy, w = np.interp(t, cum, c0[:, 0]), np.interp(t, cum, c0[:, 1]), np.interp(t, cum, w0)
    c = smooth_polyline([(float(x), float(y)) for x, y in zip(cx, cy)], max(3, int(round(2.0 / step_mm)) | 1))
    if len(w) > 4:
        k = np.ones(5) / 5
        w = np.convolve(np.pad(w, 2, mode="edge"), k, mode="valid")
    half = np.maximum(w, 0.0) / 2 + pull_mm / 2
    nrm = normals(c)
    a = np.asarray(c)
    left = a + nrm * half[:, None]
    right = a - nrm * half[:, None]
    return ([tuple(map(float, p)) for p in left], [tuple(map(float, p)) for p in right], c, list(map(float, 2 * half)))


def min_curvature_radius(c: list[Point], span_mm: float = 2.0) -> float:
    """Smallest local radius of curvature (mm) of a polyline, from heading change over ~span_mm of arc."""
    a = np.asarray(c, float)
    if len(a) < 5:
        return float("inf")
    seg = np.hypot(*np.diff(a, axis=0).T)
    step = max(float(np.median(seg)), 1e-6)
    k = max(1, int(round(span_mm / 2 / step)))
    best = float("inf")
    for i in range(k, len(a) - k):
        v1 = a[i] - a[i - k]; v2 = a[i + k] - a[i]
        n1, n2 = np.hypot(*v1), np.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        cosang = float(np.clip(v1 @ v2 / (n1 * n2), -1, 1))
        dtheta = math.acos(cosang)
        if dtheta > 1e-3:
            best = min(best, (n1 + n2) / 2 / dtheta)
    return best


def validate_rails(left, right, widths, lim: StrategyLimits, centerline: list[Point] | None = None) -> list[str]:
    errs = []
    if centerline is not None:
        r = min_curvature_radius(centerline)
        half = float(np.max(widths)) / 2
        if r < half:
            errs.append(f"SATIN_RAIL_CURVATURE: local radius {r:.1f}mm is smaller than half the column width {half:.1f}mm (inner rail folds over)")
    if polylines_intersect(left) or polylines_intersect(right):
        errs.append("SATIN_RAIL_SELF_INTERSECTION: a rail crosses itself (curvature radius smaller than half-width)")
    if polylines_intersect(left, right):
        errs.append("SATIN_RAILS_CROSS: left and right rails intersect")
    w = np.asarray(widths)
    if w.max() > lim.max_satin_width_mm:
        errs.append(f"SATIN_TOO_WIDE: {w.max():.1f}mm > {lim.max_satin_width_mm}mm (split satin or use fill)")
    # Tapered tips are normal in satin (and raster distance transforms always dip near a
    # skeleton end), so the "too narrow" test looks at the interior 84% of the column only.
    k = max(1, int(0.08 * len(w)))
    interior = w[k:-k] if len(w) > 2 * k + 2 else w
    if np.percentile(interior, 10) < lim.min_satin_width_mm:
        errs.append(f"SATIN_TOO_NARROW: {np.percentile(interior,10):.2f}mm < {lim.min_satin_width_mm}mm (use run stitch)")
    return errs


def _zigzag(left, right, spacing) -> list[Point]:
    """Alternate left/right penetrations, one pair per `spacing` along the column."""
    L = np.asarray(left); R = np.asarray(right)
    length = float(np.hypot(*np.diff((L + R) / 2, axis=0).T).sum())
    n = max(2, int(math.ceil(length / spacing)))
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff((L + R) / 2, axis=0).T))])
    t = np.linspace(0, cum[-1], n + 1)
    lx, ly = np.interp(t, cum, L[:, 0]), np.interp(t, cum, L[:, 1])
    rx, ry = np.interp(t, cum, R[:, 0]), np.interp(t, cum, R[:, 1])
    pts = []
    for i in range(n + 1):
        a, b = ((lx[i], ly[i]), (rx[i], ry[i]))
        pts += [a, b] if i % 2 == 0 else [b, a]
    return [(float(x), float(y)) for x, y in pts]


def satin_column(obj: EmbroideryObject, density_mm: float, pull_mm: float, underlay: str,
                 lim: StrategyLimits, canvas_mm) -> ObjectStitches:
    res = ObjectStitches(obj.object_id, StitchType.SATIN.value)
    if not obj.centerline_mm or len(obj.centerline_mm) < 3 or not obj.centerline_widths_mm:
        res.errors.append("GEOMETRY_UNAVAILABLE: satin column needs a centerline with width profile")
        return res
    topo = obj.skeleton_topology or {}
    if topo.get("branch_points", 0) >= 1 or obj.holes_mm:
        res.errors.append("SATIN_COMPLEX_TOPOLOGY: branched/holed shape cannot be covered by one satin column (multi-column satin not implemented)")
        return res
    left, right, c, w = build_rails(obj.centerline_mm, obj.centerline_widths_mm, pull_mm)
    errs = validate_rails(left, right, w, lim, c)
    if errs:
        res.errors += errs
        res.rails = {"left": left, "right": right}
        return res
    res.rails = {"left": left, "right": right, "centerline": c, "width_profile_mm": w}
    ins = lim.underlay_inset_mm
    if underlay in ("center_run", "zigzag", "double_zigzag", "edge_run"):
        core = resample_polyline(c, 2.0)
        if underlay == "center_run":
            res.parts.append(StitchPart("underlay:center_run", core))
        else:
            li, ri, *_ = build_rails(obj.centerline_mm, [max(x - 2 * ins, 0.2) for x in obj.centerline_widths_mm], 0.0)
            res.parts.append(StitchPart("underlay:edge_run", resample_polyline(li, 2.0) + resample_polyline(ri, 2.0)[::-1]))
            if underlay in ("zigzag", "double_zigzag"):
                res.parts.append(StitchPart("underlay:zigzag", _zigzag(li, ri, lim.underlay_spacing_mm)))
            if underlay == "double_zigzag":
                res.parts.append(StitchPart("underlay:zigzag2", _zigzag(li, ri, lim.underlay_spacing_mm)[::-1]))
    zz = _zigzag(left, right, density_mm)
    zz = _cap_stitches(zz, lim.max_stitch_mm + 5)  # cross-column stitch <= max_satin_width
    res.parts.append(StitchPart("cover:satin", zz, "cover"))
    return res


def satin_ring(obj: EmbroideryObject, density_mm: float, pull_mm: float, lim: StrategyLimits, canvas_mm, underlay: str = "center_run") -> ObjectStitches:
    """Closed satin: outer contour = left rail, hole contour = right rail."""
    res = ObjectStitches(obj.object_id, StitchType.SATIN.value)
    if len(obj.holes_mm) != 1 or len(obj.geometry) < 6:
        res.errors.append("GEOMETRY_UNAVAILABLE: satin ring needs exactly one hole contour")
        return res
    def ring_resample(poly, n):
        closed = poly + [poly[0]]
        return resample_polyline(closed, polyline_length(closed) / n)[:-1]

    total = 0.5 * (polyline_length(obj.geometry + [obj.geometry[0]]) + polyline_length(obj.holes_mm[0] + [obj.holes_mm[0][0]]))
    inner_len = polyline_length(obj.holes_mm[0] + [obj.holes_mm[0][0]])
    # one pair per `density_mm` along the band centre, but never denser than 0.35mm on the shorter inner rail
    n = max(16, int(min(total / density_mm, inner_len / 0.35)))
    o = ring_resample(obj.geometry, n); h = ring_resample(obj.holes_mm[0], n)
    m = min(len(o), len(h)); O, H = np.asarray(o[:m]), np.asarray(h[:m])
    # align hole contour to outer by minimising pair distance over cyclic shift and orientation
    best = None
    for Hc in (H, H[::-1]):
        for sh in range(0, len(Hc), max(1, len(Hc) // 90)):
            d = float(np.hypot(*(O - np.roll(Hc, sh, 0)).T).sum())
            if best is None or d < best[0]:
                best = (d, np.roll(Hc, sh, 0))
    H = best[1]
    widths = np.hypot(*(O - H).T)
    if widths.max() > lim.max_satin_width_mm:
        res.errors.append(f"SATIN_TOO_WIDE: ring width {widths.max():.1f}mm > {lim.max_satin_width_mm}mm")
        return res
    if np.percentile(widths, 10) < lim.min_satin_width_mm:
        res.errors.append("SATIN_TOO_NARROW: ring band thinner than minimum satin width")
        return res
    ctr = (O + H) / 2
    d = (O - H) / np.maximum(widths, 1e-9)[:, None]
    Ol = O + d * pull_mm / 2; Hl = H - d * pull_mm / 2
    res.rails = {"left": [tuple(map(float, p)) for p in Ol], "right": [tuple(map(float, p)) for p in Hl]}
    # v0.7: the requested underlay is honoured for rings (it used to be ignored: every ring got a centre run)
    ul = underlay if underlay in ("none", "center_run", "edge_run", "zigzag", "double_zigzag") else "center_run"
    if ul == "center_run":
        c2 = np.vstack([ctr, ctr[:1]])
        res.parts.append(StitchPart("underlay:center_run", resample_polyline([tuple(map(float, p)) for p in c2], 2.0)))
    elif ul != "none":
        ins = min(lim.underlay_inset_mm, float(widths.min()) / 3.0)
        Oi = np.vstack([O - d * ins, (O - d * ins)[:1]]); Hi = np.vstack([H + d * ins, (H + d * ins)[:1]])
        res.parts.append(StitchPart("underlay:edge_run", resample_polyline([tuple(map(float, q)) for q in Oi], 2.0)
                                    + resample_polyline([tuple(map(float, q)) for q in Hi], 2.0)[::-1]))
        if ul in ("zigzag", "double_zigzag"):
            res.parts.append(StitchPart("underlay:zigzag", _zigzag(Oi, Hi, lim.underlay_spacing_mm)))
        if ul == "double_zigzag":
            res.parts.append(StitchPart("underlay:zigzag2", _zigzag(Oi, Hi, lim.underlay_spacing_mm)[::-1]))
    pts = []
    for k in range(len(Ol)):
        a_, b_ = tuple(map(float, Ol[k])), tuple(map(float, Hl[k]))
        pts += [a_, b_] if k % 2 == 0 else [b_, a_]
    pts.append(pts[0])  # close the ring
    res.parts.append(StitchPart("cover:satin_ring", pts, "cover"))
    return res


# ------------------------------------------------------------------------ fill
def _scan_segments(rings: list[list[Point]], angle_deg: float, spacing: float, origin: Point):
    """Even-odd scanline hatch in a frame rotated by angle. Returns
    [(row_index, [(x0,x1),...])] in the ROTATED frame plus (cos,sin)."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    R = np.array([[ca, sa], [-sa, ca]])  # world -> rotated
    ox, oy = origin
    rr = [((np.asarray(r) - [ox, oy]) @ R.T) for r in rings]
    ymin = min(r[:, 1].min() for r in rr); ymax = max(r[:, 1].max() for r in rr)
    rows = []
    k0 = int(math.ceil(ymin / spacing)); k1 = int(math.floor(ymax / spacing))
    for k in range(k0, k1 + 1):
        y = k * spacing + 1e-7
        xs = []
        for r in rr:
            p, q = r, np.roll(r, -1, axis=0)
            m = (p[:, 1] > y) != (q[:, 1] > y)
            if m.any():
                pp, qq = p[m], q[m]
                xs += (pp[:, 0] + (y - pp[:, 1]) * (qq[:, 0] - pp[:, 0]) / (qq[:, 1] - pp[:, 1])).tolist()
        xs.sort()
        segs = [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2) if xs[i + 1] - xs[i] > 1e-6]
        rows.append((k, y, segs))
    return rows, R


def _inside_even_odd(pt, rings) -> bool:
    return sum(point_in_polygon_fast(pt, r) for r in rings) % 2 == 1


def point_in_polygon_fast(pt, ring) -> bool:
    from .geometry import point_in_polygon
    return point_in_polygon(pt, ring)


def _join_is_inside(a, b, rings, samples: int | None = None, edge_tol: float = 0.03) -> bool:
    """A join stitch a->b is legal only if the whole straight segment stays in the region
    (otherwise it would bridge a hole or a concavity). Points ON the boundary count as inside
    (rows end on the boundary, so consecutive row ends are joined along it)."""
    if samples is None:  # sample at most every 0.4mm so a thin hole cannot slip between samples
        samples = max(6, int(math.dist(a, b) / 0.4))
    for k in range(1, samples):
        t = k / samples
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if not _inside_even_odd(p, rings) and min(dist_point_to_polyline(p, r, True) for r in rings) > edge_tol:
            return False
    return True


def fill_region(outer: list[Point], holes: list[list[Point]], angle_deg: float, spacing: float,
                stitch_len: float, stagger: bool = True, edge_shrink: float = 0.0):
    """Tatami-style hatch. Returns list of point lists (one per contiguous run)."""
    rings = [outer] + list(holes)
    org = (float(np.mean([p[0] for p in outer])), float(np.mean([p[1] for p in outer])))
    rows, R = _scan_segments(rings, angle_deg, spacing, org)
    Rinv = R.T
    runs: list[list[Point]] = []
    cur: list[Point] = []
    direction = 1
    for ri, (k, y, segs) in enumerate(rows):
        row_pts: list[list[tuple[float, float]]] = []
        for (x0, x1) in segs:
            x0 += edge_shrink; x1 -= edge_shrink
            if x1 - x0 < 1e-6:
                continue
            off = ((k % 3) * stitch_len / 3.0) if stagger else 0.0
            xs = [x0]
            g = math.floor((x0 - off) / stitch_len) + 1
            while g * stitch_len + off < x1 - 0.3:
                px = g * stitch_len + off
                if px > x0 + 0.3:
                    xs.append(px)
                g += 1
            xs.append(x1)
            row_pts.append([(x, y) for x in xs])
        if direction < 0:
            row_pts = [seg[::-1] for seg in row_pts[::-1]]
        direction *= -1
        for seg in row_pts:
            world = [tuple((np.array(p) @ Rinv.T + org).tolist()) for p in seg]
            if cur and math.dist(cur[-1], world[0]) <= max(stitch_len * 1.6, spacing * 3) \
                    and _join_is_inside(cur[-1], world[0], rings):
                cur += world
            else:
                if cur:
                    runs.append(cur)
                cur = list(world)
    if cur:
        runs.append(cur)
    return runs


def fill_object(obj: EmbroideryObject, angle_deg: float, spacing: float, underlay: str,
                lim: StrategyLimits, canvas_mm, stitch_type: str, pull_mm: float = 0.0) -> ObjectStitches:
    res = ObjectStitches(obj.object_id, stitch_type)
    if len(obj.geometry) < 3 or polygon_area(obj.geometry) < 0.5:
        res.errors.append("GEOMETRY_UNAVAILABLE: fill needs a closed outline with area")
        return res
    outer, holes = obj.geometry, list(obj.holes_mm)
    if underlay in ("tatami", "edge_run", "zigzag"):
        for ring in _inset_contours(outer, holes, lim.underlay_inset_mm + 0.2, canvas_mm):
            closed = ring + [ring[0]]
            res.parts.append(StitchPart("underlay:edge_run", resample_polyline(closed, 2.0)))
        inner = _inset_contours(outer, holes, lim.underlay_inset_mm + 0.2, canvas_mm)
        if inner:
            big = max(inner, key=polygon_area)
            hs = [r for r in inner if r is not big]
            if polygon_area(big) > 4:
                for run in fill_region(big, hs, angle_deg + 90.0, lim.underlay_spacing_mm * 1.5, 3.5, stagger=False):
                    res.parts.append(StitchPart("underlay:tatami", _dedupe(run)))
    for run in fill_region(outer, holes, angle_deg, spacing, lim.fill_stitch_len_mm, edge_shrink=0.0):
        res.parts.append(StitchPart(f"cover:{stitch_type}", _dedupe(run), "cover"))
    if not any(p.kind == "cover" and p.points for p in res.parts):
        res.errors.append("FILL_EMPTY: no scanline intersected the region (spacing larger than shape?)")
    return res


# ------------------------------------------------------------------- dispatcher
def generate_object(obj: EmbroideryObject, plan, prof: ProductionProfile, canvas_mm,
                    lim: StrategyLimits | None = None) -> ObjectStitches:
    lim = lim or StrategyLimits()
    st = plan.stitch_type
    if st in (StitchType.RUN.value, StitchType.TRIPLE_RUN.value, StitchType.MANUAL_RUN.value):
        return run_path(obj, st, prof)
    if st == StitchType.SATIN.value:
        if obj.holes_mm and (obj.skeleton_topology or {}).get("closed_loop"):
            r = satin_ring(obj, plan.density_mm, plan.pull_compensation_mm, lim, canvas_mm, plan.underlay)
        else:
            r = satin_column(obj, plan.density_mm, plan.pull_compensation_mm, plan.underlay, lim, canvas_mm)
        if r.errors and len(obj.geometry) >= 3:
            # Never emit an invalid satin. Fall back to a fill, and SAY SO.
            fb = fill_object(obj, plan.direction_deg if plan.direction_deg is not None else 45.0,
                             0.42 * prof.spacing_scale, "tatami", lim, canvas_mm, StitchType.TATAMI.value)
            if fb.ok:
                fb.warnings.append("FALLBACK_SATIN_TO_TATAMI: " + "; ".join(r.errors))
                fb.fallback_from = StitchType.SATIN.value
                return fb
        return r
    if st in (StitchType.TATAMI.value, StitchType.COMPLEX_FILL.value):
        ang = plan.direction_deg if plan.direction_deg is not None else 45.0
        return fill_object(obj, ang, plan.density_mm, plan.underlay, lim, canvas_mm, st, plan.pull_compensation_mm)
    ang = plan.direction_deg if plan.direction_deg is not None else 45.0
    if st == StitchType.APPLIQUE.value:
        return applique(obj, lim, canvas_mm)
    if st == StitchType.PUFF.value:
        return puff(obj, ang, lim, canvas_mm)
    if st == StitchType.CROSS_STITCH.value:
        return cross_stitch(obj, lim, canvas_mm)
    if st == StitchType.GRADIENT.value:
        return fill_object(obj, ang, float(obj.params.get("gradient_density_mm", plan.density_mm)), plan.underlay, lim, canvas_mm, StitchType.TATAMI.value)
    r = ObjectStitches(obj.object_id, st)
    r.errors.append(f"STRATEGY_UNAVAILABLE: no stitch generator for '{st}'")
    return r


# ============================================================ advanced stitch types
def _contours_at(outer, holes, dist_mm, canvas_mm, grow=False):
    """Offset region boundaries by dist_mm (inward, or outward when grow=True) using raster morphology.
    Returns (outer_ring_list, hole_ring_list) after the offset."""
    import cv2

    scale = min(12.0, 1400.0 / max(canvas_mm))
    pad = int(dist_mm * scale) + 6
    size = (int(math.ceil(canvas_mm[0] * scale)) + 2 * pad, int(math.ceil(canvas_mm[1] * scale)) + 2 * pad)
    shifted = lambda pts: [(x + pad / scale, y + pad / scale) for x, y in pts]  # noqa: E731
    m = rasterize(shifted(outer), [shifted(h) for h in holes], scale, size)
    r = max(1, int(round(dist_mm * scale)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    m = cv2.dilate(m, k) if grow else cv2.erode(m, k)
    cs, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    outs, hls = [], []
    for c, h in zip(cs, hier[0] if hier is not None else []):
        if len(c) < 6:
            continue
        c = cv2.approxPolyDP(c, 0.6, True)
        ring = [(float(p[0][0]) / scale - pad / scale, float(p[0][1]) / scale - pad / scale) for p in c]
        (hls if h[3] != -1 else outs).append(ring)
    return outs, hls


def _band_satin(inner: list[Point], outer: list[Point], density: float) -> list[Point]:
    """Zigzag between two closed, roughly parallel contours (offset curves).
    Correspondence = nearest point on the other contour, sampled every `density` mm along the inner one."""
    closed = inner + [inner[0]]
    samp = resample_polyline(closed, density)[:-1]
    O = np.asarray(outer)
    pts = []
    lastq = None
    for k, p in enumerate(samp):
        d = np.hypot(*(O - np.asarray(p)).T)
        q = tuple(map(float, O[int(d.argmin())]))
        if q == lastq:  # several inner samples mapped to one outer point: skip, it would be a zero-length stitch
            continue
        lastq = q
        pp = tuple(map(float, p))
        pts += [pp, q] if len(pts) % 4 == 0 else [q, pp]
    return pts + [pts[0]] if pts else pts


def applique(obj: EmbroideryObject, lim: StrategyLimits, canvas_mm) -> ObjectStitches:
    """placement run -> STOP (lay fabric) -> tack-down -> STOP (trim fabric) -> satin border."""
    res = ObjectStitches(obj.object_id, StitchType.APPLIQUE.value)
    bw = float(obj.params.get("applique_border_mm", 2.0)); margin = float(obj.params.get("applique_margin_mm", 0.0))
    if len(obj.geometry) < 3 or min(obj.width_mm, obj.height_mm) < 2 * bw + 3.0:
        res.errors.append(f"APPLIQUE_TOO_SMALL: needs at least {2*bw+3.0:.1f}mm in both directions for a {bw}mm border")
        return res
    outs, hls = _contours_at(obj.geometry, obj.holes_mm, 0.0 + margin, canvas_mm, grow=True) if margin > 0 else ([obj.geometry], list(obj.holes_mm))
    rings = outs + hls
    res.parts.append(StitchPart("applique:placement_run", [p for r in rings for p in resample_polyline(r + [r[0]], 2.5)], "cover"))
    res.parts.append(StitchPart("control:STOP_place_fabric", [], "control", "STOP"))
    tin, tho = _contours_at(obj.geometry, obj.holes_mm, 0.8, canvas_mm)
    for r in tin + tho:
        res.parts.append(StitchPart("applique:tack_down", resample_polyline(r + [r[0]], 2.0), "cover"))
    res.parts.append(StitchPart("control:STOP_trim_fabric", [], "control", "STOP"))
    inner_o, inner_h = _contours_at(obj.geometry, obj.holes_mm, bw / 2, canvas_mm)
    outer_o, outer_h = _contours_at(obj.geometry, obj.holes_mm, bw / 2, canvas_mm, grow=True)
    if not inner_o or not outer_o:
        res.errors.append("APPLIQUE_BORDER_FAILED: shape collapses when the border is inset")
        return res
    pairs = list(zip(sorted(inner_o, key=len, reverse=True), sorted(outer_o, key=len, reverse=True))) + \
            list(zip(sorted(inner_h, key=len, reverse=True), sorted(outer_h, key=len, reverse=True)))
    for a, b in pairs:
        res.parts.append(StitchPart("cover:applique_border", _band_satin(a, b, 0.4), "cover"))
    return res


def puff(obj: EmbroideryObject, angle_deg: float, lim: StrategyLimits, canvas_mm) -> ObjectStitches:
    """underlay fill -> STOP (place foam) -> perforation run -> dense raised cover -> satin border. Foam is placed by the operator."""
    res = ObjectStitches(obj.object_id, StitchType.PUFF.value)
    dens = float(obj.params.get("puff_density_mm", 0.30))
    w = (obj.width_stats_mm or {}).get("median", min(obj.width_mm, obj.height_mm))
    if len(obj.geometry) < 3:
        res.errors.append("GEOMETRY_UNAVAILABLE: puff needs a closed outline"); return res
    if w < 2.5:
        res.errors.append(f"PUFF_TOO_NARROW: {w:.1f}mm wide; puff needs >= 2.5mm (foam will not be captured)"); return res
    if dens > 0.36:
        res.warnings.append(f"PUFF_DENSITY_SPARSE: {dens}mm row spacing may let foam show; use <= 0.35mm")
    if w > 25:
        res.warnings.append("PUFF_LARGE_AREA: very large puff areas can shift; consider splitting")
    base = fill_object(obj, angle_deg + 90, 0.8, "edge_run", lim, canvas_mm, StitchType.TATAMI.value)
    for p in base.parts:
        res.parts.append(StitchPart("puff:" + p.name.split(":")[1] + "_base", p.points, "underlay"))
    res.parts.append(StitchPart("control:STOP_place_foam", [], "control", "STOP"))
    for r in _contours_at(obj.geometry, obj.holes_mm, 0.8, canvas_mm)[0]:
        res.parts.append(StitchPart("puff:perforation_run", resample_polyline(r + [r[0]], 1.5), "underlay"))
    cover = fill_object(obj, angle_deg, dens, "none", lim, canvas_mm, StitchType.TATAMI.value)
    for p in cover.parts:
        if p.kind == "cover":
            res.parts.append(StitchPart("cover:puff", p.points, "cover"))
    inner_o, inner_h = _contours_at(obj.geometry, obj.holes_mm, 0.6, canvas_mm)
    outer_o, outer_h = _contours_at(obj.geometry, obj.holes_mm, 0.6, canvas_mm, grow=True)
    # one border band per boundary: the outside edge AND every hole edge (a band across a hole would bridge it)
    pairs = list(zip(sorted(inner_o, key=len, reverse=True), sorted(outer_o, key=len, reverse=True))) + \
            list(zip(sorted(inner_h, key=len, reverse=True), sorted(outer_h, key=len, reverse=True)))
    for a, b in pairs:
        res.parts.append(StitchPart("cover:puff_border", _band_satin(a, b, 0.35), "cover"))
    if not pairs:
        res.warnings.append("PUFF_BORDER_SKIPPED: shape collapses when the border is inset")
    return res


def cross_stitch(obj: EmbroideryObject, lim: StrategyLimits, canvas_mm) -> ObjectStitches:
    """Grid model: fabric count -> cell size; a cell is sewn only if all four corners lie inside the region."""
    from .geometry import point_in_region
    res = ObjectStitches(obj.object_id, StitchType.CROSS_STITCH.value)
    count = float(obj.params.get("cross_count_per_inch", 14)); cell = 25.4 / count
    if len(obj.geometry) < 3:
        res.errors.append("GEOMETRY_UNAVAILABLE: cross stitch needs a closed outline"); return res
    x0, y0, x1, y1 = obj.bbox_mm
    nx, ny = int((x1 - x0) / cell), int((y1 - y0) / cell)
    cells = []
    for j in range(ny):
        row = []
        for i in range(nx):
            cx, cy = x0 + i * cell, y0 + j * cell
            corners = [(cx, cy), (cx + cell, cy), (cx, cy + cell), (cx + cell, cy + cell)]
            if all(point_in_region(c, obj.geometry, obj.holes_mm) for c in corners):
                row.append((cx, cy))
        if row:
            cells.append(row)
    if not cells:
        res.errors.append(f"CROSS_STITCH_NO_CELLS: region smaller than one {cell:.2f}mm cell (count {count}/in)"); return res
    pts = []
    for r, row in enumerate(cells):  # two-pass row method: "/" left->right, then "\\" right->left
        fwd = []
        for (cx, cy) in row:
            fwd += [(cx, cy + cell), (cx + cell, cy)]          # bottom-left -> top-right
        back = []
        for (cx, cy) in reversed(row):
            back += [(cx, cy), (cx + cell, cy + cell)][::-1]   # bottom-right -> top-left, i.e. "\\"
        seq = fwd + back
        if r % 2:
            seq = seq[::-1]
        pts += seq
    res.parts.append(StitchPart("cover:cross_stitch", pts, "cover"))
    res.warnings.append(f"CROSS_STITCH_TRAVEL: connecting stitches between cells are part of the row method ({len(cells)} rows, {sum(len(c) for c in cells)} cells)")
    return res


def split_gradient(obj: EmbroideryObject, steps: int, colors: list[str], axis_deg: float = 90.0,
                   density_range: tuple[float, float] = (0.40, 0.40), canvas_mm=None):
    """Controlled shading: split a region into `steps` bands across `axis_deg`, each with its own thread colour
    and (optionally) its own row spacing. Returns (sub_objects, report). Not a photographic gradient."""
    import cv2
    if steps < 2 or len(colors) not in (1, steps):
        raise ValueError("gradient needs steps>=2 and either 1 or `steps` colours")
    cm = canvas_mm or (obj.bbox_mm[2] + 5, obj.bbox_mm[3] + 5)
    scale = min(10.0, 800.0 / max(cm))
    size = (int(math.ceil(cm[0] * scale)) + 4, int(math.ceil(cm[1] * scale)) + 4)
    m = rasterize(obj.geometry, obj.holes_mm, scale, size) > 0
    ys, xs = np.mgrid[0:size[1], 0:size[0]]
    a = math.radians(axis_deg)
    proj = (xs / scale) * math.cos(a) + (ys / scale) * math.sin(a)
    lo, hi = proj[m].min(), proj[m].max()
    subs = []
    for k in range(steps):
        band = m & (proj >= lo + (hi - lo) * k / steps) & (proj <= lo + (hi - lo) * (k + 1) / steps + (0.01 if k == steps - 1 else 0.0))
        cs, hier = cv2.findContours(band.astype(np.uint8) * 255, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        for c, h in zip(cs, hier[0] if hier is not None else []):
            if h[3] != -1 or cv2.contourArea(c) < 4 * scale * scale:
                continue
            c = cv2.approxPolyDP(c, 0.5, True)
            ring = [(float(p[0][0]) / scale, float(p[0][1]) / scale) for p in c]
            xs_, ys_ = [p[0] for p in ring], [p[1] for p in ring]
            import copy
            so = copy.deepcopy(obj)
            so.object_id = new_id("obj"); so.name = f"{obj.name} band {k+1}"; so.geometry = ring; so.holes_mm = []
            so.bbox_mm = (min(xs_), min(ys_), max(xs_), max(ys_)); so.color_group = colors[k] if len(colors) == steps else colors[0]
            so.area_mm2 = polygon_area(ring); so.params = dict(obj.params, gradient_density_mm=round(density_range[0] + (density_range[1] - density_range[0]) * k / (steps - 1), 3), gradient_band=k + 1)
            so.parent_object_id = obj.object_id; so.source += "+gradient_band"
            subs.append(so)
    report = {"steps": steps, "thread_count": len(set(colors)), "density_range_mm": list(density_range),
              "axis_deg": axis_deg, "bands_created": len(subs),
              "note": "controlled banded shading; not a continuous-tone reproduction"}
    return subs, report
