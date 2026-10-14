"""Production profiles must change results (audit finding: they were ignored)."""

import os
import tempfile

import cv2
import numpy as np
import pytest

from embcopilot import knowledge
from embcopilot.domain import ConfidenceThresholds, StitchType
from embcopilot.profiles import PROFILES, get_profile
from embcopilot.technical import TechnicalBlueprintEngine
from embcopilot.vision import LocalVisionProvider


def _bp(width_px=24):
    img = np.full((200, 200, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 90), (180, 90 + width_px), (0, 0, 200), -1)  # satin-width bar
    d = tempfile.mkdtemp()
    p = os.path.join(d, "bar.png")
    cv2.imwrite(p, img)
    return LocalVisionProvider().analyze(p, (80.0, 80.0))


def test_all_spec_profiles_exist():
    for n in ["quality_first", "balanced", "speed_first", "cap", "jacket", "left_chest",
              "flat_fabric", "knit", "denim", "light_fabric", "heavy_fabric"]:
        assert n in PROFILES


def test_unknown_profile_is_rejected_not_ignored():
    with pytest.raises(ValueError):
        get_profile("does_not_exist")
    with pytest.raises(ValueError):
        TechnicalBlueprintEngine().generate(_bp(), production_profile="nope")


def test_profile_changes_density_of_a_fill():
    d_q, _ = knowledge.recommend_density_mm(StitchType.TATAMI.value, "woven", "quality_first")
    d_s, _ = knowledge.recommend_density_mm(StitchType.TATAMI.value, "woven", "speed_first")
    assert d_q < d_s  # denser spacing for quality


def test_profile_changes_pull_compensation():
    a, _ = knowledge.recommend_pull_compensation_mm(StitchType.SATIN.value, 4.0, "woven", "balanced")
    b, _ = knowledge.recommend_pull_compensation_mm(StitchType.SATIN.value, 4.0, "woven", "knit")
    assert b > a


def test_underlay_tier_shifts_with_profile_and_clamps():
    bal, _ = knowledge.recommend_underlay(StitchType.SATIN.value, 5.0, "balanced")
    hi, _ = knowledge.recommend_underlay(StitchType.SATIN.value, 5.0, "quality_first")
    lo, _ = knowledge.recommend_underlay(StitchType.SATIN.value, 5.0, "speed_first")
    assert bal == "zigzag" and hi == "double_zigzag" and lo == "center_run"
    top, _ = knowledge.recommend_underlay(StitchType.SATIN.value, 9.0, "quality_first")
    assert top == "double_zigzag"  # clamped, no index error


def test_end_to_end_profile_changes_technical_plan():
    bp_a, bp_b = _bp(), _bp()
    tb_a, _ = TechnicalBlueprintEngine().generate(bp_a, production_profile="quality_first")
    tb_b, _ = TechnicalBlueprintEngine().generate(bp_b, production_profile="speed_first")
    a, b = tb_a.plans[0], tb_b.plans[0]
    assert a.stitch_type == b.stitch_type == StitchType.SATIN.value
    assert (a.density_mm, a.underlay, a.pull_compensation_mm) != (b.density_mm, b.underlay, b.pull_compensation_mm)
    assert tb_a.production_profile == "quality_first"


def test_profile_min_feature_drives_small_detail_flags():
    img = np.full((200, 200, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 100), (180, 102), (0, 0, 0), -1)  # 3px = 1.2mm... use tiny scale
    d = tempfile.mkdtemp(); p = os.path.join(d, "l.png"); cv2.imwrite(p, img)
    bp = LocalVisionProvider().analyze(p, (20.0, 20.0))  # 0.1 mm/px -> ~0.3mm line
    tb, _ = TechnicalBlueprintEngine().generate(bp, production_profile="knit")
    assert tb.small_detail_flags and tb.small_detail_flags[0].threshold_mm == PROFILES["knit"].min_feature_mm


def test_technical_uses_measured_width_for_diagonal_stem():
    """Old bbox proxy called a thin diagonal stem 'wide' -> tatami. Must be RUN/SATIN now."""
    img = np.full((200, 200, 3), 255, np.uint8)
    cv2.line(img, (20, 180), (180, 20), (0, 100, 0), 5, lineType=cv2.LINE_AA)
    d = tempfile.mkdtemp(); p = os.path.join(d, "s.png"); cv2.imwrite(p, img)
    bp = LocalVisionProvider().analyze(p, (80.0, 80.0))
    tb, _ = TechnicalBlueprintEngine().generate(bp)
    assert tb.plans[0].stitch_type in (StitchType.RUN.value, StitchType.TRIPLE_RUN.value, StitchType.SATIN.value)
    assert tb.plans[0].stitch_type not in (StitchType.TATAMI.value, StitchType.COMPLEX_FILL.value)


def test_confidence_threshold_bands_match_spec_and_validate():
    th = ConfidenceThresholds()
    assert th.band(0.95) == "automatic_safe"
    assert th.band(0.94) == "ask_approval"      # was "automatic_safe" at 0.90 before the fix
    assert th.band(0.85) == "ask_approval"
    assert th.band(0.84) == "require_review"
    assert th.band(0.69) == "do_not_auto_execute"
    with pytest.raises(ValueError):
        ConfidenceThresholds(auto_safe=0.5, ask_approval=0.8, require_review=0.9)
    custom = ConfidenceThresholds(auto_safe=0.9)
    assert custom.band(0.91) == "automatic_safe"  # configurable, not hard-coded


def test_connector_weights_follow_profile_and_change_decisions():
    from embcopilot.connector import ConnectorCostWeights, decide_connector
    q = ConnectorCostWeights.from_profile("quality_first")
    sp = ConnectorCostWeights.from_profile("speed_first")
    assert q.trim_weight < sp.trim_weight and q.visible_travel_weight > sp.visible_travel_weight
    # 6mm open-fabric gap (hand-computed costs):
    #   quality: trim 6.4  vs jump 0.8*6+6.0 = 10.8  -> trim
    #   speed:   trim 12.8 vs jump 1.2*6+1.8 = 9.0   -> jump
    assert decide_connector(6.0, weights=q).strategy == "trim"
    assert decide_connector(6.0, weights=sp).strategy == "jump"
