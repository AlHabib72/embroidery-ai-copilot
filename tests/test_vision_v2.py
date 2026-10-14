"""Behavioural tests for Vision V2: every assertion checks measured geometry
on synthetic images whose ground truth is known by construction."""

import math
import os
import tempfile

import cv2
import numpy as np
import pytest

from embcopilot.vision import LocalVisionProvider, export_debug

MM = (80.0, 80.0)  # 200px -> 80mm : 0.4 mm/px


def _save(img):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.png")
    cv2.imwrite(p, img)
    return p


def _analyze(img, **kw):
    return LocalVisionProvider(**kw).analyze(_save(img), MM)


def _white():
    return np.full((200, 200, 3), 255, np.uint8)


def _bbox_area(o):
    return (o.bbox_mm[2] - o.bbox_mm[0]) * (o.bbox_mm[3] - o.bbox_mm[1])


# ---- background ------------------------------------------------------------
def test_white_background_is_not_an_object():
    img = _white()
    cv2.circle(img, (100, 100), 40, (0, 0, 200), -1)
    bp = _analyze(img)
    assert len(bp.objects) == 1
    assert bp.background["mode"] == "solid"
    # nothing may cover the whole canvas
    assert _bbox_area(bp.objects[0]) < 0.4 * 80 * 80


def test_near_white_and_coloured_background_removed():
    for bg in [(250, 250, 248), (200, 230, 240)]:
        img = np.full((200, 200, 3), bg, np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 200), -1)
        bp = _analyze(img)
        assert len(bp.objects) == 1, bg
        w = bp.objects[0].bbox_mm
        assert abs((w[2] - w[0]) - 40.0) < 1.0  # 100px * 0.4mm


def test_transparent_png_uses_alpha_not_black_fill():
    rgba = np.zeros((200, 200, 4), np.uint8)
    cv2.circle(rgba, (100, 100), 60, (0, 0, 200, 255), -1, lineType=cv2.LINE_AA)
    bp = _analyze(rgba)
    assert bp.background["mode"] == "alpha"
    assert len(bp.objects) == 1
    assert bp.objects[0].color_group == "rgb(200,0,0)"


def test_gradient_background_removed():
    xs = np.linspace(255, 200, 200, dtype=np.float32)
    img = np.repeat(xs[None, :, None], 200, 0).repeat(3, 2).astype(np.uint8)
    cv2.circle(img, (100, 100), 40, (0, 0, 180), -1)
    bp = _analyze(img)
    assert len(bp.objects) == 1
    assert bp.background["mode"] in ("gradient", "solid")


def test_full_bleed_image_warns_instead_of_guessing():
    img = np.zeros((200, 200, 3), np.uint8)
    img[:, :100] = (0, 0, 200)
    img[:, 100:] = (200, 0, 0)
    bp = _analyze(img)
    assert bp.background["mode"] == "none_detected"
    assert any("border" in w for w in bp.warnings)


def test_blank_image_gives_no_objects():
    bp = _analyze(_white())
    assert bp.objects == []
    assert any("only background" in w for w in bp.warnings)


# ---- holes -------------------------------------------------------------------
def test_ring_keeps_its_hole_and_true_area():
    img = _white()
    cv2.circle(img, (100, 100), 60, (0, 0, 200), -1)
    cv2.circle(img, (100, 100), 30, (255, 255, 255), -1)
    o = _analyze(img).objects[0]
    assert len(o.holes_mm) == 1
    expect = math.pi * (60**2 - 30**2) * 0.16  # px^2 * (0.4mm)^2
    assert abs(o.area_mm2 - expect) / expect < 0.06
    assert o.area_mm2 < 0.8 * _bbox_area(o)  # not the filled bbox


def test_letter_B_like_shape_two_holes():
    img = _white()
    cv2.rectangle(img, (50, 30), (150, 170), (0, 0, 0), -1)
    cv2.rectangle(img, (75, 50), (125, 80), (255, 255, 255), -1)
    cv2.rectangle(img, (75, 110), (125, 145), (255, 255, 255), -1)
    o = _analyze(img).objects[0]
    assert len(o.holes_mm) == 2


def test_tiny_hole_is_filtered_as_noise():
    img = _white()
    cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 200), -1)
    cv2.circle(img, (100, 100), 1, (255, 255, 255), -1)  # ~3px speck
    o = _analyze(img).objects[0]
    assert o.holes_mm == []


def test_separate_objects_of_same_colour_stay_separate():
    img = _white()
    cv2.circle(img, (50, 100), 30, (0, 0, 200), -1)
    cv2.circle(img, (150, 100), 30, (0, 0, 200), -1)
    assert len(_analyze(img).objects) == 2


