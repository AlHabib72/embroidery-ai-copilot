"""Negative-space / knockout engine + protected travel zones (v0.6).

* ``NegativeSpace``: holes, counters, knockouts - always attached to a parent object.
* ``ProtectedTravelZone``: regions no connector/jump/trim travel may cross (empty centres of badges, holes,
  letter interiors).  QC rule ``CENTER_CROSSING_TRAVEL`` (ERROR by default) uses them.
* ``route_around``: grid A* detour around protected zones (exact zone mask + clearance, line-of-sight smoothed).
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

Point = tuple[float, float]


@dataclass
class NegativeSpace:
    parent_object_id: str
    geometry: list[Point]
    type: str = "hole"            # hole | counter | knockout | cutout
    area_mm2: float = 0.0

    def to_dict(self):
        return {"parent_object_id": self.parent_object_id, "type": self.type, "area_mm2": round(self.area_mm2, 3),
                "points": len(self.geometry)}


@dataclass
class ProtectedTravelZone:
    zone_id: str
    polygon: list[Point]
    kind: str                     # enclosed_empty | hole | counter | user
    reason: str = ""
    allow_override: bool = False  # only the user may set this

    def to_dict(self):
        return {"zone_id": self.zone_id, "kind": self.kind, "reason": self.reason, "allow_override": self.allow_override,
                "points": len(self.polygon)}


def _area(poly):
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def extract_negative_space(objects) -> list[NegativeSpace]:
    out = []
    for o in objects:
        for h in (getattr(o, "holes_mm", None) or []):
            if len(h) >= 3:
                t = "counter" if getattr(o, "category", "") == "text" else "hole"
                out.append(NegativeSpace(o.object_id, [tuple(p) for p in h], t, abs(_area(h))))
    return out


def build_protected_zones(objects, canvas_mm: tuple[float, float], margin_mm: float = 1.0, min_area_mm2: float = 6.0,
                          px_per_mm: float = 4.0, include_holes: bool = False, min_clear_width_mm: float = 10.0) -> list[ProtectedTravelZone]:
    """Enclosed empty regions (not connected to the canvas border and not covered by any object) become zones.
    Object holes become zones only with ``include_holes=True`` (a ring's hole usually contains other objects, so the
    enclosed-empty analysis, which subtracts everything that IS stitched, is the default).  Zones are shrunk by ``margin_mm`` so legitimate edge-walk travel is not flagged."""
    import cv2
    W, H = int(math.ceil(canvas_mm[0] * px_per_mm)) + 4, int(math.ceil(canvas_mm[1] * px_per_mm)) + 4
    cover = np.zeros((H, W), np.uint8)
    for o in objects:
        if getattr(o, "category", "") == "background" or len(o.geometry) < 3:
            continue
        om = np.zeros((H, W), np.uint8)   # outer minus holes, per object, so a ring never "covers" its own hole
        cv2.fillPoly(om, [np.array([(x * px_per_mm + 2, y * px_per_mm + 2) for x, y in o.geometry], np.int32)], 255)
        for h in (getattr(o, "holes_mm", None) or []):
            if len(h) >= 3:
                cv2.fillPoly(om, [np.array([(x * px_per_mm + 2, y * px_per_mm + 2) for x, y in h], np.int32)], 0)
        cover |= om
    empty = (cover == 0).astype(np.uint8)
    n, lab = cv2.connectedComponents(empty, connectivity=4)
    border = set(np.unique(np.r_[lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]]).tolist())
    zones: list[ProtectedTravelZone] = []
    k = int(round(margin_mm * px_per_mm))
    for lbl in range(1, n):
        if lbl in border:
            continue
        m = (lab == lbl).astype(np.uint8)
        if m.sum() / (px_per_mm ** 2) < min_area_mm2:
            continue
        if min_clear_width_mm > 0:
            # keep only WIDE open areas (centre of a badge, big counters).  Narrow gaps between letters are normal
            # inter-object travel, not protected space: morphological opening removes them.
            r = max(1, int(round(min_clear_width_mm * px_per_mm / 2)))
            ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, ker)
            if m.sum() / (px_per_mm ** 2) < min_area_mm2:
                continue
        if k > 0:
            m = cv2.erode(m, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)  # (annular zones keep their outer contour only; holes are objects)
        for c in cs:
            if cv2.contourArea(c) / (px_per_mm ** 2) < min_area_mm2 * 0.5:
                continue
            poly = [((p[0][0] - 2) / px_per_mm, (p[0][1] - 2) / px_per_mm) for p in c]
            zones.append(ProtectedTravelZone(f"zone_empty_{len(zones)+1}", poly, "enclosed_empty",
                                             "enclosed empty region (e.g. centre of a ring); travel must not cross it"))
    if include_holes:
        for ns in extract_negative_space(objects):
            if ns.area_mm2 >= min_area_mm2:
                zones.append(ProtectedTravelZone(f"zone_{ns.type}_{ns.parent_object_id}_{len(zones)+1}", ns.geometry,
                                                 ns.type, f"{ns.type} of {ns.parent_object_id}"))
    return zones


class _ZoneMask:
    def __init__(self, zones, canvas_mm, px_per_mm=4.0):
        import cv2
        self.s = px_per_mm
        W, H = int(math.ceil(canvas_mm[0] * px_per_mm)) + 6, int(math.ceil(canvas_mm[1] * px_per_mm)) + 6
        self.masks = {}
        for z in zones:
            m = np.zeros((H, W), np.uint8)
            cv2.fillPoly(m, [np.array([(x * px_per_mm + 3, y * px_per_mm + 3) for x, y in z.polygon], np.int32)], 1)
            self.masks[z.zone_id] = m

    def inside(self, zid, x, y) -> bool:
        m = self.masks[zid]
        i, j = int(round(y * self.s + 3)), int(round(x * self.s + 3))
        return 0 <= i < m.shape[0] and 0 <= j < m.shape[1] and m[i, j] > 0


@dataclass
class Crossing:
    stitch_index: int
    start: Point
    end: Point
    zone_id: str
    inside_mm: float
    command: str
    trimmed: bool = False   # thread was cut before this move -> no thread is dragged across the zone

    def to_dict(self):
        return {"trimmed": self.trimmed, "stitch_index": self.stitch_index, "from": [round(v, 2) for v in self.start], "to": [round(v, 2) for v in self.end],
                "zone": self.zone_id, "inside_mm": round(self.inside_mm, 2), "command": self.command}


def _is_travel(prev, cur) -> bool:
    return cur.command == "JUMP" or (cur.command == "STITCH" and cur.part.startswith("connector"))


def _preceded_by_trim(st, i) -> bool:
    """True when the thread was cut before the travel move at index i (TRIM, colour change or stop): no thread is dragged."""
    k = i - 1
    while k >= 0 and st[k].command in ("JUMP", "TRIM", "COLOR_CHANGE", "STOP"):
        if st[k].command != "JUMP":
            return True
        k -= 1
    return False


def find_protected_crossings(plan, zones: list[ProtectedTravelZone], step_mm: float = 0.25,
                             include_trimmed: bool = False, min_inside_mm: float = 1.0) -> list[Crossing]:
    """Every JUMP / connector segment that passes through a protected zone (override-allowed zones are skipped).
    A jump that follows a TRIM drags no thread across the fabric, so it is excluded unless ``include_trimmed``."""
    active = [z for z in zones if not z.allow_override]
    if not active:
        return []
    zm = _ZoneMask(active, plan.canvas_mm)
    out: list[Crossing] = []
    st = plan.stitches
    for i in range(1, len(st)):
        a, b = st[i - 1], st[i]
        if not _is_travel(a, b) or a.command in ("COLOR_CHANGE", "STOP", "END"):
            continue
        trimmed = b.command == "JUMP" and _preceded_by_trim(st, i)
        if trimmed and not include_trimmed:
            continue
        L = math.dist((a.x, a.y), (b.x, b.y))
        if L < 1e-6:
            continue
        n = max(1, int(L / step_mm))
        for z in active:
            inside = sum(1 for k in range(n + 1) if zm.inside(z.zone_id, a.x + (b.x - a.x) * k / n, a.y + (b.y - a.y) * k / n))
            if inside and inside * L / (n + 1) >= min_inside_mm:   # ignore sub-mm grazes (raster/contour tolerance)
                out.append(Crossing(i, (a.x, a.y), (b.x, b.y), z.zone_id, inside * L / (n + 1), b.command, trimmed))
    return out


# --------------------------------------------------------------------------- routing
def route_around(p: Point, q: Point, zones: list[ProtectedTravelZone], clearance_mm: float = 0.8,
                 canvas_mm: tuple[float, float] | None = None, px_per_mm: float = 2.0) -> list[Point] | None:
    """Shortest 8-connected grid path p->q avoiding every zone (exact zone mask + clearance), then
    line-of-sight smoothed.  Returns waypoints INCLUDING p and q, or None if no path exists.
    Endpoints that sit inside a zone are released locally (the travel must be able to start/end there)."""
    import cv2
    pts = [p, q] + [v for z in zones for v in z.polygon]
    x0 = min(v[0] for v in pts) - 6.0; y0 = min(v[1] for v in pts) - 6.0
    x1 = max(v[0] for v in pts) + 6.0; y1 = max(v[1] for v in pts) + 6.0
    if canvas_mm:
        x0, y0 = min(x0, 0.0), min(y0, 0.0); x1, y1 = max(x1, canvas_mm[0]), max(y1, canvas_mm[1])
    W, H = int((x1 - x0) * px_per_mm) + 1, int((y1 - y0) * px_per_mm) + 1
    to_px = lambda v: (int(round((v[0] - x0) * px_per_mm)), int(round((v[1] - y0) * px_per_mm)))  # noqa: E731
    blocked = np.zeros((H, W), np.uint8)
    for z in zones:
        cv2.fillPoly(blocked, [np.array([to_px(v) for v in z.polygon], np.int32)], 1)
    k = max(1, int(round(clearance_mm * px_per_mm)))
    blocked = cv2.dilate(blocked, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    for v in (p, q):  # release a small disc around the endpoints
        cv2.circle(blocked, to_px(v), 2 * k + 2, 0, -1)
    s_, g_ = to_px(p), to_px(q)
    if not (0 <= s_[0] < W and 0 <= s_[1] < H and 0 <= g_[0] < W and 0 <= g_[1] < H):
        return None
    open_ = blocked == 0
    def line_free(a, b):
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1
        for t in range(n + 1):
            x = int(round(a[0] + (b[0] - a[0]) * t / n)); y = int(round(a[1] + (b[1] - a[1]) * t / n))
            if not open_[y, x]:
                return False
        return True
    if line_free(s_, g_):
        return [p, q]
    dist = {s_: 0.0}; prev: dict = {}; heap = [(math.dist(s_, g_), 0.0, s_)]
    nbrs = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]
    found = False
    while heap:
        _, d, u = heapq.heappop(heap)
        if u == g_:
            found = True; break
        if d > dist.get(u, 1e18):
            continue
        for dx, dy, c in nbrs:
            v = (u[0] + dx, u[1] + dy)
            if 0 <= v[0] < W and 0 <= v[1] < H and open_[v[1], v[0]]:
                nd = d + c
                if nd < dist.get(v, 1e18):
                    dist[v] = nd; prev[v] = u
                    heapq.heappush(heap, (nd + math.dist(v, g_), nd, v))
    if not found:
        return None
    path = [g_]
    while path[-1] != s_:
        path.append(prev[path[-1]])
    path.reverse()
    # line-of-sight smoothing
    sm = [path[0]]; i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1 and not line_free(path[i], path[j]):
            j -= 1
        sm.append(path[j]); i = j
    out = [(x0 + a / px_per_mm, y0 + b / px_per_mm) for a, b in sm]
    out[0], out[-1] = p, q
    return out


@dataclass
class RerouteReport:
    original_strategy: str
    repair_strategy: str
    reason: str
    fixed: int
    unfixable: int
    extra_travel_mm: float

    def to_dict(self):
        return self.__dict__.copy()


def reroute_protected_crossings(plan, zones: list[ProtectedTravelZone], clearance_mm: float = 0.8):
    """Return (new_plan, RerouteReport).  The input plan is never mutated; intent change is reported, not silent."""
    import copy
    from .stitch import Stitch
    new = copy.copy(plan)
    cr = {c.stitch_index: c for c in find_protected_crossings(plan, zones)}
    out, fixed, bad, extra = [], 0, 0, 0.0
    for i, s in enumerate(plan.stitches):
        if i in cr:
            a = plan.stitches[i - 1]
            path = route_around((a.x, a.y), (s.x, s.y), [z for z in zones if not z.allow_override], clearance_mm, plan.canvas_mm)
            if path and len(path) > 2:
                for w in path[1:-1]:
                    out.append(Stitch(w[0], w[1], s.command, s.color, s.object_id, s.layer, s.part))
                extra += sum(math.dist(path[k], path[k + 1]) for k in range(len(path) - 1)) - math.dist((a.x, a.y), (s.x, s.y))
                fixed += 1
            else:
                bad += 1
        out.append(s)
    new.stitches = out
    return new, RerouteReport("direct travel", "grid-A* detour around protected zones (line-of-sight smoothed)",
                              "CENTER_CROSSING_TRAVEL", fixed, bad, round(extra, 2))
