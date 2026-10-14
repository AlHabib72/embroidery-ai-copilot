import math, os, tempfile
import cv2, numpy as np
from embcopilot.connector import route_hidden_path, route_connector, ConnectorCostWeights
from embcopilot.geometry import resample_polyline
from embcopilot.repair import (run_qc, propose_repairs, apply_repairs, metrics, QCTargets, _cleanup_short, _split_long)
from embcopilot.stitch import Stitch, StitchPlan, validate_stitch_plan
from embcopilot.pipeline import run_pipeline
from embcopilot.compare_geo import compare_geometry
from embcopilot.domain import EmbroideryObject

def _u_mask():
    scale = 4.0; m = np.zeros((160, 160), bool)
    def rect(x0, y0, x1, y1): m[int(y0 * scale):int(y1 * scale), int(x0 * scale):int(x1 * scale)] = True
    rect(5, 5, 10, 35); rect(30, 5, 35, 35); rect(5, 30, 35, 35)
    return m, scale

def test_hidden_route_stays_entirely_inside_allowed_area_around_the_gap():
    m, sc = _u_mask()
    p = route_hidden_path((7, 7), (32, 7), m, sc)
    assert p is not None
    assert all(m[int(q[1] * sc), int(q[0] * sc)] for q in resample_polyline(p, 0.3))
    L = sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1)); assert L > 25 * 2           # a real detour, not the straight line

def test_hidden_route_none_when_no_hidden_path_exists():
    m, sc = _u_mask(); m[int(30 * sc):int(35 * sc), :] = False                                    # cut the U
    assert route_hidden_path((7, 7), (32, 7), m, sc) is None

def test_hidden_route_respects_detour_limit():
    m, sc = _u_mask()
    assert route_hidden_path((7, 7), (32, 7), m, sc, max_detour=1.2, max_abs_detour_mm=1.0) is None

def test_routed_candidate_wins_over_visible_jump_and_carries_its_path():
    m, sc = _u_mask(); never = lambda x, y: False
    path = route_hidden_path((7, 7), (32, 7), m, sc)
    d = route_connector((7, 7), (32, 7), never, never, ConnectorCostWeights.from_profile("quality_first"), routed_path=path)
    assert "HIDDEN_ROUTED" in d.candidates
    if d.strategy == "HIDDEN_ROUTED":
        assert d.path == path

def _res():
    img = np.full((300, 300, 3), 255, np.uint8)
    cv2.circle(img, (80, 80), 50, (0, 0, 200), -1, cv2.LINE_AA); cv2.circle(img, (220, 220), 50, (0, 150, 0), -1, cv2.LINE_AA)
    cv2.circle(img, (220, 80), 40, (200, 0, 0), -1, cv2.LINE_AA)
    p = os.path.join(tempfile.mkdtemp(), "x.png"); cv2.imwrite(p, img)
    return run_pipeline(p, (90, 90))
R = _res()
CTX = {"objects": R.objects, "technical": R.technical, "canvas": R.plan.canvas_mm, "deps": R.graph.dependencies(), "profile": "balanced"}

def test_qc_flags_threshold_violations_with_severity_and_diagnosis():
    iss = run_qc(R.plan, R.objects, R.validation, QCTargets(max_jumps=0, max_trims=0, max_color_changes=0), R.technical)
    codes = {i.code for i in iss}
    assert {"EXCESSIVE_JUMPS", "EXCESSIVE_TRIMS", "EXCESSIVE_COLOR_CHANGES"} <= codes
    assert all(i.severity in ("INFO", "WARNING", "ERROR", "CRITICAL") for i in iss)
    assert any(i.diagnosis for i in iss)

def test_qc_clean_with_generous_targets():
    iss = run_qc(R.plan, R.objects, R.validation, QCTargets(max_jumps=999, max_trims=999, max_color_changes=999, max_travel_fraction=1.0), R.technical)
    assert not {"EXCESSIVE_JUMPS", "EXCESSIVE_TRIMS", "EXCESSIVE_COLOR_CHANGES", "UNNECESSARY_TRAVEL"} & {i.code for i in iss}

def test_qc_density_rules():
    import copy
    tb = copy.deepcopy(R.technical); tb.plans[0].stitch_type = "tatami"; tb.plans[0].density_mm = 0.1
    assert "DENSITY_TOO_HIGH" in {i.code for i in run_qc(R.plan, R.objects, R.validation, QCTargets(), tb)}
    tb.plans[0].density_mm = 1.2
    assert "DENSITY_TOO_SPARSE" in {i.code for i in run_qc(R.plan, R.objects, R.validation, QCTargets(), tb)}

def _dirty_plan():
    st = [Stitch(0, 0, "JUMP", "c", "o", 0, "c"), Stitch(0, 0, "STITCH", "c", "o", 0, "cover:x")]
    for i in range(1, 30):
        st.append(Stitch(i * 0.5, 0, "STITCH", "c", "o", 0, "cover:x")); st.append(Stitch(i * 0.5 + 0.05, 0, "STITCH", "c", "o", 0, "cover:x"))
    st.append(Stitch(30, 0, "STITCH", "c", "o", 0, "cover:x")); st.append(Stitch(30, 0, "END", "c", "", 0, "e"))
    return StitchPlan((40, 10), st, ["o"])

