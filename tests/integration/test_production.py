import json, math, os, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from embcopilot.stitch import Stitch, StitchPlan
from embcopilot.negative_space import (ProtectedTravelZone, find_protected_crossings, route_around, reroute_protected_crossings,
                                       build_protected_zones, extract_negative_space)
from embcopilot.machine import MachineProfile, TravelContext, decide_travel, get_machine
from embcopilot.stitchclean import classify_short_stitches, clean_short_stitches
from embcopilot.qc_v2 import run_qc, export_gate, OverrideLog, plan_metrics, release_gates
from embcopilot.travel import optimize_travel
from embcopilot.designdiff import diff_plans

def _plan(stitches):
    return StitchPlan(canvas_mm=(100, 100), stitches=stitches, order=["a"])

def _zone():
    return ProtectedTravelZone("z", [(40, 40), (60, 40), (60, 60), (40, 60)], "enclosed_empty")

def _crossing_plan():
    return _plan([Stitch(10, 50, "STITCH", "c", "a", 0, "cover:x"), Stitch(11, 50, "STITCH", "c", "a", 0, "cover:x"),
                  Stitch(90, 50, "JUMP", "c", "a", 0, "connector:jump"), Stitch(91, 50, "STITCH", "c", "a", 0, "cover:x"),
                  Stitch(91, 50, "TRIM", "c", "a", 0, "connector:end_trim"), Stitch(91, 50, "END", "c", "", 0, "end")])

def test_zone_crossing_detected():
    cr = find_protected_crossings(_crossing_plan(), [_zone()])
    assert len(cr) == 1 and cr[0].inside_mm > 15

def test_trimmed_jump_not_flagged_as_thread_dragging():
    p = _crossing_plan()
    p.stitches.insert(2, Stitch(11, 50, "TRIM", "c", "a", 0, "connector:trim"))
    assert find_protected_crossings(p, [_zone()]) == []

def test_route_around_avoids_zone():
    path = route_around((10, 50), (90, 50), [_zone()], 0.8, (100, 100))
    assert path and len(path) >= 3
    plan = _plan([Stitch(*path[0], "STITCH", "c", "a"), *[Stitch(x, y, "JUMP", "c", "a", 0, "connector:detour") for x, y in path[1:]]])
    assert find_protected_crossings(plan, [_zone()]) == []

def test_reroute_reports_original_and_repair_strategy():
    new, rep = reroute_protected_crossings(_crossing_plan(), [_zone()])
    d = rep.to_dict()
    assert d["fixed"] == 1 and d["original_strategy"] and d["repair_strategy"] and d["reason"] == "CENTER_CROSSING_TRAVEL"
    assert find_protected_crossings(new, [_zone()]) == []

def test_optimize_travel_no_crossing_afterwards_and_input_not_mutated():
    p = _crossing_plan(); before = len(p.stitches)
    new, rep = optimize_travel(p, [_zone()], "generic_flat")
    assert len(p.stitches) == before                       # input untouched
    assert find_protected_crossings(new, [_zone()]) == []
    assert rep.rerouted + rep.trims_added >= 1

def test_trim_policy_depends_on_context():
    m = get_machine("generic_flat")
    assert decide_travel(TravelContext(1.5, True, True), m)["action"] == "run_connector"
    assert decide_travel(TravelContext(30, False, False), m)["action"] == "trim_jump"
    assert decide_travel(TravelContext(30, False, False, crosses_protected=True), m)["action"] == "route_around"
    assert decide_travel(TravelContext(30, False, False), get_machine("no_trim_machine"))["action"] == "jump"
    stretchy = decide_travel(TravelContext(1.7, False, False, fabric_stretch=0.9, thread_visibility=0.9), m)
    assert stretchy["action"] == "trim_jump"                # same distance, different fabric/thread => different decision
    assert decide_travel(TravelContext(1.7, False, False), m)["action"] == "jump"

