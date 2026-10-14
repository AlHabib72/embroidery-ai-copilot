"""
Connector engine.

Decides how to travel between two consecutive objects in a colour block:
trim, jump, or one of the "hide the travel" strategies. Reuses Phase 1's
`metrics.nearest_neighbour_preview` for the ordering question (which object
comes next) and adds the strategy question on top (given this gap, how
should the machine cover it).

CRITICAL DESIGN PRINCIPLE from the project brief: minimum trims is NOT
always the same as best production quality. A visible travel stitch across
open fabric can look worse than an extra trim — the cost function below
prices that trade-off instead of always minimising trim count.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import ConnectorStrategy


@dataclass(frozen=True)
class ConnectorCostWeights:
    """Configurable cost weights — no single universal optimum (see brief
    section 29). A "quality first" profile would raise visible_travel_weight
    and trim_weight relative to jump_distance_weight; "speed first" would
    do the opposite. These are the balanced-profile defaults."""

    jump_distance_weight: float = 1.0  # cost per mm of jump
    trim_weight: float = 8.0  # flat cost per trim (machine time + tail risk)
    visible_travel_weight: float = 3.0  # flat cost if travel crosses open fabric
    direction_penalty_weight: float = 1.0  # cost if travel direction fights the next object's start

    @classmethod
    def from_profile(cls, profile) -> "ConnectorCostWeights":
        """Weights scaled by a ProductionProfile (audit fix: profiles used to
        be ignored). quality_first prices visible travel higher and trims
        lower; speed_first does the opposite."""
        from .profiles import get_profile

        p = get_profile(profile)
        base = cls()
        return cls(
            jump_distance_weight=base.jump_distance_weight * p.jump_distance_scale,
            trim_weight=base.trim_weight * p.trim_weight_scale,
            visible_travel_weight=base.visible_travel_weight * p.visible_travel_scale,
            direction_penalty_weight=base.direction_penalty_weight,
        )


@dataclass
class ConnectorDecision:
    strategy: str
    cost: float
    reason: str


def _crosses_open_fabric(gap_mm: float, hidden_travel_max_mm: float) -> bool:
    """A coarse proxy: a gap longer than the "can plausibly hide under a
    future object" distance is assumed to cross open, visible fabric.
    This is a heuristic threshold, not a check against actual design
    geometry (that would require knowing what, if anything, later covers
    the gap) — see README for this engine's current limitations."""
    return gap_mm > hidden_travel_max_mm


def decide_connector(
    gap_mm: float,
    hidden_travel_max_mm: float = 4.0,
    weights: ConnectorCostWeights | None = None,
    direction_conflict: bool = False,
) -> ConnectorDecision:
    """Price each candidate strategy for one inter-object gap and return the
    cheapest. Every candidate's cost is computed (not just the winner) so
    the caller can show why an alternative wasn't chosen if asked.
    """
    w = weights or ConnectorCostWeights()
    candidates: dict[str, float] = {}

    # TRIM: always available, flat cost plus a little for the trim's own tail.
    candidates[ConnectorStrategy.TRIM.value] = w.trim_weight

    # HIDDEN_TRAVEL: only realistic for a short gap (has to plausibly be
    # covered by something sewn later); cheap when it applies. Considered
    # before JUMP below so that on an exact cost tie (e.g. a zero-length
    # gap) the strategy that deliberately hides the travel wins over a
    # bare jump, which is the more production-sensible default.
    if gap_mm <= hidden_travel_max_mm:
        candidates[ConnectorStrategy.HIDDEN_TRAVEL.value] = w.jump_distance_weight * gap_mm * 0.5

    # JUMP: cheap for a short gap, scales with distance; no trim cost, but
    # a jump that's clearly crossing open fabric still gets the visible
    # travel penalty on top.
    jump_cost = w.jump_distance_weight * gap_mm
    if _crosses_open_fabric(gap_mm, hidden_travel_max_mm):
        jump_cost += w.visible_travel_weight
    candidates[ConnectorStrategy.JUMP.value] = jump_cost

    if direction_conflict:
        for key in candidates:
            candidates[key] += w.direction_penalty_weight

    best_strategy = min(candidates, key=candidates.get)
    best_cost = candidates[best_strategy]

    reason_bits = [f"gap {gap_mm:.1f}mm"]
    if best_strategy == ConnectorStrategy.TRIM.value:
        reason_bits.append("trimming beats a jump/hidden-travel here")
    elif best_strategy == ConnectorStrategy.HIDDEN_TRAVEL.value:
        reason_bits.append(f"short enough (<= {hidden_travel_max_mm}mm) to plausibly hide under later stitching")
    elif best_strategy == ConnectorStrategy.JUMP.value and _crosses_open_fabric(gap_mm, hidden_travel_max_mm):
        reason_bits.append("cheaper than a trim even though it likely crosses open fabric — flagged for review")
    if direction_conflict:
        reason_bits.append("direction conflict penalty applied to all options equally")

    return ConnectorDecision(strategy=best_strategy, cost=round(best_cost, 2), reason="; ".join(reason_bits))


