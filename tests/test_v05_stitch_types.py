import math, os, tempfile
import cv2, numpy as np
from embcopilot.domain import EmbroideryObject
from embcopilot.geometry import point_in_region, polygon_area
from embcopilot.strategies import applique, puff, cross_stitch, split_gradient, StrategyLimits, generate_object
from embcopilot.pipeline import run_pipeline
from embcopilot.formats import export_design

SQ = [(5, 5), (35, 5), (35, 35), (5, 35)]
def sq_obj(**kw):
    return EmbroideryObject("o", "o", geometry=list(SQ), bbox_mm=(5, 5, 35, 35), width_stats_mm={"median": 30}, area_mm2=900, **kw)
LIM = StrategyLimits(); CV = (40, 40)

# ---------------------------------------------------------------- applique
def test_applique_sequence_placement_stop_tack_stop_border():
    r = applique(sq_obj(), LIM, CV)
    assert not r.errors
    names = [p.name for p in r.parts]
    assert names == ["applique:placement_run", "control:STOP_place_fabric", "applique:tack_down", "control:STOP_trim_fabric", "cover:applique_border"]
    assert [p.command for p in r.parts if p.command] == ["STOP", "STOP"]

def test_applique_placement_follows_outline_and_border_straddles_it():
    r = applique(sq_obj(), LIM, CV)
    pl = r.parts[0].points
    assert all(abs(x - 5) < 0.6 or abs(x - 35) < 0.6 or abs(y - 5) < 0.6 or abs(y - 35) < 0.6 for x, y in pl)
    b = r.parts[-1].points
    xs = [p[0] for p in b]; assert min(xs) < 5 and max(xs) > 35                       # border extends beyond the outline
    assert min(xs) > 5 - 2.6 and max(xs) < 35 + 2.6                                     # ...by about half the border width each way

def test_applique_border_width_parameter_is_honoured():
    a = applique(sq_obj(params={"applique_border_mm": 1.5}), LIM, CV).parts[-1].points
    b = applique(sq_obj(params={"applique_border_mm": 3.0}), LIM, CV).parts[-1].points
    assert (max(p[0] for p in b) - min(p[0] for p in b)) > (max(p[0] for p in a) - min(p[0] for p in a)) + 1.0

def test_applique_too_small_is_rejected():
    o = EmbroideryObject("o", "o", geometry=[(0, 0), (4, 0), (4, 4), (0, 4)], bbox_mm=(0, 0, 4, 4))
    r = applique(o, LIM, CV); assert r.errors and "APPLIQUE_TOO_SMALL" in r.errors[0]

# ---------------------------------------------------------------- puff
def test_puff_structure_stop_for_foam_and_dense_cover():
    r = puff(sq_obj(), 45, LIM, CV)
    assert not r.errors
    names = [p.name for p in r.parts]
    i = names.index("control:STOP_place_foam")
    assert all(n.startswith("puff:") for n in names[:i] if n)                          # base before foam stop
    assert any(n == "puff:perforation_run" for n in names[i:]) and "cover:puff_border" in names
    cover = [p for p in r.parts if p.name == "cover:puff"]
    ys = sorted({round(pt[1], 2) for c in cover for pt in c.points})
    assert np.median(np.diff(ys)) < 0.5                                                 # denser than a normal fill

def test_puff_warns_or_rejects_unsuitable_shapes():
    thin = EmbroideryObject("o", "o", geometry=[(0, 0), (30, 0), (30, 1.5), (0, 1.5)], bbox_mm=(0, 0, 30, 1.5), width_stats_mm={"median": 1.5})
    assert "PUFF_TOO_NARROW" in puff(thin, 0, LIM, CV).errors[0]
    sparse = puff(sq_obj(params={"puff_density_mm": 0.5}), 45, LIM, CV); assert any("PUFF_DENSITY_SPARSE" in w for w in sparse.warnings)
    big = EmbroideryObject("o", "o", geometry=[(0, 0), (60, 0), (60, 60), (0, 60)], bbox_mm=(0, 0, 60, 60), width_stats_mm={"median": 60})
    assert any("PUFF_LARGE_AREA" in w for w in puff(big, 0, LIM, (70, 70)).warnings)

# ---------------------------------------------------------------- cross stitch
def test_cross_stitch_grid_cells_and_x_geometry():
    r = cross_stitch(sq_obj(params={"cross_count_per_inch": 14}), LIM, CV)
    cell = 25.4 / 14
    pts = r.parts[0].points
    assert len(pts) % 4 == 0
    # every point lies on the grid lattice anchored at the bbox corner
    for x, y in pts:
        assert abs(((x - 5) / cell) - round((x - 5) / cell)) < 1e-6 and abs(((y - 5) / cell) - round((y - 5) / cell)) < 1e-6
    assert all(point_in_region(p, SQ, []) for p in pts)
    n_cells = len(pts) // 4
    assert abs(n_cells - int(30 / cell) ** 2) <= 0 or n_cells == int(30 / cell) ** 2

