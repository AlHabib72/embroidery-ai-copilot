"""Stitch geometry, plan building, validation, DST export, comparison."""
import math, os, random, tempfile
import cv2, numpy as np

from embcopilot.domain import EmbroideryObject
from embcopilot.geometry import point_in_region, point_in_polygon, polylines_intersect, polygon_area
from embcopilot.strategies import (fill_region, satin_column, satin_ring, build_rails, validate_rails,
                                   StrategyLimits, run_path)
from embcopilot.profiles import get_profile
from embcopilot.sequence import SeqNode, order_objects, DependencyCycleError
from embcopilot.connector import route_connector, ConnectorCostWeights
from embcopilot.stitch import (build_stitch_plan, validate_stitch_plan, to_dst_design, Stitch, StitchPlan, STITCH, JUMP)
from embcopilot.pipeline import run_pipeline

SQ = [(0, 0), (20, 0), (20, 20), (0, 20)]
HOLE = [(7, 7), (13, 7), (13, 13), (7, 13)]

# ---------------------------------------------------------------- fill
def test_fill_keeps_hole_empty_and_stays_in_region():
    runs = fill_region(SQ, [HOLE], 45, 0.4, 3.5)
    pts = [p for r in runs for p in r]
    assert pts
    assert not any(point_in_polygon(p, [(7.1, 7.1), (12.9, 7.1), (12.9, 12.9), (7.1, 12.9)]) for p in pts)
    assert all(-0.01 <= p[0] <= 20.01 and -0.01 <= p[1] <= 20.01 for p in pts)

def test_fill_row_spacing_matches_request():
    runs = fill_region(SQ, [], 0, 0.5, 3.5)           # angle 0 -> horizontal rows
    ys = sorted({round(p[1], 3) for r in runs for p in r})
    gaps = np.diff(ys)
    assert abs(np.median(gaps) - 0.5) < 0.01
    assert abs(len(ys) - 40) <= 1

def test_fill_stitch_length_capped():
    runs = fill_region(SQ, [HOLE], 30, 0.4, 3.5)
    longest = max(math.dist(a, b) for r in runs for a, b in zip(r, r[1:]))
    assert longest <= 3.5 * 1.6 + 1e-6

def test_fill_angle_changes_direction():
    r0 = fill_region(SQ, [], 0, 0.5, 3.5)[0]
    r90 = fill_region(SQ, [], 90, 0.5, 3.5)[0]
    assert abs(r0[1][1] - r0[0][1]) < 1e-6      # horizontal row
    assert abs(r90[1][0] - r90[0][0]) < 1e-6    # vertical row

# ---------------------------------------------------------------- satin
def _stem(width=3.0, n=40, curve=0.0):
    cl = [(i * 0.75, 10 + curve * math.sin(i / 8)) for i in range(n)]
    return EmbroideryObject("s", "stem", centerline_mm=cl, centerline_widths_mm=[width] * n,
                            geometry=[(0, 8), (30, 8), (30, 12), (0, 12)], bbox_mm=(0, 8, 30, 12),
                            skeleton_topology={"endpoints": 2, "branch_points": 0})

def test_satin_rails_do_not_cross_and_width_matches():
    o = _stem(3.0)
    r = satin_column(o, 0.4, 0.0, "center_run", StrategyLimits(), (40, 40))
    assert not r.errors
    L, R = r.rails["left"], r.rails["right"]
    assert not polylines_intersect(L, R)
    w = np.hypot(*(np.asarray(L) - np.asarray(R)).T)
    assert abs(np.median(w) - 3.0) < 0.05

def test_satin_pull_compensation_widens_column():
    a = satin_column(_stem(3.0), 0.4, 0.0, "none", StrategyLimits(), (40, 40)).rails
    b = satin_column(_stem(3.0), 0.4, 0.4, "none", StrategyLimits(), (40, 40)).rails
    wa = np.median(np.hypot(*(np.asarray(a["left"]) - np.asarray(a["right"])).T))
    wb = np.median(np.hypot(*(np.asarray(b["left"]) - np.asarray(b["right"])).T))
    assert abs((wb - wa) - 0.4) < 0.05

def test_satin_density_controls_stitch_count():
    n = lambda d: len(satin_column(_stem(3.0), d, 0.0, "none", StrategyLimits(), (40, 40)).parts[-1].points)
    assert n(0.3) > n(0.6) * 1.8

