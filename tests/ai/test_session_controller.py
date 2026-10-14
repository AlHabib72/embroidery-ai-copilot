import math, os, tempfile
from embcopilot.vectorbadge import build_vector_badge
from embcopilot.ai.session import DesignSession, build_roles, ring_set_width
from embcopilot.ai.router import AIRouter
from embcopilot.ai.controller import AIController
from embcopilot.ai.schemas import validate_steps
from embcopilot.stitchclean import count_over

_CACHE = {}
def _session():
    if "bp" not in _CACHE:
        _CACHE["bp"] = build_vector_badge()
    bp, deps, _ = _CACHE["bp"]
    return DesignSession(bp, deps, "woven", "balanced")

def _steps(*raw):
    s, e = validate_steps(list(raw)); assert not e, e; return s

def _ring_width(S, oid="outer_border"):
    o = next(o for o in S.build.objects if o.object_id == oid)
    return math.dist((50, 50), o.geometry[0]) - math.dist((50, 50), o.holes_mm[0][0])

def test_roles_detected_for_vector_badge():
    S = _session(); r = S.roles()
    assert r["outer_ring"] == ["outer_border"] and r["inner_ring"] == ["inner_ring"] and len(r["text"]) >= 15 and len(r["monogram"]) == 2

def test_roles_detected_for_raster_rings():
    from embcopilot.golden import write_golden
    from embcopilot.pipeline import run_pipeline
    R = run_pipeline(write_golden(tempfile.mkdtemp())["11_complex_badge"], (100, 100))
    S = DesignSession.from_pipeline(R)
    assert "outer_ring" in S.roles() and "inner_ring" in S.roles()
    assert abs(S.build.metrics["stitch_count"] - plan_stitches(R)) <= max(60, 0.02 * plan_stitches(R))   # session baseline ~ original pipeline plan

def plan_stitches(R): return R.plan.stats()["stitch_count"]

def test_ring_width_change_is_exact_geometry():
    S = _session(); w0 = _ring_width(S)
    S.apply(_steps({"op": "set_width", "target": "outer_ring", "width_mm": 2.0}))
    assert abs(w0 - 3.5) < 0.05 and abs(_ring_width(S) - 2.0) < 0.05

def test_width_on_non_ring_is_refused_not_faked():
    S = _session()
    out = S.apply(_steps({"op": "set_width", "target": "monogram", "width_mm": 2.0}))
    assert any("WIDTH_NOT_APPLIED" in n for n in out["notes"])                  # reported, geometry untouched

def test_parameters_really_change_the_plan():
    S = _session()
    tb0 = {p.object_id: p for p in S.build.tb.plans}["outer_border"]
    S.apply(_steps({"op": "set_density", "target": "outer_ring", "density_mm": 0.6}, {"op": "set_pull_comp", "target": "outer_ring", "pull_comp_mm": 0.5},
                   {"op": "set_underlay", "target": "outer_ring", "underlay": ["double_zigzag"]}))
    tb1 = {p.object_id: p for p in S.build.tb.plans}["outer_border"]
    assert tb1.density_mm == 0.6 and tb1.pull_compensation_mm == 0.5 and tb1.underlay == "double_zigzag" and tb0.density_mm != 0.6
    n = sum(1 for s in S.build.plan.stitches if s.object_id == "outer_border" and s.part.startswith("cover"))
    assert n > 0
    assert any(s.part.startswith("underlay:zigzag2") for s in S.build.plan.stitches if s.object_id == "outer_border")

def test_fabric_change_changes_technical_values():
    S = _session()
    d0 = {p.object_id: (p.density_mm, p.pull_compensation_mm, p.underlay) for p in S.build.tb.plans}
    S.apply(_steps({"op": "set_fabric", "fabric": "TOWEL"}))
    d1 = {p.object_id: (p.density_mm, p.pull_compensation_mm, p.underlay) for p in S.build.tb.plans}
    assert d0 != d1 and S.state["fabric"] == "towel"

def test_auto_split_enforces_limit():
    S = _session()
    S.apply(_steps({"op": "auto_split", "max_stitch_mm": 4.0}))
    assert count_over(S.build.plan, 4.0) == 0

def test_protect_blocks_changes_in_same_command():
    S = _session()
    out = S.apply(_steps({"op": "protect", "target": "monogram"}, {"op": "set_color", "target": "monogram", "color_hex": "#C9A227"},
                         {"op": "set_color", "target": "outer_ring", "color_hex": "#C9A227"}))
    assert any("PROTECTED" in s["reason"] for s in out["skipped"])
    cols = {o.object_id: o.color_group for o in S.build.objects}
    assert cols["outer_border"] == "rgb(201,162,39)" and cols["mono_L_023"] != "rgb(201,162,39)"

def test_missing_target_is_skipped_with_reason():
    S = _session()
    out = S.apply(_steps({"op": "set_density", "target": "nonexistent_thing", "density_mm": 0.4}))
    assert out["skipped"] and "TARGET_NOT_FOUND" in out["skipped"][0]["reason"] and not out["changed"]