def test_cross_stitch_count_changes_cell_size():
    a = cross_stitch(sq_obj(params={"cross_count_per_inch": 14}), LIM, CV).parts[0].points
    b = cross_stitch(sq_obj(params={"cross_count_per_inch": 18}), LIM, CV).parts[0].points
    assert len(b) > len(a) * 1.4

def test_cross_stitch_respects_holes_and_shape():
    o = sq_obj(); o.holes_mm = [[(15, 15), (25, 15), (25, 25), (15, 25)]]
    pts = cross_stitch(o, LIM, CV).parts[0].points
    assert not any(15.5 < x < 24.5 and 15.5 < y < 24.5 for x, y in pts)

def test_cross_stitch_too_small():
    o = EmbroideryObject("o", "o", geometry=[(0, 0), (1, 0), (1, 1), (0, 1)], bbox_mm=(0, 0, 1, 1))
    assert "CROSS_STITCH_NO_CELLS" in cross_stitch(o, LIM, CV).errors[0]

# ---------------------------------------------------------------- gradient
def test_gradient_bands_partition_region_and_report_honestly():
    cols = ["rgb(0,0,100)", "rgb(0,0,160)", "rgb(60,60,220)", "rgb(150,150,255)"]
    subs, rep = split_gradient(sq_obj(), 4, cols, 90, (0.35, 0.6), CV)
    assert len(subs) == 4 and rep["thread_count"] == 4 and "not a continuous-tone" in rep["note"]
    assert abs(sum(s.area_mm2 for s in subs) - 900) < 40
    assert [s.color_group for s in subs] == cols
    dens = [s.params["gradient_density_mm"] for s in subs]; assert dens == sorted(dens) and abs(dens[0] - 0.35) < 1e-6 and abs(dens[-1] - 0.6) < 1e-6
    ys = [np.mean([p[1] for p in s.geometry]) for s in subs]; assert ys == sorted(ys)

def test_gradient_validates_arguments():
    try: split_gradient(sq_obj(), 1, ["rgb(0,0,0)"]); assert False
    except ValueError: pass

# ---------------------------------------------------------------- pipeline integration
def _art():
    img = np.full((300, 300, 3), 255, np.uint8); cv2.circle(img, (150, 150), 110, (0, 0, 200), -1, cv2.LINE_AA)
    p = os.path.join(tempfile.mkdtemp(), "c.png"); cv2.imwrite(p, img); return p

def test_override_to_applique_and_puff_reach_stitch_stream_with_stops():
    for st, stops in (("applique", 2), ("puff", 1)):
        r = run_pipeline(_art(), (60, 60), overrides={"decoration": st, "body": st, "unknown": st, "background": st, "small_detail": st})
        types = {v["strategy"] for v in r.plan.object_status.values()}
        assert st in types, (st, r.plan.object_status)
        assert r.plan.stats()["stops"] == stops
        assert any(s.command == "STOP" for s in r.plan.stitches)

def test_stops_survive_dst_export_as_colour_stop_codes():
    r = run_pipeline(_art(), (60, 60), overrides={"decoration": "applique", "body": "applique", "unknown": "applique", "background": "applique", "small_detail": "applique"})
    info = export_design(r.plan, "dst", os.path.join(tempfile.mkdtemp(), "a.dst"))
    assert info["verified"]

def test_override_recorded_as_user_decision():
    r = run_pipeline(_art(), (60, 60), overrides={"decoration": "cross_stitch", "body": "cross_stitch", "unknown": "cross_stitch", "background": "cross_stitch", "small_detail": "cross_stitch"})
    p = r.technical.plans[0]
    assert p.stitch_confidence.value == 1.0 and "override" in p.stitch_reason.lower()
    assert r.plan.stats()["stitch_count"] > 300


def test_failing_override_falls_back_visibly_and_never_drops_the_object():
    img = np.full((300, 300, 3), 255, np.uint8); cv2.rectangle(img, (40, 140), (260, 146), (0, 0, 200), -1)   # 6px bar: too thin for puff
    p = os.path.join(tempfile.mkdtemp(), "t.png"); cv2.imwrite(p, img)
    r = run_pipeline(p, (60, 60), overrides={"decoration": "puff", "unknown": "puff", "stem": "puff", "outline": "puff", "small_detail": "puff", "text": "puff", "leaf": "puff", "body": "puff"})
    st = next(iter(r.plan.object_status.values()))
    assert st["ok"] and st["strategy"] != "puff" and any("OVERRIDE_FAILED_FELL_BACK" in w for w in st["warnings"])
    assert not any(f.code == "OBJECT_NOT_GENERATED" for f in r.validation.findings)

def test_export_refuses_when_validation_has_errors():
    from embcopilot.formats import ExportError
    from embcopilot.stitch import ValidationReport, Finding
    r = run_pipeline(_art(), (60, 60))
    bad = ValidationReport([Finding("ERROR", "OBJECT_NOT_GENERATED", "x")])
    try:
        export_design(r.plan, "dst", os.path.join(tempfile.mkdtemp(), "a.dst"), validation=bad); assert False
    except ExportError as e:
        assert "VALIDATION_ERRORS" in str(e)
