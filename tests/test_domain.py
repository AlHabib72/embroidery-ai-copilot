"""Tests for embcopilot.domain — core dataclasses and enums."""

from embcopilot.domain import (
    ApprovalState,
    Confidence,
    ConfidenceThresholds,
    Decision,
    DesignProject,
    EmbroideryObject,
    RiskLevel,
    new_id,
)


def test_confidence_clamps_to_0_1():
    assert Confidence(1.5).value == 1.0
    assert Confidence(-0.2).value == 0.0
    assert Confidence(0.42).pct == 42


def test_confidence_thresholds_bands():
    th = ConfidenceThresholds()
    assert th.band(0.95) == "automatic_safe"
    assert th.band(0.87) == "ask_approval"
    assert th.band(0.75) == "require_review"
    assert th.band(0.3) == "do_not_auto_execute"


def test_new_id_has_prefix_and_is_unique():
    a = new_id("obj")
    b = new_id("obj")
    assert a.startswith("obj_")
    assert a != b


def test_embroidery_object_bbox_dimensions():
    obj = EmbroideryObject(object_id="o1", name="test", bbox_mm=(1.0, 2.0, 5.0, 9.0))
    assert obj.width_mm == 4.0
    assert obj.height_mm == 7.0


def test_embroidery_object_to_dict_serialises_confidence():
    obj = EmbroideryObject(object_id="o1", name="test", confidence={"stitch_type": Confidence(0.8, "test")})
    d = obj.to_dict()
    assert d["confidence"]["stitch_type"]["value"] == 0.8


def test_decision_defaults_require_approval():
    dec = Decision(
        decision_id="d1", subject_object_id="o1", action="do_x", reason="because",
        confidence=Confidence(0.5),
    )
    assert dec.requires_approval is True
    assert dec.approval_state == ApprovalState.PROPOSED.value
    assert dec.risk == RiskLevel.LOW


def test_design_project_to_dict_roundtrips_objects():
    proj = DesignProject(project_id="p1", name="Test")
    proj.objects.append(EmbroideryObject(object_id="o1", name="obj1"))
    d = proj.to_dict()
    assert d["project_id"] == "p1"
    assert len(d["objects"]) == 1
    assert d["objects"][0]["object_id"] == "o1"
