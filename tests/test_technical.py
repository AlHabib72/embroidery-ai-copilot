"""Tests for embcopilot.technical — TechnicalBlueprintEngine."""

from embcopilot.domain import EmbroideryObject, StitchType, new_id
from embcopilot.technical import TechnicalBlueprintEngine, MIN_PRACTICAL_FEATURE_MM
from embcopilot.vision import ObjectBlueprint


def _blueprint_with(*bboxes_mm, geometry=None):
    bp = ObjectBlueprint(source_image="test.png", image_size_px=(100, 100), target_size_mm=(50.0, 50.0))
    for i, bbox in enumerate(bboxes_mm):
        bp.objects.append(
            EmbroideryObject(
                object_id=new_id("obj"),
                name=f"Region {i + 1}",
                bbox_mm=bbox,
                geometry=geometry or [(bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[2], bbox[3]), (bbox[0], bbox[3])],
            )
        )
    return bp


def test_generate_produces_one_plan_per_object():
    bp = _blueprint_with((0, 0, 3, 20), (0, 0, 20, 20))
    engine = TechnicalBlueprintEngine()
    tb, decisions = engine.generate(bp, fabric="woven")
    assert len(tb.plans) == 2
    assert len(decisions) == 2


def test_mutates_source_objects_with_the_recommendation():
    bp = _blueprint_with((0, 0, 3, 20))
    engine = TechnicalBlueprintEngine()
    engine.generate(bp, fabric="woven")
    assert bp.objects[0].stitch_type != StitchType.UNDECIDED.value
    assert bp.objects[0].density_mm is not None
    assert "stitch_type" in bp.objects[0].confidence


def test_small_detail_is_flagged_not_deleted():
    tiny_bbox = (0, 0, 0.2, 0.2)  # 0.2mm, well under the practical floor
    bp = _blueprint_with(tiny_bbox)
    engine = TechnicalBlueprintEngine()
    tb, _ = engine.generate(bp, fabric="woven")
    assert len(tb.small_detail_flags) == 1
    assert tb.small_detail_flags[0].measured_mm < MIN_PRACTICAL_FEATURE_MM
    # the object itself must still be present -- flagged, never removed
    assert len(bp.objects) == 1


def test_decision_requires_approval_when_confidence_is_low():
    # A round-ish shape in the satin band should score a lower confidence
    # (routed to tatami with a hedge) -- see test_knowledge for the rule.
    bp = _blueprint_with((0, 0, 4, 4))  # square bbox -> low elongation
    engine = TechnicalBlueprintEngine()
    tb, decisions = engine.generate(bp, fabric="woven")
    assert decisions[0].confidence.value < 0.85
    assert decisions[0].requires_approval is True


def test_overall_confidence_is_object_specific_not_global():
    # thin elongated bar vs round blob: different evidence -> different scores
    bp = _blueprint_with((0, 0, 3, 20), (0, 0, 20, 20))
    tb, _ = TechnicalBlueprintEngine().generate(bp, fabric="woven")
    a, b = (p.overall_confidence.value for p in tb.plans)
    assert abs(a - b) > 0.05, (a, b)  # identical scores would mean no evidence is used
    for p in tb.plans:
        assert 0.0 <= p.overall_confidence.value <= 1.0