def _s(x, y, part="cover:x", obj="a"):
    return Stitch(x, y, "STITCH", "c", obj, 0, part)

def test_short_stitch_classes():
    st = [_s(0, 0), _s(5, 0), _s(5.0, 0.0), _s(10, 0)]                         # duplicate
    assert ("duplicate" in [c for _, c in classify_short_stitches(st)])
    st = [_s(0, 0), _s(5, 0), _s(5.1, 0), _s(5.1, 5)]                            # corner (90 deg direction change)
    assert any(c == "corner" for _, c in classify_short_stitches(st))
    st = [_s(0, 0), _s(5, 0), _s(5, 0.1, "lock:tie"), _s(10, 0)]
    assert any(c == "intentional" for _, c in classify_short_stitches(st))

def test_cleanup_preserves_corners_and_locks():
    st = [_s(0, 0), _s(5, 0), _s(5, 0), _s(5.1, 0.1), _s(5.1, 5), _s(5.1, 5.1, "lock:tie"), _s(9, 5)]
    new, rep = clean_short_stitches(_plan(st))
    assert rep.preserved >= 1 and rep.removed >= 1
    assert any(abs(s.x - 5.1) < 1e-9 and abs(s.y - 5.1) < 1e-9 for s in new.stitches)   # lock stitch kept

def test_qc_export_blocking_and_logged_override():
    class O: object_id = "bg"; category = "background"; holes_mm = []; geometry = []; stitch_type = "tatami"
    p = _plan([_s(10, 10, obj="bg"), _s(20, 10, obj="bg"), Stitch(20, 10, "END", "c", "", 0, "end")])
    rep = run_qc(p, [O()], None, "generic_flat")
    assert any(i.code == "BACKGROUND_AS_STITCH" and i.severity == "ERROR" for i in rep.issues)
    assert export_gate(rep).allowed is False
    log = OverrideLog()
    assert export_gate(rep, {"confirm": True, "reason": ""}, log).allowed is False          # reason required
    d = export_gate(rep, {"confirm": True, "reason": "customer approved", "user": "ann"}, log)
    assert d.allowed and d.override_logged and log.entries[0]["reason"] == "customer approved"

def test_qc_categories_present():
    rep = run_qc(_crossing_plan(), [], [_zone()], "generic_flat", scale_known=False)
    cats = rep.to_dict()["categories"]
    assert set(["Geometry QC", "Pathing QC", "Export QC", "Reference QC"]) <= set(cats)
    assert any(i.code == "SCALE_UNKNOWN" for i in rep.issues) and any(i.code == "CENTER_CROSSING_TRAVEL" for i in rep.issues)

def test_out_of_hoop():
    m = MachineProfile("tiny", hoop=__import__("embcopilot.machine", fromlist=["HoopProfile"]).HoopProfile("h", 50, 50, 5))
    p = _plan([_s(0, 0), _s(80, 0), Stitch(80, 0, "END", "c", "", 0, "e")])
    assert any(i.code == "OUT_OF_HOOP" for i in run_qc(p, [], None, m).issues)

def test_diff_plans_reports_deltas():
    a = _crossing_plan(); b, _ = optimize_travel(a, [_zone()])
    d = diff_plans(a, b, [_zone()])
    assert d["metrics"]["center_crossing_travel"]["new"] == 0 and "center_crossing_travel" in d["improved"]

def test_negative_space_and_zone_from_ring():
    from embcopilot.domain import EmbroideryObject
    o = EmbroideryObject("r", "ring", "border", geometry=[(10, 10), (90, 10), (90, 90), (10, 90)], bbox_mm=(10, 10, 90, 90),
                         holes_mm=[[(30, 30), (70, 30), (70, 70), (30, 70)]])
    ns = extract_negative_space([o]); assert ns and ns[0].parent_object_id == "r" and ns[0].area_mm2 > 1500
    z = build_protected_zones([o], (100, 100))
    assert len(z) == 1 and z[0].kind == "enclosed_empty"
