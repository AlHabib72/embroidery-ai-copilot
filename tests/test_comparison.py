"""Tests for embcopilot.comparison — coarse reference-vs-design comparison."""

from embcopilot.comparison import compare
from embcopilot.domain import EmbroideryObject, new_id
from embcopilot.vision import ObjectBlueprint
from embcopilot.analyze import analyze
from embcopilot.config import AnalyzerConfig
from embcopilot.models import Design, Record, StitchKind


def _blueprint_with_n_objects(n: int) -> ObjectBlueprint:
    bp = ObjectBlueprint(source_image="x.png", image_size_px=(10, 10), target_size_mm=(10.0, 10.0))
    for i in range(n):
        bp.objects.append(EmbroideryObject(object_id=new_id("obj"), name=f"R{i}"))
    return bp


def _analysis_with_n_segments(n: int):
    """Build a design whose reconstructed segment count is exactly n.

    A single leading stitch with no jump before it never becomes its own
    segment (Phase 1's flush_segment requires >= 2 stitches to define a
    run) — so each loop iteration below, not the initial record, is what
    contributes one segment.
    """
    recs = [Record(0, 0, 0, 0, StitchKind.STITCH)]
    x = 0
    for i in range(n):
        x += 100
        recs.append(Record(x, 0, 90, 0, StitchKind.JUMP))
        recs.append(Record(x + 10, 0, 10, 0, StitchKind.STITCH))
        recs.append(Record(x + 20, 0, 10, 0, StitchKind.STITCH))
        x += 20
    recs.append(Record(x, 0, 0, 0, StitchKind.END))
    d = Design(records=recs)
    return analyze(d, AnalyzerConfig())


def test_matching_counts_reports_no_discrepancy():
    bp = _blueprint_with_n_objects(3)
    a = _analysis_with_n_segments(3)
    report = compare(bp, a)
    assert report.reference_object_count == 3
    assert report.design_segment_count == 3
    assert "match" in report.object_coverage_note.lower()


def test_extra_segments_reported_without_overclaiming():
    bp = _blueprint_with_n_objects(1)
    a = _analysis_with_n_segments(4)
    report = compare(bp, a)
    assert report.design_segment_count > report.reference_object_count
    assert "not proof" in report.object_coverage_note.lower()


def test_missing_segments_reported_without_overclaiming():
    bp = _blueprint_with_n_objects(5)
    a = _analysis_with_n_segments(0)
    report = compare(bp, a)
    assert report.design_segment_count < report.reference_object_count
    assert "not proof" in report.object_coverage_note.lower()


def test_caveats_always_present():
    bp = _blueprint_with_n_objects(2)
    a = _analysis_with_n_segments(1)
    report = compare(bp, a)
    assert len(report.caveats) >= 1
    assert any("shape-matching" in c or "not a shape" in c for c in report.caveats)


def test_to_dict_is_json_serialisable():
    import json

    bp = _blueprint_with_n_objects(2)
    a = _analysis_with_n_segments(1)
    report = compare(bp, a)
    json.dumps(report.to_dict())