def test_satin_too_wide_and_too_narrow_are_rejected_not_generated():
    assert any("SATIN_TOO_WIDE" in e for e in satin_column(_stem(14.0), 0.4, 0, "none", StrategyLimits(), (40, 40)).errors)
    assert any("SATIN_TOO_NARROW" in e for e in satin_column(_stem(0.3), 0.4, 0, "none", StrategyLimits(), (40, 40)).errors)

def test_tight_curve_with_wide_column_is_rejected():
    n = 60
    ang = np.linspace(0, math.pi, n)
    cl = [(10 + 3 * math.cos(a), 10 + 3 * math.sin(a)) for a in ang]   # radius 3mm arc
    o = EmbroideryObject("c", "x", centerline_mm=cl, centerline_widths_mm=[8.0] * n,
                         geometry=SQ, bbox_mm=(0, 0, 20, 20), skeleton_topology={"branch_points": 0})
    r = satin_column(o, 0.4, 0, "none", StrategyLimits(), (40, 40))
    assert r.errors and any("RAIL" in e for e in r.errors)     # half-width 4 > radius 3 -> invalid
    assert not any(p.kind == "cover" and p.points for p in r.parts)

def test_branched_shape_refuses_single_column_satin():
    o = _stem(3.0); o.skeleton_topology = {"branch_points": 2}
    assert any("COMPLEX_TOPOLOGY" in e for e in satin_column(o, 0.4, 0, "none", StrategyLimits(), (40, 40)).errors)

def test_satin_ring_uses_outer_and_hole_as_rails():
    t = np.linspace(0, 2 * math.pi, 120, endpoint=False)
    outer = [(20 + 10 * math.cos(a), 20 + 10 * math.sin(a)) for a in t]
    hole = [(20 + 6 * math.cos(a), 20 + 6 * math.sin(a)) for a in t]
    o = EmbroideryObject("r", "ring", geometry=outer, holes_mm=[hole], bbox_mm=(10, 10, 30, 30))
    r = satin_ring(o, 0.4, 0.0, StrategyLimits(), (40, 40))
    assert not r.errors
    pts = r.parts[-1].points
    radii = [math.dist(p, (20, 20)) for p in pts]
    assert min(radii) > 5.9 and max(radii) < 10.1          # stitches only inside the band
    assert abs(min(radii) - 6) < 0.15 and abs(max(radii) - 10) < 0.15
    assert pts[0] == pts[-1]                                # closed

# ---------------------------------------------------------------- run
def test_run_stitch_length_follows_profile():
    o = _stem(1.0)
    a = run_path(o, "run", get_profile("quality_first")).parts[0].points
    b = run_path(o, "run", get_profile("speed_first")).parts[0].points
    assert len(a) > len(b)

def test_triple_run_goes_out_back_out():
    o = _stem(1.0)
    single = run_path(o, "run", get_profile("balanced")).parts[0].points
    triple = run_path(o, "triple_run", get_profile("balanced")).parts[0].points
    assert len(triple) == 3 * len(single) - 2
    assert triple[0] == single[0] and triple[len(single) - 1] == single[-1]

def test_no_geometry_gives_explicit_unavailable_not_fake_stitches():
    o = EmbroideryObject("x", "x")
    r = run_path(o, "run", get_profile("balanced"))
    assert r.errors and "GEOMETRY_UNAVAILABLE" in r.errors[0] and not r.ok

# ---------------------------------------------------------------- sequence / connector
def _n(i, c, x, z=0): return SeqNode(i, c, (x, 0), (x + 1, 0), z)

def test_sequence_never_violates_dependency_to_save_travel():
    nodes = [_n("far_base", "r", 100, 0), _n("near_detail", "r", 1, 1)]
    order = order_objects(nodes, {"near_detail": ["far_base"]})
    assert order == ["far_base", "near_detail"]

def test_sequence_groups_colours_when_free():
    nodes = [_n("a1", "red", 0), _n("b1", "blue", 5), _n("a2", "red", 10), _n("b2", "blue", 15)]
    order = order_objects(nodes, {})
    changes = sum(1 for x, y in zip(order, order[1:]) if x[0] != y[0])
    assert changes <= 1

def test_sequence_detects_cycle():
    try:
        order_objects([_n("a", "r", 0), _n("b", "r", 1)], {"a": ["b"], "b": ["a"]})
        assert False
    except DependencyCycleError:
        pass

