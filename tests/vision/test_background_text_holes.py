"""Vision-level regression: background is not an object, holes survive, rings stay rings (synthetic art)."""
import os, tempfile
import cv2, numpy as np
from embcopilot.pipeline import run_pipeline
from embcopilot.golden import write_golden
from embcopilot.qc_v2 import run_qc
from embcopilot.negative_space import build_protected_zones

def _run(name):
    d = tempfile.mkdtemp()
    p = write_golden(d)[name]
    return run_pipeline(p, (100, 100))

def test_background_never_stitched_circle():
    R = _run("01_circle")
    assert len(R.objects) == 1
    ob = R.objects[0]
    assert ob.width_mm < 85          # the white page (100 mm) was NOT turned into an object
    assert not any(i.code == "BACKGROUND_AS_STITCH" for i in run_qc(R.plan, R.objects, None).issues)

def test_ring_keeps_its_hole_and_protected_centre():
    R = _run("02_ring")
    ob = R.objects[0]
    assert len(ob.holes_mm) == 1                          # ring, not a solid disc
    zones = build_protected_zones(R.objects, R.plan.canvas_mm)
    assert len(zones) == 1 and zones[0].kind == "enclosed_empty"
    assert run_qc(R.plan, R.objects, zones).metrics["center_crossing_travel"] == 0

def test_glyph_counter_preserved_in_raster_pipeline():
    from embcopilot.fonts import glyph_outline
    from embcopilot.golden import find_default_font
    g = glyph_outline(find_default_font(), "O", 60.0)
    im = np.full((400, 400, 3), 255, np.uint8)
    for s in g.shapes:
        cv2.fillPoly(im, [np.array([(x + 100, 330 - y) for x, y in s.outer], np.int32)], (0, 0, 0))
        for h in s.holes:
            cv2.fillPoly(im, [np.array([(x + 100, 330 - y) for x, y in h], np.int32)], (255, 255, 255))
    d = tempfile.mkdtemp(); p = os.path.join(d, "o.png"); cv2.imwrite(p, im)
    R = run_pipeline(p, (60, 60))
    assert any(o.holes_mm for o in R.objects)

def test_multi_object_logo_has_hole_and_three_colours():
    R = _run("12_multi_object_logo")
    assert len({o.color_group for o in R.objects}) == 3
    assert any(o.holes_mm for o in R.objects)
