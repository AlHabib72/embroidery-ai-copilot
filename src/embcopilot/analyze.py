"""
Phase 1 analysis engine: turns a parsed Design into a full Analysis.

This module only orchestrates — the actual numeric work lives in metrics.py
and the QC judgement calls live in rules.py, so each can be read and tested
on its own.
"""

from __future__ import annotations

import datetime as _dt

from . import __version__
from .config import AnalyzerConfig
from .metrics import density_grid, estimate_runtime_minutes, estimate_thread, nearest_neighbour_preview
from .models import Analysis, Design, Record, Segment, StitchKind
from . import rules as _rules


def analyze(design: Design, cfg: AnalyzerConfig | None = None) -> Analysis:
    cfg = cfg or AnalyzerConfig()

    a = Analysis(
        name=design.header.get("LA", "").strip() or "Untitled",
        source=design.source,
        analyzer_version=__version__,
        fabric_preset=cfg.fabric,
        generated_at=_dt.datetime.now().isoformat(timespec="seconds"),
        config_used=cfg.as_dict(),
    )

    a.width_mm, a.height_mm = design.size_mm()
    a.stitches = sum(1 for r in design.records if r.kind == StitchKind.STITCH)
    a.colors = sum(1 for r in design.records if r.kind == StitchKind.COLOR_CHANGE) + 1

    _stitch_length_stats(design, a, cfg)
    _reconstruct_segments(design, a, cfg)

    thread_m, bobbin_m = estimate_thread(a.needle_length_mm, cfg)
    a.thread_metres = thread_m
    a.bobbin_metres = bobbin_m

    _density(design, a, cfg)
    _travel_preview(a, cfg)

    a.runtime_minutes = estimate_runtime_minutes(
        a.stitches, a.estimated_trims, a.colors, cfg
    )

    a.issues = _rules.evaluate(a, cfg)
    a.score, a.grade = _rules.score(a.issues)
    return a


# ----------------------------------------------------------------------


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _stitch_length_stats(design: Design, a: Analysis, cfg: AnalyzerConfig) -> None:
    """Stitch-length distribution plus where the outliers are, in one pass."""
    lengths: list[float] = []
    short_pts: list[tuple[float, float]] = []
    tiny_pts: list[tuple[float, float]] = []
    long_pts: list[tuple[float, float]] = []

    for r in design.records:
        if r.kind != StitchKind.STITCH or not (r.dx or r.dy):
            continue
        length = r.length_mm
        lengths.append(length)
        pt = (r.x / 10.0, r.y / 10.0)
        if length < cfg.short_stitch_mm:
            short_pts.append(pt)
        if length < cfg.tiny_stitch_mm:
            tiny_pts.append(pt)
        if length > cfg.long_stitch_mm:
            long_pts.append(pt)

    a.needle_length_mm = sum(lengths)
    if lengths:
        a.stitch_length_avg_mm = sum(lengths) / len(lengths)
        a.stitch_length_max_mm = max(lengths)
    a.short_stitches = len(short_pts)
    a.tiny_stitches = len(tiny_pts)
    a.long_stitches = len(long_pts)
    a.short_stitch_bbox = _bbox(short_pts)
    a.tiny_stitch_bbox = _bbox(tiny_pts)
    a.long_stitch_bbox = _bbox(long_pts)