def test_property_random_dags_always_respected():
    rnd = random.Random(7)
    for _ in range(60):
        n = rnd.randint(2, 12)
        nodes = [_n(f"o{i}", rnd.choice("rgb"), rnd.uniform(0, 100), rnd.randint(0, 3)) for i in range(n)]
        deps = {f"o{i}": [f"o{j}" for j in range(i) if rnd.random() < 0.3] for i in range(n)}
        order = order_objects(nodes, deps)
        pos = {o: k for k, o in enumerate(order)}
        assert len(order) == n
        assert all(pos[d] < pos[k] for k, v in deps.items() for d in v)

def test_connector_hidden_when_path_under_next_object():
    inside = lambda x, y: True
    d = route_connector((0, 0), (3, 0), inside, inside)
    assert d.strategy == "HIDDEN_STITCH"

def test_connector_open_fabric_long_gap_trims_under_quality_but_jumps_when_speed_first():
    never = lambda x, y: False
    q = route_connector((0, 0), (6, 0), never, never, ConnectorCostWeights.from_profile("quality_first"))
    s = route_connector((0, 0), (6, 0), never, never, ConnectorCostWeights.from_profile("speed_first"))
    assert q.strategy == "TRIM_JUMP" and s.strategy == "JUMP"
    assert set(q.candidates) >= {"JUMP", "TRIM_JUMP"}

def test_connector_jump_over_existing_objects_is_less_visible_than_over_bare_fabric():
    never = lambda x, y: False; always = lambda x, y: True
    bare = route_connector((0, 0), (6, 0), never, never)
    covered = route_connector((0, 0), (6, 0), never, always)
    assert covered.cost < bare.cost and covered.visible_fraction < bare.visible_fraction