def plan_connectors(
    gaps_mm: list[float],
    hidden_travel_max_mm: float = 4.0,
    weights: ConnectorCostWeights | None = None,
) -> list[ConnectorDecision]:
    """Decide a strategy for a whole list of consecutive gaps (e.g. one per
    hop in a nearest-neighbour object ordering from metrics.py)."""
    return [decide_connector(g, hidden_travel_max_mm, weights) for g in gaps_mm]


# ---------------------------------------------------------------------------
# Connector V2: geometry-aware routing (Phase 10)
# ---------------------------------------------------------------------------
@dataclass
class RouteDecision:
    strategy: str  # TRIM_JUMP | JUMP | HIDDEN_STITCH
    cost: float
    distance_mm: float
    visible_fraction: float  # share of the travel path lying over open fabric
    candidates: dict
    reason: str
    path: list | None = None


def route_connector(exit_pt, entry_pt, covered_by_next, covered_anywhere, weights: ConnectorCostWeights | None = None,
                    max_jump_mm: float = 12.0, samples: int = 24, routed_path=None) -> RouteDecision:
    """Choose how to get from one object's real EXIT point to the next
    object's real ENTRY point.

    `covered_by_next(x, y)` -> True if that point is inside the next object's
    region (a travel stitch there is hidden under its cover stitches).
    `covered_anywhere(x, y)` -> True if the point is inside ANY object region
    (a jump over already-sewn thread is less visible than over bare fabric).

    Candidates and their honest costs:
      HIDDEN_STITCH: only if >=95% of the straight path is under the next
        object; cost ~ half the distance (extra stitches, no trim).
      JUMP: no trim; distance cost + visible_travel penalty scaled by the
        fraction of the path over open fabric; risk penalty above max_jump.
      TRIM_JUMP: trim cost + distance cost (the jump is trimmed away after
        sewing, so no visible-travel penalty).
    """
    w = weights or ConnectorCostWeights()
    ex, ey = exit_pt; nx, ny = entry_pt
    d = ((nx - ex) ** 2 + (ny - ey) ** 2) ** 0.5
    ts = [i / samples for i in range(samples + 1)]
    pts = [(ex + (nx - ex) * t, ey + (ny - ey) * t) for t in ts]
    hidden = sum(1 for p in pts if covered_by_next(*p)) / len(pts)
    open_frac = sum(1 for p in pts if not covered_anywhere(*p)) / len(pts)
    cand = {}
    if d <= 1e-6:
        cand["HIDDEN_STITCH"] = 0.0
    elif hidden >= 0.95:
        cand["HIDDEN_STITCH"] = w.jump_distance_weight * d * 0.5
    if routed_path is not None and len(routed_path) >= 2 and "HIDDEN_STITCH" not in cand:
        plen = sum(((routed_path[i + 1][0] - routed_path[i][0]) ** 2 + (routed_path[i + 1][1] - routed_path[i][1]) ** 2) ** 0.5 for i in range(len(routed_path) - 1))
        cand["HIDDEN_ROUTED"] = w.jump_distance_weight * plen * 0.5
    jump = w.jump_distance_weight * d + w.visible_travel_weight * open_frac
    if d > max_jump_mm:
        jump += (d - max_jump_mm) * 2.0  # long unsplit jumps risk thread pull-out
    cand["JUMP"] = jump
    cand["TRIM_JUMP"] = w.trim_weight + w.jump_distance_weight * d
    best = min(cand, key=cand.get)
    why = f"{d:.1f}mm, {open_frac*100:.0f}% over open fabric, {hidden*100:.0f}% under next object"
    rd = RouteDecision(best, round(cand[best], 3), round(d, 3), round(open_frac, 3),
                       {k: round(v, 3) for k, v in cand.items()}, why)
    rd.path = routed_path if best == "HIDDEN_ROUTED" else None
    return rd