def _reconstruct_segments(design: Design, a: Analysis, cfg: AnalyzerConfig) -> None:
    """Split the record stream into segments (inferred objects) and jump runs.

    See models.Segment for why "segment" and "Wilcom object" are not
    guaranteed to be the same count.
    """
    color = 0
    cur: list[Record] = []
    jump_run: list[Record] = []
    color_counts: dict[int, int] = {}

    def flush_segment() -> None:
        nonlocal cur
        if len(cur) >= 2:
            xs = [r.x / 10.0 for r in cur]
            ys = [r.y / 10.0 for r in cur]
            a.segments.append(
                Segment(
                    color_index=color,
                    stitches=len(cur),
                    start=(xs[0], ys[0]),
                    end=(xs[-1], ys[-1]),
                    # BUGFIX (Phase 2 audit): this used to sum cur[1:], silently
                    # dropping the segment's very first stitch — the movement
                    # from wherever the needle landed (after a jump/colour
                    # change) to the first needle penetration of this segment.
                    # That is real, physical thread and belongs in the total.
                    length_mm=sum(r.length_mm for r in cur),
                    bbox=(min(xs), min(ys), max(xs), max(ys)),
                )
            )
        cur = []

    def flush_jumps() -> None:
        nonlocal jump_run
        if not jump_run:
            return
        a.jumps += len(jump_run)
        # BUGFIX (Phase 2 audit): this used to sum every jump's (dx, dy) and
        # take the magnitude of the COMBINED vector — i.e. net displacement
        # of the whole jump run, not total distance travelled. A run of
        # "5mm right, 5mm left, 5mm right" is 15mm of actual machine travel
        # but only 5mm of net displacement; summing vectors first reported
        # the latter. Summing each jump's own length is the physically
        # correct total, and is what both the trim heuristic and the
        # travel-estimate metric should use.
        total_path_mm = sum(r.length_mm for r in jump_run)
        # Heuristic only: DST carries no explicit trim command. A long total
        # jump path, or the classic multi-jump run some digitizing software
        # emits before a trim, is treated as "the machine probably trimmed
        # here". Real trim behaviour depends on machine settings not visible
        # in the file — see rules.rule_estimated_trims for how this is reported.
        if total_path_mm > cfg.machine_trim_mm or len(jump_run) >= 3:
            a.estimated_trims += 1
        a.travel_actual_mm += total_path_mm
        jump_run = []

    for r in design.records:
        if r.kind == StitchKind.COLOR_CHANGE:
            flush_jumps()
            flush_segment()
            color_counts.setdefault(color, 0)
            color += 1
        elif r.kind == StitchKind.JUMP:
            flush_segment()
            jump_run.append(r)
        elif r.kind == StitchKind.SEQUIN:
            a.sequins += 1
        elif r.kind == StitchKind.STITCH:
            flush_jumps()
            cur.append(r)
            color_counts[color] = color_counts.get(color, 0) + 1

    flush_jumps()
    flush_segment()

    # BUGFIX (Phase 2 audit): color_counts only gained an entry for a colour
    # index when a stitch was seen for it, OR via the setdefault right
    # before advancing past it on a colour change. That left two edge cases
    # broken: a file with zero stitches at all (color_counts stays {} even
    # though a.colors == 1), and the FINAL colour block when it has zero
    # stitches (no colour change follows it, so nothing ever registers it).
    # Ensure every colour index from 0 up to the last one always appears,
    # so len(color_blocks) always equals a.colors.
    for c in range(color + 1):
        color_counts.setdefault(c, 0)

    a.color_blocks = [
        {
            "index": i + 1,
            "stitches": n,
            "segments": sum(1 for s in a.segments if s.color_index == i),
            "share_pct": round(100.0 * n / a.stitches, 1) if a.stitches else 0.0,
        }
        for i, n in sorted(color_counts.items())
    ]


def _density(design: Design, a: Analysis, cfg: AnalyzerConfig) -> None:
    cells = density_grid(design, cfg.grid_mm)
    area = cfg.grid_mm * cfg.grid_mm
    a.dense_cells = sum(1 for v in cells.values() if v / area > cfg.density_warning)
    a.critical_cells = sum(1 for v in cells.values() if v / area > cfg.density_critical)
    if cells:
        worst_key = max(cells, key=cells.get)
        cx, cy = worst_key
        g = cfg.grid_mm
        a.worst_density_bbox = (cx * g, cy * g, (cx + 1) * g, (cy + 1) * g)


def _travel_preview(a: Analysis, cfg: AnalyzerConfig) -> None:
    by_color: dict[int, list[Segment]] = {}
    for s in a.segments:
        by_color.setdefault(s.color_index, []).append(s)

    total_travel = 0.0
    total_trims = 0
    for segs in by_color.values():
        travel, trims_left = nearest_neighbour_preview(segs, cfg.walkable_mm)
        total_travel += travel
        total_trims += trims_left

    a.travel_theoretical_mm = total_travel
    a.trims_theoretical = total_trims
