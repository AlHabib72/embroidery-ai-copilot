"""Tests for embcopilot.command — NL -> structured command, anti-hallucination."""

from embcopilot.command import CommandParser
from embcopilot.domain import CommandKind, EmbroideryObject, new_id
from embcopilot.vision import ObjectBlueprint


def _blueprint_with_categories(*categories):
    bp = ObjectBlueprint(source_image="x.png", image_size_px=(10, 10), target_size_mm=(10.0, 10.0))
    for i, cat in enumerate(categories):
        bp.objects.append(EmbroideryObject(object_id=new_id("obj"), name=f"R{i}", category=cat))
    return bp


def test_query_phrasing_is_recognised_as_query_even_with_action_verb():
    cmd = CommandParser().parse("How should I digitize this wing?")
    assert cmd.kind == CommandKind.QUERY.value


def test_digitize_verbs_recognised_as_action():
    for text in ["Punch all leaves.", "Trace only branches.", "Finish bird.", "Create the wing."]:
        cmd = CommandParser().parse(text)
        assert cmd.kind == CommandKind.ACTION.value, text


def test_reduce_trims_sets_constraint():
    cmd = CommandParser().parse("Reduce trims")
    assert cmd.constraints.get("minimize_trims") is True


def test_optimize_connectors_intent():
    cmd = CommandParser().parse("Optimize connectors")
    assert cmd.intent == "optimize_connectors"


def test_irregular_plural_leaf_leaves_is_matched():
    cmd = CommandParser().parse("Punch all leaves.")
    assert cmd.target_category == "leaf"


def test_validate_reports_zero_matches_without_hallucinating():
    bp = _blueprint_with_categories("body", "wing")
    cmd = CommandParser().parse("Trace only branches.")
    cmd = CommandParser().validate(cmd, bp)
    assert cmd.matched_object_ids == []
    assert "0 confirmed" in cmd.note


def test_validate_reports_real_matches():
    bp = _blueprint_with_categories("leaf", "leaf", "body")
    cmd = CommandParser().parse("Punch all leaves.")
    cmd = CommandParser().validate(cmd, bp)
    assert len(cmd.matched_object_ids) == 2


def test_quality_constraints_parsed():
    cmd = CommandParser().parse("Premium quality, minimum thread usage.")
    assert cmd.constraints.get("quality_profile") == "quality_first"
    assert cmd.constraints.get("minimize_thread") is True


def test_no_category_means_entire_design_scope():
    cmd = CommandParser().parse("Optimize connectors")
    assert cmd.scope == "entire_design"
    assert cmd.target_category is None
