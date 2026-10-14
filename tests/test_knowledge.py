"""Tests for embcopilot.knowledge — the rule-based digitizing engine."""

import math

from embcopilot import knowledge
from embcopilot.domain import StitchType, UnderlayType


def test_width_under_1mm_recommends_run():
    stitch, conf, reason = knowledge.recommend_stitch_type(width_mm=0.5, area_mm2=5, elongation=4.0)
    assert stitch == StitchType.RUN.value
    assert "run" in reason.lower()
    assert conf.value > 0


def test_elongated_shape_in_satin_band_recommends_satin():
    stitch, conf, reason = knowledge.recommend_stitch_type(width_mm=4.0, area_mm2=60, elongation=4.0)
    assert stitch == StitchType.SATIN.value
    assert "satin" in reason.lower()


def test_round_blob_in_satin_width_band_recommends_tatami_not_satin():
    # Same width as a satin case, but low elongation (round-ish shape).
    stitch, conf, reason = knowledge.recommend_stitch_type(width_mm=4.0, area_mm2=60, elongation=1.2)
    assert stitch == StitchType.TATAMI.value
    assert "blob" in reason.lower() or "elongat" in reason.lower()


def test_wide_shape_recommends_tatami():
    stitch, conf, reason = knowledge.recommend_stitch_type(width_mm=20.0, area_mm2=400, elongation=2.0)
    assert stitch == StitchType.TATAMI.value


def test_zero_width_is_degenerate_not_a_crash():
    stitch, conf, reason = knowledge.recommend_stitch_type(width_mm=0.0, area_mm2=0, elongation=1.0)
    assert stitch == StitchType.UNDECIDED.value
    assert conf.value < 0.5


def test_density_by_stitch_type():
    satin_density, _ = knowledge.recommend_density_mm(StitchType.SATIN.value)
    tatami_density, _ = knowledge.recommend_density_mm(StitchType.TATAMI.value)
    run_density, _ = knowledge.recommend_density_mm(StitchType.RUN.value)
    assert satin_density == 0.40
    assert tatami_density > satin_density
    assert run_density > tatami_density  # run spacing is a stitch length, much bigger


def test_underlay_scales_with_satin_width():
    narrow, _ = knowledge.recommend_underlay(StitchType.SATIN.value, 2.0)
    mid, _ = knowledge.recommend_underlay(StitchType.SATIN.value, 5.0)
    wide, _ = knowledge.recommend_underlay(StitchType.SATIN.value, 10.0)
    assert narrow == UnderlayType.CENTER_RUN.value
    assert mid == UnderlayType.ZIGZAG.value
    assert wide == UnderlayType.DOUBLE_ZIGZAG.value


def test_underlay_none_for_run_stitch():
    underlay, reason = knowledge.recommend_underlay(StitchType.RUN.value, 0.5)
    assert underlay == UnderlayType.NONE.value


def test_pull_compensation_increases_for_stretch_fabric():
    woven_pull, _ = knowledge.recommend_pull_compensation_mm(StitchType.SATIN.value, 4.0, "woven")
    knit_pull, _ = knowledge.recommend_pull_compensation_mm(StitchType.SATIN.value, 4.0, "knit")
    assert knit_pull > woven_pull


def test_pull_compensation_not_applicable_to_non_column_stitch():
    pull, reason = knowledge.recommend_pull_compensation_mm(StitchType.RUN.value, 0.5, "woven")
    assert pull == 0.0
    assert "not" in reason.lower()


def test_principal_axis_of_a_horizontal_line_is_zero_degrees():
    geometry = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
    angle, elongation = knowledge.principal_axis_deg(geometry)
    assert abs(angle - 0.0) < 1.0
    assert elongation > 10  # strongly elongated


def test_principal_axis_of_a_vertical_line_is_ninety_degrees():
    geometry = [(0.0, 0.0), (0.0, 10.0), (0.0, 20.0), (0.0, 30.0)]
    angle, elongation = knowledge.principal_axis_deg(geometry)
    assert abs(angle - 90.0) < 1.0


def test_principal_axis_degenerate_input_returns_default():
    angle, elongation = knowledge.principal_axis_deg([(1.0, 1.0)])
    assert angle == 0.0
    assert elongation == 1.0


def test_satin_direction_is_perpendicular_to_axis():
    # A horizontal satin column (axis ~0deg) should get a ~90deg stitch direction.
    geometry = [(0.0, 0.0), (10.0, 0.5), (20.0, 0.0), (30.0, -0.5)]
    direction, reason, conf = knowledge.recommend_direction(geometry, StitchType.SATIN.value)
    assert direction is not None
    # circular distance mod 180 (an axis angle of 179 deg IS ~0 deg -- a
    # line has no distinguished direction, so compare with wraparound.
    diff = abs(direction - 90.0) % 180.0
    circular_diff = min(diff, 180.0 - diff)
    assert circular_diff < 20.0


def test_fill_direction_avoids_0_and_90_degrees():
    geometry = [(0.0, 0.0), (5.0, 5.0), (10.0, 0.0), (5.0, -5.0)]  # roundish
    direction, reason, conf = knowledge.recommend_direction(geometry, StitchType.TATAMI.value)
    assert direction is not None
    assert direction not in (0.0, 90.0)


def test_direction_with_no_geometry_is_honestly_uncertain():
    direction, reason, conf = knowledge.recommend_direction([], StitchType.SATIN.value)
    assert direction is None
    assert conf.value < 0.2