# ---------------------------------------------------------------------------
# Obstacle-aware hidden routing (Phase 15): A* over the area that will be
# covered LATER, so travel stitches end up hidden under stitching sewn after them.
# ---------------------------------------------------------------------------
def route_hidden_path(exit_pt, entry_pt, allowed_mask, scale: float, grid_per_mm: float = 2.0,
                      max_detour: float = 2.5, max_abs_detour_mm: float = 6.0, endpoint_mask=None):
    """A* (8-connected) restricted to `allowed_mask` (bool array at `scale` px/mm, y down).
    Returns a polyline [(x,y)...] in mm from exit to entry, or None if no hidden route exists
    within max_detour x straight distance (+ max_abs_detour_mm)."""
    import heapq
    import math

    import cv2
    import numpy as np

    h, w = allowed_mask.shape
    f = grid_per_mm / scale
    gw, gh = max(2, int(w * f)), max(2, int(h * f))
    g = cv2.resize(allowed_mask.astype(np.uint8), (gw, gh), interpolation=cv2.INTER_AREA) > 0
    cell = lambda p: (min(gw - 1, max(0, int(p[0] * grid_per_mm))), min(gh - 1, max(0, int(p[1] * grid_per_mm))))  # noqa: E731
    s, t = cell(exit_pt), cell(entry_pt)
    if endpoint_mask is not None:  # let the route start/finish on the boundary while its body stays in the (eroded) interior
        ge = cv2.resize(endpoint_mask.astype(np.uint8), (gw, gh), interpolation=cv2.INTER_AREA) > 0
        for c in (s, t):
            y0, y1, x0, x1 = max(0, c[1] - 2), min(gh, c[1] + 3), max(0, c[0] - 2), min(gw, c[0] + 3)
            g[y0:y1, x0:x1] |= ge[y0:y1, x0:x1]
    g[s[1], s[0]] = True; g[t[1], t[0]] = True
    straight = math.dist(exit_pt, entry_pt)
    limit = (straight * max_detour + max_abs_detour_mm) * grid_per_mm
    openq = [(0.0, 0.0, s)]
    best = {s: 0.0}; prev = {}
    while openq:
        _, d, u = heapq.heappop(openq)
        if u == t:
            break
        if d > best.get(u, 1e18):
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not (dx or dy):
                    continue
                v = (u[0] + dx, u[1] + dy)
                if not (0 <= v[0] < gw and 0 <= v[1] < gh) or not g[v[1], v[0]]:
                    continue
                nd = d + math.hypot(dx, dy)
                if nd > limit or nd >= best.get(v, 1e18):
                    continue
                best[v] = nd; prev[v] = u
                heapq.heappush(openq, (nd + math.hypot(t[0] - v[0], t[1] - v[1]), nd, v))
    if t not in prev and s != t:
        return None
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    path.reverse()
    pts = [((c[0] + 0.5) / grid_per_mm, (c[1] + 0.5) / grid_per_mm) for c in path]
    pts[0], pts[-1] = tuple(exit_pt), tuple(entry_pt)
    def hidden(poly) -> bool:
        """every point along `poly` (every 0.3mm) lies inside the allowed area (1px tolerance)"""
        for i in range(len(poly) - 1):
            L = math.dist(poly[i], poly[i + 1])
            for k in range(max(1, int(L / 0.3)) + 1):
                t = k / max(1, int(L / 0.3))
                x, y = poly[i][0] + (poly[i + 1][0] - poly[i][0]) * t, poly[i][1] + (poly[i + 1][1] - poly[i][1]) * t
                px, py = int(round(x * scale)), int(round(y * scale))
                if not allowed_mask[max(0, py - 1):py + 2, max(0, px - 1):px + 2].any():
                    return False
        return True

    if len(pts) > 2:  # simplify to corners, but ONLY if the simplified path is still fully hidden
        ap = cv2.approxPolyDP(np.asarray(pts, np.float32).reshape(-1, 1, 2), 0.35, False)
        simp = [tuple(map(float, p[0])) for p in ap]
        simp[0], simp[-1] = tuple(exit_pt), tuple(entry_pt)
        pts = simp if hidden(simp) else pts
    if not hidden(pts):
        return None
    return pts
