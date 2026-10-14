import os, tempfile
import cv2, numpy as np
from embcopilot.threads import (ThreadDatabase, ThreadColor, build_colorway, delta_e2000, rgb_to_lab, hex_to_rgb, describe_delta_e)
from embcopilot.fabrics import FABRICS, get_fabric, pull_compensation, check_hoop, HOOPS, fabric_warnings
from embcopilot.pipeline import run_pipeline
import pytest


def test_delta_e2000_matches_published_reference_pairs():   # Sharma, Wu & Dalal (2005) test data
    assert abs(delta_e2000((50, 2.6772, -79.7751), (50, 0, -82.7485)) - 2.0425) < 1e-3
    assert abs(delta_e2000((50, 2.5, 0), (73, 25, -18)) - 27.1492) < 1e-3
    assert delta_e2000((60, 10, 10), (60, 10, 10)) == 0

def test_lab_of_white_and_black():
    L, a, b = rgb_to_lab((255, 255, 255)); assert abs(L - 100) < 0.1 and abs(a) < 0.5 and abs(b) < 0.5
    assert rgb_to_lab((0, 0, 0))[0] < 0.1

def test_generic_palette_is_labelled_not_a_brand():
    db = ThreadDatabase.generic()
    assert db.brands == ["Generic-24"] and len(db.threads) == 24
    assert all("not a brand" in dict(t.metadata)["source"] for t in db.threads)
    assert not any(b in " ".join(db.brands).lower() for b in ("madeira", "isacord", "gunold"))

def test_perceptual_match_beats_rgb_distance_case():
    db = ThreadDatabase.generic()
    de, t = db.candidates(hex_to_rgb("#1E6ACB"))[0]
    assert t.name == "Blue" and de < 6

def test_csv_import_and_brand_selection():
    p = os.path.join(tempfile.mkdtemp(), "pal.csv")
    open(p, "w").write("code,name,hex\n1234,Test Red,#FF0000\n5678,Test Blue,#0000FF\n")
    db = ThreadDatabase.generic(); assert db.load_csv(p, "MyBrand") == 2
    de, t = db.candidates((250, 10, 10), "MyBrand")[0]
    assert (t.brand, t.code) == ("MyBrand", "1234")
    with pytest.raises(ValueError):
        db.candidates((1, 2, 3), "NoSuchBrand")

def test_min_threads_mode_reuses_threads_and_reports_error():
    db = ThreadDatabase.generic()
    cols = ["rgb(31,95,191)", "rgb(40,100,195)", "rgb(200,16,46)"]
    a = build_colorway(cols, db, None, "nearest"); b = build_colorway(cols, db, None, "min_threads")
    assert b.thread_count <= a.thread_count
    assert all(e.delta_e >= 0 for e in b.entries)

def test_manual_override_and_unknown_code():
    db = ThreadDatabase.generic()
    cw = build_colorway(["rgb(255,0,0)"], db, None, "manual", manual={"rgb(255,0,0)": "G22"})
    assert cw.entries[0].thread.code == "G22" and cw.entries[0].note == "manual choice"
    with pytest.raises(ValueError):
        build_colorway(["rgb(255,0,0)"], db, None, "manual", manual={"rgb(255,0,0)": "ZZZ"})

def test_colorways_do_not_touch_geometry():
    img = np.full((300, 300, 3), 255, np.uint8); cv2.circle(img, (150, 150), 90, (0, 0, 200), -1, cv2.LINE_AA)
    p = os.path.join(tempfile.mkdtemp(), "c.png"); cv2.imwrite(p, img)
    r1 = run_pipeline(p, (60, 60), brand="Generic-24", colorway_mode="nearest")
    r2 = run_pipeline(p, (60, 60), brand="Generic-24", colorway_mode="min_threads")
    assert [(s.x, s.y) for s in r1.plan.stitches] == [(s.x, s.y) for s in r2.plan.stitches]

def test_unloaded_brand_is_reported_not_faked():
    img = np.full((200, 200, 3), 255, np.uint8); cv2.circle(img, (100, 100), 60, (0, 0, 200), -1)
    p = os.path.join(tempfile.mkdtemp(), "c.png"); cv2.imwrite(p, img)
    r = run_pipeline(p, (50, 50), brand="Madeira")
    assert any("THREAD_BRAND_NOT_LOADED" in w for w in r.warnings)
    assert all(e.thread.brand == "Generic-24" for e in r.colorway.entries)

def test_describe_delta_e_never_says_exact():
    for d in (0, 0.5, 1.5, 3, 7, 20):
        assert "exact" not in describe_delta_e(d)

# ------------------------------------------------------------------ fabrics
def test_all_spec_fabrics_exist():
    for n in "T_SHIRT JERSEY DENIM TOWEL PIQUE TWILL CANVAS FLEECE CAP PATCH STRETCH_FABRIC".split():
        assert n in FABRICS

def test_pull_compensation_is_monotonic_in_stretch_and_density():
    args = dict(stitch_type="satin", width_mm=4.0, length_mm=30, direction_deg=0, underlay="center_run")
    firm = pull_compensation(FABRICS["CANVAS"], density_mm=0.4, **args).amount_mm
    stretchy = pull_compensation(FABRICS["STRETCH_FABRIC"], density_mm=0.4, **args).amount_mm
    dense = pull_compensation(FABRICS["CANVAS"], density_mm=0.25, **args).amount_mm
    assert stretchy > firm and dense > firm

def test_pull_compensation_underlay_and_run_rules():
    a = pull_compensation(FABRICS["T_SHIRT"], "satin", 4, 30, 0.4, 0, "center_run").amount_mm
    b = pull_compensation(FABRICS["T_SHIRT"], "satin", 4, 30, 0.4, 0, "zigzag").amount_mm
    assert b < a
    assert pull_compensation(FABRICS["T_SHIRT"], "run", 1, 30, 2.2, 0, "none").amount_mm == 0
    fill = pull_compensation(FABRICS["T_SHIRT"], "tatami", 10, 30, 0.4, 0, "tatami").amount_mm
    assert 0 < fill < a

def test_pull_output_has_reason_and_capped_confidence():
    pc = pull_compensation(FABRICS["TOWEL"], "satin", 5, 30, 0.4, 30, "zigzag")
    assert "Heuristic" in pc.reason and pc.confidence < 0.9 and abs(pc.direction_deg - 120) < 1e-6

def test_towel_warns_and_changes_plan_vs_tshirt():
    img = np.full((300, 300, 3), 255, np.uint8); cv2.rectangle(img, (40, 140), (260, 160), (0, 0, 200), -1)
    p = os.path.join(tempfile.mkdtemp(), "b.png"); cv2.imwrite(p, img)
    t = run_pipeline(p, (60, 60), fabric="TOWEL"); s = run_pipeline(p, (60, 60), fabric="T_SHIRT")
    pt, ps = t.technical.plans[0], s.technical.plans[0]
    assert (pt.density_mm, pt.underlay, pt.pull_compensation_mm) != (ps.density_mm, ps.underlay, ps.pull_compensation_mm)
    assert any("pile" in w.lower() for w in pt.warnings)

def test_hoop_check_valid_and_exceeds():
    assert check_hoop(80, 90, HOOPS["generic-100x100"])["status"] == "VALID"
    r = check_hoop(96, 90, HOOPS["generic-100x100"]); assert r["status"] == "DESIGN_EXCEEDS_HOOP"