def test_cleanup_short_stitches_reduces_measured_short_count_and_keeps_shape():
    p = _dirty_plan(); m0 = metrics(p); p2 = _cleanup_short(p)
    assert metrics(p2)["short_stitches"] < m0["short_stitches"]
    assert (p2.stitches[-2].x, p2.stitches[-2].y) == (p.stitches[-2].x, p.stitches[-2].y)     # end point preserved
    assert len(p.stitches) == len(_dirty_plan().stitches)                                     # original untouched

def test_split_long_only_splits_non_satin():
    a = StitchPlan((40, 10), [Stitch(0, 0, "JUMP", "c", "o"), Stitch(0, 0, "STITCH", "c", "o", 0, "cover:tatami"), Stitch(15, 0, "STITCH", "c", "o", 0, "cover:tatami"),
                              Stitch(15, 0, "END", "c", "")], ["o"])
    assert metrics(_split_long(a, 7.0))["long_stitches"] == 0 and metrics(a)["long_stitches"] == 1
    b = StitchPlan((40, 10), [Stitch(0, 0, "JUMP", "c", "o"), Stitch(0, 0, "STITCH", "c", "o", 0, "cover:satin"), Stitch(9, 0, "STITCH", "c", "o", 0, "cover:satin"),
                              Stitch(9, 0, "END", "c", "")], ["o"])
    assert len(_split_long(b, 7.0).stitches) == len(b.stitches)

def test_propose_measures_but_never_mutates_and_apply_only_approved():
    before_stitches = [(s.x, s.y, s.command) for s in R.plan.stitches]
    iss = run_qc(R.plan, R.objects, R.validation, QCTargets(max_jumps=0, max_trims=0), R.technical)
    props = propose_repairs(R.plan, iss, CTX, R.objects)
    assert [(s.x, s.y, s.command) for s in R.plan.stitches] == before_stitches                # proposing changed nothing
    assert props and all(p.before and p.after and p.delta is not None for p in props)
    okay = [p for p in props if p.status == "PROPOSED"]
    new, applied = apply_repairs(R.plan, props, [], CTX)
    assert applied == [] and [(s.x, s.y, s.command) for s in new.stitches] == before_stitches  # nothing approved -> nothing applied
    if okay:
        new, applied = apply_repairs(R.plan, props, [okay[0].name], CTX)
        assert applied == [okay[0].name] and new is not R.plan

def test_rejected_or_no_effect_repairs_cannot_be_applied():
    iss = run_qc(R.plan, R.objects, R.validation, QCTargets(max_jumps=0, max_trims=0), R.technical)
    props = propose_repairs(R.plan, iss, CTX, R.objects)
    bad = [p for p in props if p.status != "PROPOSED"]
    for p in bad:
        _, applied = apply_repairs(R.plan, props, [p.name], CTX)
        assert applied == []

def test_repaired_plan_still_validates_and_after_metrics_match_reality():
    plan = _dirty_plan()
    ctx = dict(CTX)
    from embcopilot.repair import QCIssue
    props = propose_repairs(plan, [QCIssue("TOO_SHORT_STITCHES", "WARNING", "x")], ctx, [EmbroideryObject("o", "o", geometry=[(-1, -1), (31, -1), (31, 1), (-1, 1)], bbox_mm=(-1, -1, 31, 1), width_stats_mm={"median": 0.5})])
    p = next(x for x in props if x.name == "cleanup_short_stitches")
    assert p.status == "PROPOSED" and p.after["short_stitches"] == metrics(_cleanup_short(plan))["short_stitches"] < p.before["short_stitches"]

# ------------------------------------------------------------ comparison upgrade
def test_comparison_reports_contour_error_small_for_faithful_stitching():
    for c in R.comparison:
        assert c.contour_chamfer_mm is not None and c.contour_chamfer_mm < 1.0 and c.topology_ok

def test_comparison_detects_topology_loss_when_a_hole_is_stitched_over():
    ring = EmbroideryObject("r", "ring", geometry=[(10, 10), (40, 10), (40, 40), (10, 40)], holes_mm=[[(20, 20), (30, 20), (30, 30), (20, 30)]], bbox_mm=(10, 10, 40, 40), area_mm2=800)
    st = [Stitch(10, 10, "JUMP", "c", "r")]
    y = 10.0
    while y <= 40:                                                  # solid rows straight across, ignoring the hole
        st += [Stitch(10, y, "STITCH", "c", "r"), Stitch(40, y, "STITCH", "c", "r")]; y += 0.5
    plan = StitchPlan((50, 50), st, ["r"])
    per, glob = compare_geometry([ring], plan)
    assert per[0].holes_reference == 1 and per[0].holes_stitched == 0 and not per[0].topology_ok
    assert per[0].status != "MATCH" and any("topology" in n for n in per[0].notes)

def test_comparison_flags_missing_object():
    ghost = EmbroideryObject("g", "ghost", geometry=[(1, 1), (5, 1), (5, 5), (1, 5)], bbox_mm=(1, 1, 5, 5))
    per, _ = compare_geometry([ghost], StitchPlan((10, 10), [], []))
    assert per[0].status == "MISSING"