def test_type_target_resolves_against_original_types_for_the_whole_command():
    S = _session()
    n_sat = sum(1 for p in S.build.tb.plans if p.stitch_type == "satin")
    assert n_sat >= 2
    out = S.apply(_steps({"op": "set_stitch_type", "target": "type:satin", "stitch_type": "tatami"}, {"op": "set_density", "target": "type:satin", "density_mm": 0.45}))
    assert not out["skipped"] and all(p.density_mm == 0.45 for p in S.build.tb.plans if p.object_id in ("outer_border", "inner_ring"))

def test_delete_object_removes_it_and_undo_restores():
    S = _session()
    n0 = len(S.build.objects)
    S.apply(_steps({"op": "delete_object", "target": "monogram"}))
    assert len(S.build.objects) == n0 - 2 and not any(s.object_id.startswith("mono_") for s in S.build.plan.stitches)
    S.undo(); assert len(S.build.objects) == n0
    S.redo(); assert len(S.build.objects) == n0 - 2

def test_undo_redo_audit_and_history():
    S = _session()
    S.apply(_steps({"op": "set_density", "target": "all", "density_mm": 0.5}), "prompt A", "local-rules", "rule-parser-v1", "low", "user")
    assert S.undo_stack and S.history[-1]["prompt"] == "prompt A"
    m = S.undo(); assert m["stitch_count"] > 0 and not S.undo_stack and S.state["density"] == {}
    assert any(r.get("event") == "UNDO" for r in S.audit.records) and S.audit.records[0]["provider"] == "local-rules"

def test_fewer_trims_never_increases_trims():
    S = _session(); t0 = S.build.metrics["trim_count"]
    S.apply(_steps({"op": "optimize", "what": ["fewer_trims"]}))
    assert S.build.metrics["trim_count"] <= t0

def test_controller_confirm_flow_with_edit_and_exclude():
    S = _session(); C = AIController(S, AIRouter("LOCAL"), "CONFIRM")
    p = C.propose("outer ring 2mm satin, and make the monogram gold")
    assert p["status"] == "pending" and p["decision"]["requires_approval"] and p["preview"]["after"]["stitch_count"] > 0
    assert S.state["width"] == {}                                                       # NOTHING applied before approval
    wi = next(c["index"] for c in p["chips"] if c["op"] == "set_stitch_type")
    ci = next(c["index"] for c in p["chips"] if c["op"] == "set_color")
    C.approve(p["proposal_id"], exclude=[ci], edits={str(wi): {"width_mm": 2.5}})      # drop the colour chip, edit the width chip
    assert abs(_ring_width(S) - 2.5) < 0.05 and S.state["color"] == {}
    try: C.approve(p["proposal_id"]); assert False
    except ValueError: pass                                                             # cannot apply twice

def test_controller_rejects_out_of_range_edit_and_non_editable_param():
    S = _session(); C = AIController(S, AIRouter("LOCAL"), "CONFIRM")
    p = C.propose("density 0.4mm spacing on the text")
    di = p["chips"][0]["index"]
    for edits in ({str(di): {"density_mm": 5.0}}, {str(di): {"op": "delete_object"}}):
        try: C.approve(p["proposal_id"], edits=edits); assert False
        except ValueError: pass
    assert S.state["density"] == {}

def test_controller_auto_mode_applies_only_low_risk():
    S = _session(); C = AIController(S, AIRouter("LOCAL"), "AUTO")
    low = C.propose("0.45mm spacing on the text")
    assert low["status"] == "applied" and S.state["density"]
    high = C.propose("remove the monogram")
    assert high["status"] == "pending" and len(S.build.objects) > 0 and not S.state["deleted"]

def test_controller_manual_mode_blocks_edits_and_reject_is_logged():
    S = _session(); C = AIController(S, AIRouter("LOCAL"), "MANUAL")
    p = C.propose("0.45mm spacing on the text")
    assert p["status"] == "blocked" and not S.state["density"]
    C.set_mode("CONFIRM"); p2 = C.propose("0.45mm spacing on the text"); C.reject(p2["proposal_id"])
    assert any(r.get("event") == "REJECTED" for r in S.audit.records)

def test_export_and_emb_requests_are_honest():
    with tempfile.TemporaryDirectory() as d:
        S = _session(); C = AIController(S, AIRouter("LOCAL"), "CONFIRM", d)
        p = C.propose("export emb"); C.approve(p["proposal_id"])
        r = p["result"]["actions"][0]["result"]
        assert r["status"] == "REQUIRES_WILCOM_CONFIGURATION" and r["download"] is False
        assert not [f for f in os.listdir(d) if f.endswith(".emb")] and not any(f.endswith(".emb") for _, _, fs in os.walk(d) for f in fs)
        assert "handoff" in r and r["handoff"]["not_an_emb"]
        p = C.propose("export pes"); C.approve(p["proposal_id"])
        assert p["result"]["actions"][0]["result"]["status"] == "UNAVAILABLE"
        p = C.propose("export dst"); C.approve(p["proposal_id"])
        assert p["result"]["actions"][0]["result"]["status"] in ("EXPORTED_VERIFIED", "BLOCKED_BY_QC")
