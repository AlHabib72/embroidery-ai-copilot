"""Tests for embcopilot.analyze — building an Analysis from a Design."""

from embcopilot.analyze import analyze
from embcopilot.config import AnalyzerConfig
from embcopilot.models import Design, Record, StitchKind


def _rec(x, y, dx, dy, kind=StitchKind.STITCH):
    return Record(x, y, dx, dy, kind)


def test_basic_counts_and_dimensions():
    # A 10mm x 10mm square, four stitches, one colour, no jumps.
    recs = [
        _rec(0, 0, 0, 0),
        _rec(100, 0, 100, 0),
        _rec(100, 100, 0, 100),
        _rec(0, 100, -100, 0),
        _rec(0, 100, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs, header={"LA": "SQUARE"})
    a = analyze(d, AnalyzerConfig())

    assert a.stitches == 4
    assert a.colors == 1
    assert a.jumps == 0
    assert round(a.width_mm, 1) == 10.0
    assert round(a.height_mm, 1) == 10.0
    assert a.name == "SQUARE"


def test_color_change_increments_color_count_and_splits_blocks():
    recs = [
        _rec(0, 0, 0, 0),
        _rec(10, 0, 10, 0),
        _rec(10, 0, 0, 0, StitchKind.COLOR_CHANGE),
        _rec(20, 0, 10, 0),
        _rec(20, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d)
    assert a.colors == 2
    assert len(a.color_blocks) == 2
    assert a.color_blocks[0]["stitches"] == 2
    assert a.color_blocks[1]["stitches"] == 1


def test_jump_never_counted_as_a_stitch():
    recs = [
        _rec(0, 0, 0, 0),
        _rec(50, 50, 50, 50, StitchKind.JUMP),
        _rec(60, 50, 10, 0),
        _rec(60, 50, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d)
    assert a.stitches == 2  # only the two STITCH records
    assert a.jumps == 1


def test_tiny_stitch_detection_and_bbox():
    cfg = AnalyzerConfig(tiny_stitch_mm=0.3, short_stitch_mm=0.5)
    recs = [
        _rec(0, 0, 0, 0),
        _rec(2, 0, 2, 0),  # 0.2mm -> tiny AND short
        _rec(200, 0, 198, 0),  # long jump-free stitch, not tiny
        _rec(200, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d, cfg)
    assert a.tiny_stitches == 1
    assert a.short_stitches == 1  # tiny is a subset of short
    assert a.tiny_stitch_bbox == (0.2, 0.0, 0.2, 0.0)


def test_long_stitch_detection_and_satin_ceiling():
    cfg = AnalyzerConfig(long_stitch_mm=10.0, max_satin_mm=12.0)
    recs = [
        _rec(0, 0, 0, 0),
        _rec(150, 0, 150, 0),  # 15mm, exceeds long_stitch_mm and max_satin_mm
        _rec(150, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d, cfg)
    assert a.long_stitches == 1
    assert a.stitch_length_max_mm == 15.0
    long_issue = next(i for i in a.issues if i.code == "long_stitches")
    assert long_issue.severity == "critical"  # over the satin ceiling


def test_segment_reconstruction_breaks_on_jump_and_color_change():
    recs = [
        _rec(0, 0, 0, 0),
        _rec(10, 0, 10, 0),
        _rec(50, 0, 40, 0, StitchKind.JUMP),
        _rec(60, 0, 10, 0),
        _rec(70, 0, 10, 0),
        _rec(70, 0, 0, 0, StitchKind.COLOR_CHANGE),
        _rec(80, 0, 10, 0),
        _rec(90, 0, 10, 0),
        _rec(90, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d)
    # segment 1: two stitches before the jump; segment 2: two after the jump
    # (before colour change); segment 3: two after the colour change.
    assert len(a.segments) == 3
    assert a.segments[0].stitches == 2
    assert a.segments[1].stitches == 2
    assert a.segments[1].color_index == 0
    assert a.segments[2].color_index == 1


def test_sequin_counted_separately_not_as_stitch():
    recs = [
        _rec(0, 0, 0, 0),
        _rec(10, 0, 10, 0, StitchKind.SEQUIN),
        _rec(20, 0, 10, 0),
        _rec(20, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d)
    assert a.sequins == 1
    assert a.stitches == 2


def test_empty_design_does_not_crash():
    d = Design(records=[_rec(0, 0, 0, 0, StitchKind.END)])
    a = analyze(d)
    assert a.stitches == 0
    assert a.width_mm == 0.0
    assert a.score >= 0


def test_dense_fill_flags_a_density_issue():
    cfg = AnalyzerConfig(density_warning=0.5, density_critical=1.0, grid_mm=1.0)
    # A tight zig-zag packed entirely inside one 1mm x 1mm grid cell:
    # 9 full-width (1mm) rows plus 8 short (0.1mm) connecting steps, roughly
    # 9.8mm of thread inside ~1mm^2 -> should clear both thresholds.
    recs = []
    x = y = 0
    recs.append(_rec(0, 0, 0, 0))
    going_right = True
    for row in range(9):
        nx = 10 if going_right else 0
        recs.append(_rec(nx, y, nx - x, 0))
        x = nx
        going_right = not going_right
        if row < 8:
            recs.append(_rec(x, y + 1, 0, 1))
            y += 1
    recs.append(_rec(x, y, 0, 0, StitchKind.END))
    d = Design(records=recs)
    a = analyze(d, cfg)
    assert a.dense_cells > 0 or a.critical_cells > 0


def test_config_used_is_recorded_on_analysis():
    cfg = AnalyzerConfig(spm=850, fabric="cap")
    d = Design(records=[_rec(0, 0, 0, 0, StitchKind.END)])
    a = analyze(d, cfg)
    assert a.config_used["spm"] == 850
    assert a.fabric_preset == "cap"
    assert a.analyzer_version  # non-empty


# ----------------------------------------------------------------------
# Phase 2 audit fixes: jump travel is a SUM of path lengths (not net
# displacement of the combined vector), segment length includes the first
# stitch, and colour blocks are consistent even at the edges.
# ----------------------------------------------------------------------


def test_jump_travel_is_total_path_not_net_displacement():
    # "5mm right, 5mm left, 5mm right" -> 15mm travelled, 5mm net displacement.
    recs = [
        _rec(0, 0, 0, 0),
        _rec(50, 0, 50, 0, StitchKind.JUMP),   # +5mm
        _rec(0, 0, -50, 0, StitchKind.JUMP),   # -5mm
        _rec(50, 0, 50, 0, StitchKind.JUMP),   # +5mm
        _rec(60, 0, 10, 0),
        _rec(60, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d, AnalyzerConfig())
    assert abs(a.travel_actual_mm - 15.0) < 1e-6
    assert a.travel_actual_mm != 5.0  # would be the (wrong) net-displacement value


def test_jump_travel_sums_multiple_separate_jump_runs():
    recs = [
        _rec(0, 0, 0, 0),
        _rec(30, 0, 30, 0, StitchKind.JUMP),  # 3mm
        _rec(40, 0, 10, 0),
        _rec(70, 0, 30, 0, StitchKind.JUMP),  # 3mm
        _rec(80, 0, 10, 0),
        _rec(80, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d, AnalyzerConfig())
    assert abs(a.travel_actual_mm - 6.0) < 1e-6


def test_segment_length_includes_first_stitch_after_a_jump():
    # After the jump lands at (50,0), the first stitch of the new segment
    # moves 2mm to (70,0) -- that 2mm must be counted in the segment length.
    recs = [
        _rec(0, 0, 0, 0),
        _rec(10, 0, 10, 0),
        _rec(50, 0, 40, 0, StitchKind.JUMP),
        _rec(70, 0, 20, 0),  # first stitch of the new segment: 2mm
        _rec(80, 0, 10, 0),  # 1mm
        _rec(80, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d, AnalyzerConfig())
    second_segment = a.segments[1]
    assert abs(second_segment.length_mm - 3.0) < 1e-6  # 2mm + 1mm, not just 1mm


def test_color_blocks_count_matches_colors_even_with_zero_stitch_tail():
    # Colour 2 has stitches, but colour 3 (after the final colour change)
    # never gets a stitch before END -- it must still appear as a 0-stitch block.
    recs = [
        _rec(0, 0, 0, 0),
        _rec(10, 0, 10, 0),
        _rec(10, 0, 0, 0, StitchKind.COLOR_CHANGE),
        _rec(10, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d, AnalyzerConfig())
    assert a.colors == 2
    assert len(a.color_blocks) == 2
    assert a.color_blocks[-1]["stitches"] == 0


def test_color_blocks_on_completely_empty_design():
    d = Design(records=[_rec(0, 0, 0, 0, StitchKind.END)])
    a = analyze(d, AnalyzerConfig())
    assert a.colors == 1
    assert len(a.color_blocks) == 1
    assert a.color_blocks[0]["stitches"] == 0


def test_color_blocks_with_consecutive_color_changes_and_no_stitches_between():
    recs = [
        _rec(0, 0, 0, 0),
        _rec(0, 0, 0, 0, StitchKind.COLOR_CHANGE),
        _rec(0, 0, 0, 0, StitchKind.COLOR_CHANGE),
        _rec(10, 0, 10, 0),
        _rec(10, 0, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    a = analyze(d, AnalyzerConfig())
    assert a.colors == 3
    assert len(a.color_blocks) == 3
    assert a.color_blocks[1]["stitches"] == 0  # the middle, empty colour block
