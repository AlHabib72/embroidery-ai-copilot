"""
Pure numeric estimates: thread/bobbin consumption, production time, travel.

Every function here returns an ESTIMATE derived from stitch geometry alone.
None of these numbers are measured; real values depend on thread, fabric,
tension and machine settings that a stitch file does not record. Read each
docstring for the assumption behind the number before quoting it as fact.
"""

from __future__ import annotations

import math

from .config import AnalyzerConfig
from .models import Design, Segment, StitchKind


def estimate_thread(needle_length_mm: float, cfg: AnalyzerConfig) -> tuple[float, float]:
    """Estimate upper-thread and bobbin-thread consumption, in metres.

    `needle_length_mm` is the sum of stitch-to-stitch distances (needle
    travel only, no jumps). `cfg.thread_factor` inflates that flat distance
    to account for thread consumed in forming each stitch (take-up lever,
    tie stitches) — it is a configurable multiplier, not a physical
    constant, and the right value varies by thread and stitch type.
    `cfg.bobbin_factor` expresses bobbin thread as a fraction of upper
    thread; the true ratio depends on stitch type (satin pulls more bobbin
    than a run stitch) and cannot be derived from coordinates alone.
    """
    thread_m = needle_length_mm * cfg.thread_factor / 1000.0
    bobbin_m = thread_m * cfg.bobbin_factor
    return thread_m, bobbin_m


def estimate_runtime_minutes(
    stitches: int, estimated_trims: int, colors: int, cfg: AnalyzerConfig
) -> float:
    """Estimated production time.

    Stitch time at the configured machine speed, plus a fixed handling cost
    per estimated trim and per colour change. Real run time also depends on
    machine acceleration limits, hoop repositioning and operator handling —
    none of which are visible in a stitch file — so treat this as a rough
    planning number, not a guaranteed cycle time.
    """
    return (
        stitches / cfg.spm
        + estimated_trims * cfg.trim_seconds / 60.0
        + max(0, colors - 1) * cfg.color_change_seconds / 60.0
    )


def nearest_neighbour_preview(
    segments: list[Segment], walkable_mm: float
) -> tuple[float, int]:
    """Theoretical travel-distance preview for one colour block's segments.

    Greedily re-orders the segments by nearest start/end point, allowing
    each segment to be sewn forwards or reversed. This is a PREVIEW
    calculation only: it does not reorder or rewrite anything, and greedy
    nearest-neighbour is not guaranteed to be the true shortest route
    (finding that exactly is a travelling-salesman problem; a full solve is
    not attempted here). Colour order itself is never changed.

    Returns (theoretical_travel_mm, segments_still_needing_an_estimated_trim).
    """
    if len(segments) < 2:
        return 0.0, 0

    total = 0.0
    trims_left = 0
    remaining = segments[1:]
    cursor = segments[0].end

    while remaining:
        best_i, best_d, best_reversed = 0, float("inf"), False
        for i, seg in enumerate(remaining):
            d_forward = math.dist(cursor, seg.start)
            d_reversed = math.dist(cursor, seg.end)
            if d_forward < best_d:
                best_i, best_d, best_reversed = i, d_forward, False
            if d_reversed < best_d:
                best_i, best_d, best_reversed = i, d_reversed, True
        seg = remaining.pop(best_i)
        total += best_d
        if best_d > walkable_mm:
            trims_left += 1
        cursor = seg.start if best_reversed else seg.end

    return total, trims_left


def density_grid(design: Design, grid_mm: float) -> dict[tuple[int, int], float]:
    """Rasterise stitch-to-stitch thread length onto a grid of `grid_mm` cells.

    This is a geometric proxy for local thread density (mm of thread per
    mm^2 of a cell) — NOT Wilcom's own density setting, which also factors
    in stitch type, underlay and pull compensation that are invisible to a
    plain stitch stream. Use the result to spot hot spots for review, not to
    reproduce an exact density value that was set in the digitizing software.

    Every record's (x, y) is an absolute position, updated regardless of
    kind. Thread is only laid when the CURRENT record is a STITCH — that is
    true whether the record right before it was another stitch, a jump, or
    a colour change, because a stitch record's own movement always
    represents real thread from wherever the needle last was. Gating this
    on "was the previous record also a stitch" (an earlier version of this
    function did) wrongly discarded the very first stitch after every jump,
    undercounting density right where a design resumes sewing.
    """
    cells: dict[tuple[int, int], float] = {}
    last_x = last_y = 0.0
    have_position = False
    g = grid_mm

    for r in design.records:
        x1, y1 = r.x / 10.0, r.y / 10.0

        if r.kind == StitchKind.STITCH and have_position:
            dist = math.hypot(x1 - last_x, y1 - last_y)
            if dist > 0:
                steps = max(1, int(dist / (g * 0.5)))
                per_step = dist / steps
                for s in range(steps):
                    t = (s + 0.5) / steps
                    cx = int((last_x + (x1 - last_x) * t) // g)
                    cy = int((last_y + (y1 - last_y) * t) // g)
                    cells[(cx, cy)] = cells.get((cx, cy), 0.0) + per_step

        last_x, last_y = x1, y1
        have_position = True

    return cells