# ---- anti-aliasing -------------------------------------------------------------
def test_antialiased_edges_do_not_create_extra_objects():
    img = _white()
    cv2.circle(img, (60, 100), 35, (0, 160, 0), -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (140, 100), 35, (200, 0, 0), -1, lineType=cv2.LINE_AA)
    bp = _analyze(img)
    assert len(bp.objects) == 2


def test_jpeg_artifacts_do_not_create_extra_objects():
    img = _white()
    cv2.circle(img, (100, 100), 60, (0, 0, 200), -1, lineType=cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 30])
    dec = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    assert len(_analyze(dec).objects) == 1


def test_original_image_untouched_in_debug_copy():
    img = _white()
    cv2.circle(img, (100, 100), 40, (0, 0, 200), -1)
    bp = _analyze(img)
    assert (bp.debug_images["original"] == img).all()


# ---- width / centerline ----------------------------------------------------------
def test_horizontal_bar_width_is_measured_not_bbox():
    img = _white()
    cv2.rectangle(img, (20, 95), (180, 104), (0, 100, 0), -1)  # 10px tall = 4.0mm
    o = _analyze(img).objects[0]
    assert abs(o.width_stats_mm["median"] - 4.0) < 0.6
    assert o.centerline_mm is not None
    ys = [p[1] for p in o.centerline_mm]
    assert max(ys) - min(ys) < 1.0  # straight line


def test_diagonal_stem_width_is_not_the_bbox_size():
    img = _white()
    cv2.line(img, (20, 180), (180, 20), (0, 100, 0), 6, lineType=cv2.LINE_AA)
    o = _analyze(img).objects[0]
    bbox_w = o.bbox_mm[2] - o.bbox_mm[0]
    assert bbox_w > 55  # the old proxy would have said ~64mm wide
    assert o.width_stats_mm["median"] < 4.0
    assert o.centerline_mm is not None and len(o.centerline_mm) > 10


def test_wide_blob_gets_no_forced_centerline():
    img = _white()
    cv2.circle(img, (100, 100), 60, (0, 0, 200), -1)
    o = _analyze(img).objects[0]
    assert o.centerline_mm is None


def test_y_shape_reports_branch_point():
    img = _white()
    for a, b in [((100, 190), (100, 110)), ((100, 110), (50, 30)), ((100, 110), (150, 30))]:
        cv2.line(img, a, b, (0, 100, 0), 7)
    o = _analyze(img).objects[0]
    assert o.skeleton_topology["endpoints"] >= 3
    assert o.skeleton_topology["branch_points"] >= 1


# ---- export --------------------------------------------------------------------
def test_debug_export_writes_numbered_artifacts():
    img = _white()
    cv2.circle(img, (100, 100), 40, (0, 0, 200), -1)
    bp = _analyze(img)
    out = tempfile.mkdtemp()
    written = export_debug(bp, out)
    names = sorted(os.path.basename(p) for p in written)
    assert names == ["01_original.png", "02_background_mask.png", "03_segmentation.png",
                     "04_contours.png", "05_centerlines.png", "06_object_blueprint.json"]
    m = cv2.imread(os.path.join(out, "02_background_mask.png"), 0)
    assert m[0, 0] == 255 and m[100, 100] == 0  # corner = background, centre = not


# ---- resolution stability & performance ------------------------------------------
def test_object_count_is_stable_across_export_resolutions():
    base = _white()[:200, :200]
    img = np.full((400, 400, 3), 255, np.uint8)
    cv2.circle(img, (120, 120), 70, (0, 0, 180), -1, lineType=cv2.LINE_AA)
    cv2.circle(img, (120, 120), 35, (255, 255, 255), -1, lineType=cv2.LINE_AA)
    cv2.putText(img, "A", (220, 330), cv2.FONT_HERSHEY_SIMPLEX, 3, (150, 60, 0), 8, cv2.LINE_AA)
    cv2.line(img, (200, 60), (380, 220), (30, 90, 30), 8, cv2.LINE_AA)
    counts = []
    for size in (200, 400, 1200):
        r = cv2.resize(img, (size, size), interpolation=cv2.INTER_CUBIC if size > 400 else cv2.INTER_AREA)
        counts.append(len(_analyze(r).objects))
    assert counts == [3, 3, 3], counts


def test_large_image_is_analysed_at_bounded_resolution():
    import time
    img = np.full((3000, 3000, 3), 255, np.uint8)
    cv2.circle(img, (1500, 1500), 900, (0, 0, 200), -1, lineType=cv2.LINE_AA)
    t = time.time()
    bp = _analyze(img)
    assert time.time() - t < 15
    assert len(bp.objects) == 1
    assert any("downscaled" in w for w in bp.warnings)
    assert bp.image_size_px == (3000, 3000)  # original size still recorded