# ---------------------------------------------------------------- full pipeline (golden, tolerant)
def _art(kind):
    img = np.full((400, 400, 3), 255, np.uint8)
    if kind == "floral":
        cv2.line(img, (40, 360), (360, 60), (30, 90, 30), 8, cv2.LINE_AA)
        for c, a in [((150, 250), 30), ((250, 160), -30)]:
            cv2.ellipse(img, c, (45, 20), a, 0, 360, (50, 160, 50), -1, cv2.LINE_AA)
        cv2.circle(img, (300, 300), 70, (0, 0, 180), -1, cv2.LINE_AA); cv2.circle(img, (300, 300), 40, (255, 255, 255), -1, cv2.LINE_AA)
    elif kind == "badge":
        cv2.circle(img, (200, 200), 150, (0, 0, 160), -1, cv2.LINE_AA); cv2.circle(img, (200, 200), 100, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.rectangle(img, (150, 170), (250, 230), (0, 120, 200), -1)
    elif kind == "logo":
        cv2.rectangle(img, (60, 60), (200, 340), (40, 40, 40), -1)
        cv2.circle(img, (290, 200), 80, (0, 100, 220), -1, cv2.LINE_AA)
    p = os.path.join(tempfile.mkdtemp(), f"{kind}.png"); cv2.imwrite(p, img)
    return p

def test_pipeline_floral_golden_tolerances():
    r = run_pipeline(_art("floral"), (100, 100))
    assert r.stopped is None
    assert 4 <= len(r.objects) <= 6                               # 7 vision regions -> occlusion merge
    cats = {o.category for o in r.objects}
    assert {"leaf", "decoration"} <= cats
    assert r.validation.status in ("VALID", "WARNING")
    assert r.comparison_global["coverage_of_reference"] > 0.9
    assert r.comparison_global["stitched_outside_reference"] < 0.05
    assert all(c.status in ("MATCH", "WARNING") for c in r.comparison)
    st = r.plan.stats()
    assert st["stitch_count"] > 500 and st["color_count"] >= 3

def test_pipeline_badge_and_logo_generate_valid_plans():
    for kind in ("badge", "logo"):
        r = run_pipeline(_art(kind), (100, 100))
        assert r.validation.status != "ERROR", (kind, [f.message for f in r.validation.findings])
        assert r.comparison_global["coverage_of_reference"] > 0.9

def test_pipeline_profile_changes_actual_stitch_count():
    q = run_pipeline(_art("logo"), (100, 100), "quality_first").plan.stats()["stitch_count"]
    s = run_pipeline(_art("logo"), (100, 100), "speed_first").plan.stats()["stitch_count"]
    assert q > s * 1.05

def test_pipeline_fails_safe_on_blank_image():
    p = os.path.join(tempfile.mkdtemp(), "blank.png"); cv2.imwrite(p, np.full((200, 200, 3), 255, np.uint8))
    r = run_pipeline(p, (50, 50))
    assert r.stopped and r.plan is None                            # nothing downstream generated

def test_pipeline_dependencies_and_colour_changes_in_output():
    r = run_pipeline(_art("floral"), (100, 100))
    pos = {o: i for i, o in enumerate(r.plan.order)}
    for k, v in r.graph.dependencies().items():
        for d in v:
            if k in pos and d in pos:
                assert pos[d] < pos[k]
    cmds = [s.command for s in r.plan.stitches]
    assert cmds.count("COLOR_CHANGE") == r.plan.stats()["color_count"] - 1
    assert cmds[-1] == "END"

def test_pipeline_writes_all_numbered_artifacts_and_dst_roundtrips():
    from embcopilot.dst import read_dst
    from embcopilot.analyze import analyze
    out = tempfile.mkdtemp()
    r = run_pipeline(_art("badge"), (100, 100), out_dir=out)
    names = set(os.listdir(out))
    for n in ["01_reference.png", "02_background_removed.png", "03_segmentation.png", "04_objects.json",
              "05_object_blueprint.json", "06_technical_blueprint.json", "07_stitch_geometry.json",
              "08_sequence.json", "09_connectors.json", "10_qc_report.json", "11_simulation.png",
              "12_action_plan.json", "design.dst", "playback.html"]:
        assert n in names, n
    d = read_dst(os.path.join(out, "design.dst"))
    a = analyze(d)
    assert a.stitches == r.plan.stats()["stitch_count"]            # exported == generated
    assert a.colors == r.plan.stats()["color_count"]
    assert abs(a.width_mm - (max(s.x for s in r.plan.stitches if s.command == STITCH) - min(s.x for s in r.plan.stitches if s.command == STITCH))) < 0.3

def test_action_plan_is_plan_only_with_real_points():
    import json
    out = tempfile.mkdtemp()
    r = run_pipeline(_art("logo"), (100, 100), out_dir=out)
    data = json.load(open(os.path.join(out, "12_action_plan.json")))
    assert "nothing executed" in data["MODE"].lower()
    geo = [a for a in data["actions"] if a["action"] == "create_geometry"]
    assert geo and all(len(a["params"]["points_mm"]) >= 3 for a in geo)
    assert all(a["requires_confirmation"] for a in data["actions"])

# ---------------------------------------------------------------- validator catches real defects
def _plan_with(stitches, order=("o",)):
    return StitchPlan((20, 20), stitches, list(order))

def _o(): return EmbroideryObject("o", "o", geometry=[(2, 2), (18, 2), (18, 18), (2, 18)], bbox_mm=(2, 2, 18, 18), width_stats_mm={"median": 5})

def test_validator_flags_out_of_bounds_nan_long_and_color_errors():
    st = [Stitch(5, 5, JUMP, "rgb(1,1,1)", "o"), Stitch(5, 5, STITCH, "rgb(1,1,1)", "o", part="cover:x"),
          Stitch(19.9, 5, STITCH, "rgb(1,1,1)", "o", part="cover:x"),            # 14.9 mm stitch
          Stitch(float("nan"), 1, STITCH, "rgb(1,1,1)", "o", part="cover:x"),
          Stitch(200, 5, STITCH, "rgb(1,1,1)", "o", part="cover:x"),
          Stitch(6, 6, STITCH, "rgb(9,9,9)", "o", part="cover:x")]               # colour switch w/o COLOR_CHANGE
    codes = {f.code for f in validate_stitch_plan(_plan_with(st), [_o()]).findings}
    assert {"NON_FINITE_COORDINATE", "OUT_OF_BOUNDS", "STITCH_EXCEEDS_MACHINE_LIMIT", "MISSING_COLOR_CHANGE"} <= codes

def test_validator_flags_stitches_outside_region_and_dependency_violation():
    st = [Stitch(15 + i * 0.4, 30, STITCH, "c", "o", part="cover:x") for i in range(20)]
    rep = validate_stitch_plan(StitchPlan((40, 40), st, ["o", "p"]), [_o(), EmbroideryObject("p", "p", geometry=SQ, bbox_mm=(0, 0, 20, 20))], {"o": ["p"]})
    codes = {f.code for f in rep.findings}
    assert "STITCHES_OUTSIDE_REGION" in codes and "DEPENDENCY_VIOLATED" in codes and rep.status == "ERROR"

def test_validator_reports_valid_on_clean_generated_plan():
    r = run_pipeline(_art("logo"), (100, 100))
    assert r.validation.status in ("VALID", "WARNING")

def test_export_blocked_for_stitch_over_machine_limit():
    plan = _plan_with([Stitch(0, 0, JUMP, "c", "o"), Stitch(0, 0, STITCH, "c", "o"), Stitch(15, 0, STITCH, "c", "o"), Stitch(15, 0, "END", "c", "")])
    try:
        to_dst_design(plan); assert False
    except ValueError:
        pass

# ---------------------------------------------------------------- properties over random shapes
def test_property_random_convex_fills_stay_inside_and_finite():
    rnd = random.Random(3)
    for _ in range(25):
        n = rnd.randint(5, 10)
        ang = sorted(rnd.uniform(0, 2 * math.pi) for _ in range(n))
        poly = [(30 + rnd.uniform(6, 14) * math.cos(a), 30 + rnd.uniform(6, 14) * math.sin(a)) for a in ang]
        if polygon_area(poly) < 30: continue
        angle = rnd.uniform(0, 180); sp = rnd.uniform(0.3, 0.8)
        runs = fill_region(poly, [], angle, sp, 3.5)
        for r in runs:
            for p in r:
                assert math.isfinite(p[0]) and math.isfinite(p[1])
                assert point_in_polygon((p[0], p[1]), poly) or min(math.dist(p, q) for q in poly) < 30
        pts = [p for r in runs for p in r]
        xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
        assert all(min(xs) - 1e-6 <= p[0] <= max(xs) + 1e-6 and min(ys) - 1e-6 <= p[1] <= max(ys) + 1e-6 for p in pts)

def test_property_pipeline_every_stitch_belongs_to_valid_object():
    r = run_pipeline(_art("floral"), (100, 100))
    ids = {o.object_id for o in r.objects}
    assert all((s.object_id in ids) or (s.object_id == "" and s.command in ("TRIM", "END")) for s in r.plan.stitches)
    assert all(math.isfinite(s.x) and math.isfinite(s.y) for s in r.plan.stitches)

def test_performance_pipeline_1024px_under_budget():
    import time
    img = np.full((1024, 1024, 3), 255, np.uint8)
    cv2.circle(img, (300, 300), 200, (0, 0, 160), -1, cv2.LINE_AA); cv2.circle(img, (300, 300), 110, (255, 255, 255), -1, cv2.LINE_AA)
    cv2.ellipse(img, (700, 650), (220, 90), 30, 0, 360, (30, 150, 30), -1, cv2.LINE_AA)
    p = os.path.join(tempfile.mkdtemp(), "big.png"); cv2.imwrite(p, img)
    t = time.time(); r = run_pipeline(p, (120, 120))
    assert time.time() - t < 20
    assert r.validation.status != "ERROR"


def test_occluding_leaves_are_sewn_after_the_branch_they_hide():
    """Regression for a bug found by rendering: merged branch was sewn over the leaves."""
    r = run_pipeline(_art("floral"), (100, 100))
    branch = max(r.objects, key=lambda o: len(o.centerline_mm or []))
    leaves = [o for o in r.objects if o.category == "leaf"]
    assert len(leaves) == 2
    pos = {o: i for i, o in enumerate(r.plan.order)}
    for lf in leaves:
        assert pos[branch.object_id] < pos[lf.object_id]
        assert branch.object_id in r.graph.dependencies()[lf.object_id]
        assert lf.z_order > branch.z_order


def _segments_crossing_hole(runs, outer, holes, tol=0.03):
    """Count segments whose midpoint is truly outside the region. Rows end ON the boundary and are joined
    along it, so a midpoint within `tol` of a boundary counts as inside (same rule the engine uses)."""
    from embcopilot.geometry import point_in_polygon, dist_point_to_polyline
    bad = 0
    for r in runs:
        for a, b in zip(r, r[1:]):
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            near = min(dist_point_to_polyline(m, ring, True) for ring in [outer] + list(holes)) <= tol
            in_hole = any(point_in_polygon(m, h) for h in holes)
            if not near and (in_hole or not point_in_polygon(m, outer)):
                bad += 1
    return bad

def test_fill_never_bridges_a_hole_regression():
    """Found by rendering the 'A' glyph: joins between row segments crossed the counter."""
    for hole in (HOLE, [(4, 4), (16, 4), (16, 16), (4, 16)], [(9, 2), (11, 2), (11, 18), (9, 18)]):
        for ang in (0, 30, 45, 90, 135):
            runs = fill_region(SQ, [hole], ang, 0.4, 3.5)
            assert _segments_crossing_hole(runs, SQ, [hole]) == 0, (hole, ang)

def test_fill_never_bridges_a_concavity():
    U = [(0, 0), (20, 0), (20, 20), (14, 20), (14, 6), (6, 6), (6, 20), (0, 20)]   # U shape
    for ang in (0, 20, 90):
        runs = fill_region(U, [], ang, 0.5, 3.5)
        assert _segments_crossing_hole(runs, U, []) == 0

def test_validator_catches_a_stitch_that_bridges_a_hole():
    o = EmbroideryObject("o", "o", geometry=SQ, holes_mm=[HOLE], bbox_mm=(0, 0, 20, 20), width_stats_mm={"median": 5})
    st = [Stitch(3, 10, JUMP, "c", "o"), Stitch(3, 10, STITCH, "c", "o", part="cover:x"), Stitch(17, 10, STITCH, "c", "o", part="cover:x")]
    codes = {f.code for f in validate_stitch_plan(StitchPlan((20, 20), st, ["o"]), [o]).findings}
    assert "STITCH_CROSSES_HOLE_OR_OUTSIDE" in codes

def test_pipeline_glyph_counter_stays_open():
    img = np.full((400, 400, 3), 255, np.uint8)
    cv2.putText(img, "A", (100, 330), cv2.FONT_HERSHEY_SIMPLEX, 8, (150, 60, 0), 22, cv2.LINE_AA)
    p = os.path.join(tempfile.mkdtemp(), "A.png"); cv2.imwrite(p, img)
    r = run_pipeline(p, (100, 100))
    o = [x for x in r.objects if x.holes_mm][0]
    hole = o.holes_mm[0]
    from embcopilot.geometry import point_in_polygon
    stream = [s for s in r.plan.stitches if s.command != "TRIM"]
    crossing = sum(1 for a, b in zip(stream, stream[1:]) if a.command == b.command == STITCH
                   and a.object_id == b.object_id == o.object_id
                   and point_in_polygon(((a.x + b.x) / 2, (a.y + b.y) / 2), hole))
    assert crossing == 0


def test_pipeline_result_is_stable_across_export_resolutions():
    """Same artwork at 5 resolutions -> same objects, same semantics, same stitch strategies."""
    seen = []
    for n in (400, 768, 1024, 1600, 3000):
        img = np.full((n, n, 3), 255, np.uint8); s = n / 400
        cv2.line(img, (int(40*s), int(360*s)), (int(360*s), int(60*s)), (30, 90, 30), int(8*s), cv2.LINE_AA)
        for c, a in [((150, 250), 30), ((250, 160), -30)]:
            cv2.ellipse(img, (int(c[0]*s), int(c[1]*s)), (int(45*s), int(20*s)), a, 0, 360, (50, 160, 50), -1, cv2.LINE_AA)
        cv2.circle(img, (int(300*s), int(300*s)), int(70*s), (0, 0, 180), -1, cv2.LINE_AA)
        cv2.circle(img, (int(300*s), int(300*s)), int(40*s), (255, 255, 255), -1, cv2.LINE_AA)
        p = os.path.join(tempfile.mkdtemp(), "x.png"); cv2.imwrite(p, img)
        r = run_pipeline(p, (100, 100))
        seen.append((len(r.objects), tuple(sorted(o.category for o in r.objects)),
                     tuple(sorted(st["strategy"] for st in r.plan.object_status.values())), r.validation.status != "ERROR"))
    assert len(set(seen)) == 1, seen


def test_satin_tapered_tips_are_allowed_but_narrow_interior_is_not():
    n = 60
    w = [0.3 + 2.7 * min(1, i / 6, (n - 1 - i) / 6) for i in range(n)]      # tapers to 0.3mm at both tips
    cl = [(i * 0.5, 10.0) for i in range(n)]
    o = EmbroideryObject("t", "t", centerline_mm=cl, centerline_widths_mm=w, geometry=SQ, bbox_mm=(0, 0, 30, 20),
                         skeleton_topology={"branch_points": 0})
    assert not satin_column(o, 0.4, 0, "none", StrategyLimits(), (40, 40)).errors
    o.centerline_widths_mm = [0.4] * n
    assert any("TOO_NARROW" in e for e in satin_column(o, 0.4, 0, "none", StrategyLimits(), (40, 40)).errors)
