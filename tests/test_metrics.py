"""Tests for embcopilot.metrics — pure numeric estimate functions."""

from embcopilot.config import AnalyzerConfig
from embcopilot.metrics import (
    density_grid,
    estimate_runtime_minutes,
    estimate_thread,
    nearest_neighbour_preview,
)
from embcopilot.models import Design, Record, Segment, StitchKind


def test_estimate_thread_applies_configured_factors():
    cfg = AnalyzerConfig(thread_factor=1.2, bobbin_factor=0.5)
    thread_m, bobbin_m = estimate_thread(1000.0, cfg)  # 1000mm needle travel
    assert thread_m == 1000.0 * 1.2 / 1000.0
    assert bobbin_m == thread_m * 0.5


def test_estimate_thread_zero_input():
    cfg = AnalyzerConfig()
    thread_m, bobbin_m = estimate_thread(0.0, cfg)
    assert thread_m == 0.0
    assert bobbin_m == 0.0


def test_estimate_runtime_accounts_for_trims_and_colors():
    cfg = AnalyzerConfig(spm=1000, trim_seconds=1.6, color_change_seconds=12.0)
    minutes = estimate_runtime_minutes(stitches=1000, estimated_trims=2, colors=3, cfg=cfg)
    expected = 1000 / 1000 + 2 * 1.6 / 60.0 + 2 * 12.0 / 60.0
    assert abs(minutes - expected) < 1e-9


def test_estimate_runtime_single_color_no_change_cost():
    cfg = AnalyzerConfig(spm=1000)
    minutes = estimate_runtime_minutes(stitches=500, estimated_trims=0, colors=1, cfg=cfg)
    assert abs(minutes - 0.5) < 1e-9


def test_nearest_neighbour_preview_orders_closest_first():
    # Three segments on a line: start at 0, targets at 100 and 10.
    # A sane nearest-neighbour walk should visit 10 before 100.
    segs = [
        Segment(0, 5, (0, 0), (0, 0), 5.0, (0, 0, 0, 0)),
        Segment(0, 5, (100, 0), (100, 0), 5.0, (100, 0, 100, 0)),
        Segment(0, 5, (10, 0), (10, 0), 5.0, (10, 0, 10, 0)),
    ]
    total, trims_left = nearest_neighbour_preview(segs, walkable_mm=7.0)
    # Optimal order 0 -> 10 -> 100 costs 10 + 90 = 100.
    assert total == 100.0
    assert trims_left == 2  # both the 10mm and 90mm hops exceed walkable_mm


def test_nearest_neighbour_preview_single_segment_no_travel():
    segs = [Segment(0, 5, (0, 0), (10, 0), 5.0, (0, 0, 10, 0))]
    total, trims_left = nearest_neighbour_preview(segs, walkable_mm=7.0)
    assert total == 0.0
    assert trims_left == 0


def test_density_grid_skips_jump_spans():
    # A jump followed by a stitch must not be rasterised as if thread was
    # laid across the jump.
    recs = [
        Record(0, 0, 0, 0, StitchKind.STITCH),
        Record(500, 500, 500, 500, StitchKind.JUMP),  # 50mm jump
        Record(510, 500, 10, 0, StitchKind.STITCH),  # 1mm real stitch after landing
        Record(510, 500, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    cells = density_grid(d, grid_mm=1.0)
    total_thread = sum(cells.values())
    # Only the 1mm post-jump stitch should be rasterised, not the 50mm jump.
    assert abs(total_thread - 1.0) < 0.05


def test_density_grid_empty_design():
    d = Design(records=[Record(0, 0, 0, 0, StitchKind.END)])
    cells = density_grid(d, grid_mm=1.0)
    assert cells == {}
