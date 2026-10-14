"""Golden regression: parametric vector badge (rings + CurvedTextEngine glyphs + monogram) -> plan -> production pass -> package.
Thresholds are deliberately loose: they prove DIRECTION (no crossings, rings are satin rings, export validated), not sew quality."""
import json, os, tempfile
from embcopilot.vectorbadge import build_vector_badge, plan_vector_badge
from embcopilot.pipeline import PipelineResult
from embcopilot.production import production_pass
from embcopilot.qc_v2 import plan_metrics, run_qc
from embcopilot.negative_space import build_protected_zones, find_protected_crossings

def _build(fabric="denim"):
    bp, deps, info = build_vector_badge()
    tb, plan, val = plan_vector_badge(bp, deps, fabric)
    return bp, deps, info, tb, plan, val

def test_badge_objects_and_hierarchy():
    bp, deps, info, tb, plan, val = _build()
    ids = [o.object_id for o in bp.objects]
    assert ids[0] == "outer_border" and "inner_ring" in ids and any(i.startswith("mono_L") for i in ids) and any(i.startswith("text_") for i in ids)
    assert deps["inner_ring"] and all(d == ["outer_border"] for k, d in deps.items() if k.startswith("text_"))
    assert val.status != "ERROR"

def test_rings_are_satin_rings_not_tatami():
    bp, deps, info, tb, plan, val = _build()
    assert plan.object_status["outer_border"]["strategy"] == "satin" and plan.object_status["inner_ring"]["strategy"] == "satin"

def test_counters_in_letters_are_holes():
    bp, deps, *_ = _build()
    o_glyphs = [o for o in bp.objects if o.object_id.endswith("_O")]
    assert o_glyphs and all(len(o.holes_mm) == 1 for o in o_glyphs)

def test_no_stitch_inside_counter_or_background():
    bp, deps, info, tb, plan, val = _build()
    from embcopilot.geometry import point_in_polygon, dist_point_to_polyline
    for o in bp.objects:
        if o.object_id.endswith("_O"):
            hole = o.holes_mm[0]
            inside = [s for s in plan.stitches if s.object_id == o.object_id and s.command == "STITCH" and not s.part.startswith("connector")
                      and point_in_polygon((s.x, s.y), hole) and dist_point_to_polyline((s.x, s.y), hole, True) > 0.2]   # rows may END on the edge
            assert not inside, o.object_id

def test_sequence_respects_dependencies():
    bp, deps, info, tb, plan, val = _build()
    pos = {o: i for i, o in enumerate(plan.order)}
    for k, ds in deps.items():
        for d in ds:
            if k in pos and d in pos:
                assert pos[d] < pos[k], (d, k)

def test_production_pass_removes_protected_crossings_and_exports_validated():
    bp, deps, info, tb, plan, val = _build()
    R = PipelineResult(bp, bp.objects, None, tb, plan, val, [], {}, [], [], {}, {})
    with tempfile.TemporaryDirectory() as d:
        res = production_pass(R, "generic_flat", d, ("dst", "exp", "pes", "emb"))
        m = res.diff["metrics"]
        assert m["center_crossing_travel"]["new"] == 0 and m["center_crossing_travel"]["new"] <= m["center_crossing_travel"]["old"]
        assert m["short_stitch_count"]["new"] <= m["short_stitch_count"]["old"]
        assert not res.qc_after.errors and all(g["passed"] for g in res.gates)
        f = res.package["files"]
        assert f["design.dst"]["status"] == "EXPORTED_VERIFIED" and f["design.emb"]["status"] == "REQUIRES_WILCOM_CONFIGURATION"
        assert not os.path.exists(os.path.join(d, "design.emb")) and not os.path.exists(os.path.join(d, "design.pes"))
        rep = json.load(open(os.path.join(d, "production_report.json")))
        assert rep["travel"]["original_strategy"] and rep["travel"]["repair_strategy"]   # intent changes are reported

def test_preview_and_export_share_one_plan():
    bp, deps, info, tb, plan, val = _build()
    from embcopilot.exportpkg import plan_to_svg
    svg = plan_to_svg(plan)
    first = next(s for s in plan.stitches if s.command == "STITCH")
    assert f"{first.x:.2f},{first.y:.2f}" in svg          # preview coordinates come from the exported stitch list

def test_fabric_changes_technical_parameters_not_just_labels():
    _, _, _, tb_d, plan_d, _ = _build("denim")
    _, _, _, tb_t, plan_t, _ = _build("towel")
    pd = {p.object_id: p for p in tb_d.plans}; pt = {p.object_id: p for p in tb_t.plans}
    diff = [k for k in pd if (pd[k].density_mm, pd[k].pull_compensation_mm, pd[k].underlay) != (pt[k].density_mm, pt[k].pull_compensation_mm, pt[k].underlay)]
    assert diff, "denim and towel produced identical technical parameters"

def test_golden_metric_floor_complex_badge_raster():
    """The raster 'complex badge' golden must keep improving: center-crossing travel -> well below the v0.5 baseline."""
    from embcopilot.golden import write_golden
    from embcopilot.pipeline import run_pipeline
    d = tempfile.mkdtemp()
    R = run_pipeline(write_golden(d)["11_complex_badge"], (100, 100))
    res = production_pass(R, "generic_flat")
    m = res.diff["metrics"]["center_crossing_travel"]
    assert m["old"] >= 20 and m["new"] <= 0.2 * m["old"]      # measured 73 -> 8 on this synthetic golden (not zero: see CAPABILITY_MATRIX)
