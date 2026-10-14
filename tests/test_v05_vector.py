import math, os, tempfile, zlib
import pytest
from embcopilot.vector import parse_svg, objects_from_vector, parse_ai, VectorError, parse_path_d, parse_transform
from embcopilot.inputs import sniff
from embcopilot.pipeline import run_pipeline
from embcopilot.geometry import polygon_area

def svg(body, w="100mm", h="100mm", vb="0 0 100 100"):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="{vb}">{body}</svg>'

def _objs(body, **kw): return objects_from_vector(parse_svg(svg(body, **kw)))

def test_rect_and_circle_exact_areas():
    o = _objs('<rect x="10" y="10" width="40" height="20" fill="#f00"/><circle cx="70" cy="70" r="15" fill="#00f"/>')
    assert abs(o[0].area_mm2 - 800) < 0.01
    assert abs(o[1].area_mm2 - math.pi * 225) / (math.pi * 225) < 0.01

def test_compound_path_hole_even_odd():
    o = _objs('<path d="M0 0H40V40H0Z M10 10V30H30V10Z" fill="#c00" fill-rule="evenodd"/>')
    assert len(o) == 1 and len(o[0].holes_mm) == 1 and abs(o[0].area_mm2 - (1600 - 400)) < 0.5

def test_islands_inside_holes_are_separate_objects():
    o = _objs('<path d="M0 0H60V60H0Z M10 10V50H50V10Z M20 20H40V40H20Z" fill="#c00"/>')
    assert len(o) == 2
    assert sorted(len(x.holes_mm) for x in o) == [0, 1]

def test_transforms_groups_and_viewbox_scaling():
    d = parse_svg(svg('<g transform="translate(10,20) scale(2)"><rect width="5" height="5" fill="#000"/></g>', w="200mm", h="200mm", vb="0 0 100 100"))
    pts = d.paths[0].subpaths[0]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    assert abs(min(xs) - 20) < 1e-6 and abs(max(xs) - 40) < 1e-6 and abs(min(ys) - 40) < 1e-6   # translate(10,20) scale(2) then viewBox x2

def test_rotate_transform_matrix():
    M = parse_transform("rotate(90)")
    assert abs(M[0, 0]) < 1e-9 and abs(M[1, 0] - 1) < 1e-9

def test_bezier_flattening_accuracy_and_arc():
    subs = parse_path_d("M0 0 C 0 20, 40 20, 40 0", 0.02)[0][0]
    ys = [p[1] for p in subs]
    assert abs(max(ys) - 15.0) < 0.05          # cubic apex = 0.75 * 20
    arc = parse_path_d("M 10 0 A 10 10 0 1 1 -10 0 A 10 10 0 1 1 10 0 Z", 0.02)[0][0]
    assert abs(polygon_area(arc) - math.pi * 100) / (math.pi * 100) < 0.01

def test_relative_and_smooth_commands():
    p = parse_path_d("m 0 0 h 10 v 10 h -10 z")[0]
    assert p[1] is True and abs(polygon_area(p[0]) - 100) < 1e-9

def test_stroke_becomes_centerline_object_with_width():
    o = _objs('<path d="M10 50 L90 50" fill="none" stroke="#080" stroke-width="3"/>')
    assert len(o) == 1 and o[0].centerline_mm and abs(o[0].centerline_widths_mm[0] - 3) < 1e-6

def test_gradient_paint_rejected_with_warning_not_silently_flattened():
    d = parse_svg(svg('<defs><linearGradient id="g"/></defs><rect width="10" height="10" fill="url(#g)"/>'))
    assert not d.paths and any("PAINT_SERVER" in w for w in d.warnings)

def test_text_and_use_flagged_unsupported():
    d = parse_svg(svg('<text x="1" y="1">hi</text><rect width="10" height="10"/>'))
    assert any("<text>" in w for w in d.warnings) and len(d.paths) == 1

def test_target_size_fit_scales_uniformly():
    d = parse_svg(svg('<rect x="10" y="10" width="20" height="10" fill="#000"/>'), target_mm=(40, 40))
    xs = [p[0] for p in d.paths[0].subpaths[0]]; assert abs(max(xs) - 40) < 1e-6

def test_corrupt_svg_and_bad_paint():
    with pytest.raises(VectorError): parse_svg("<svg><g></svg>")
    with pytest.raises(VectorError): parse_svg('<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>')  # no size

def test_svg_pipeline_keeps_exact_geometry_and_never_rasterises_it():
    body = '<circle cx="50" cy="50" r="30" fill="#c00"/>'
    p = os.path.join(tempfile.mkdtemp(), "c.svg"); open(p, "w").write(svg(body))
    r = run_pipeline(p, (100, 100))
    assert r.input_kind == "vector" and r.vision_mode.startswith("Vector")
    o = r.objects[0]
    assert abs(o.area_mm2 - math.pi * 900) / (math.pi * 900) < 0.01           # exact, not pixel-quantised
    assert len(o.geometry) > 40 and r.validation.status != "ERROR"
    assert not any(f.code == "LONG_JUMP" for f in r.validation.findings)   # underlay->cover travel is hidden, not jumped
    assert r.plan.canvas_mm == (100.0, 100.0)

def test_svg_native_size_kept_unless_fit_requested():
    p = os.path.join(tempfile.mkdtemp(), "c.svg"); open(p, "w").write(svg('<rect x="10" y="10" width="20" height="10" fill="#000"/>'))
    a = run_pipeline(p, (50, 50)); b = run_pipeline(p, (50, 50), vector_fit=True)
    assert abs(a.objects[0].area_mm2 - 200) < 0.5 and any("native size" in w for w in a.warnings)
    assert b.objects[0].area_mm2 > 900

def test_svg_ring_becomes_satin_ring_and_stroke_becomes_satin_or_run():
    body = '<path d="M20 20H60V60H20Z M30 30V50H50V30Z" fill="#00c" fill-rule="evenodd"/><path d="M10 90H90" stroke="#080" stroke-width="2" fill="none"/>'
    p = os.path.join(tempfile.mkdtemp(), "s.svg"); open(p, "w").write(svg(body))
    r = run_pipeline(p, (100, 100))
    strategies = sorted(v["strategy"] for v in r.plan.object_status.values())
    assert len(strategies) == 2 and "satin" in strategies

# ---------------------------------------------------------------- AI (PDF-compatible)
def _pdf(content: bytes, compress=True):
    body = zlib.compress(content) if compress else content
    filt = b"/Filter/FlateDecode" if compress else b""
    return (b"%PDF-1.4\n1 0 obj<</Type/Page/MediaBox[0 0 612 792]/Contents 2 0 R>>endobj\n2 0 obj<<" + filt + b"/Length " + str(len(body)).encode()
            + b">>\nstream\n" + body + b"\nendstream\nendobj\ntrailer<<>>\n%%EOF")

def test_ai_pdf_compatible_paths_extracted_with_correct_size():
    p = os.path.join(tempfile.mkdtemp(), "a.ai"); open(p, "wb").write(_pdf(b"1 0 0 rg 100 100 200 100 re f"))
    assert sniff(p).kind == "ai"
    o = objects_from_vector(parse_ai(p))
    assert abs(o[0].area_mm2 - 200 * 100 * (25.4 / 72) ** 2) < 1.0 and o[0].color_group == "rgb(255,0,0)"

def test_ai_uncompressed_stream_and_curve_and_stroke():
    p = os.path.join(tempfile.mkdtemp(), "b.ai")
    open(p, "wb").write(_pdf(b"0 0 1 RG 3 w 100 100 m 200 300 300 300 400 100 c S", compress=False))
    o = objects_from_vector(parse_ai(p))
    assert o and o[0].name.startswith("Stroke")

def test_legacy_or_encrypted_or_empty_ai_is_unsupported_not_screenshotted():
    d = tempfile.mkdtemp()
    open(d + "/ps.ai", "wb").write(b"%!PS-Adobe-3.0 EPSF\n")
    open(d + "/enc.ai", "wb").write(_pdf(b"1 0 0 rg 0 0 10 10 re f").replace(b"trailer<<>>", b"trailer<</Encrypt 9 0 R>>"))
    open(d + "/empty.ai", "wb").write(_pdf(b"BT /F1 12 Tf (hello) Tj ET"))
    for n in ("ps", "enc", "empty"):
        with pytest.raises(VectorError) as e: parse_ai(f"{d}/{n}.ai")
        assert e.value.code == "AI_FILE_UNSUPPORTED_VARIANT"

def test_pipeline_reports_ai_unsupported_cleanly():
    p = os.path.join(tempfile.mkdtemp(), "x.ai"); open(p, "wb").write(_pdf(b"BT (x) Tj ET"))
    r = run_pipeline(p, (50, 50)); assert r.stopped and "AI_FILE_UNSUPPORTED_VARIANT" in r.stopped and r.plan is None

# ---------------------------------------------------------------- sniffing
def test_sniff_trusts_content_over_extension():
    import cv2, numpy as np
    d = tempfile.mkdtemp(); img = np.full((20, 20, 3), 255, np.uint8)
    cv2.imwrite(d + "/real.png", img); os.rename(d + "/real.png", d + "/renamed.jpg")
    s = sniff(d + "/renamed.jpg"); assert s.kind == "png" and s.ok and not s.extension_matches

def test_sniff_rejects_empty_oversize_and_garbage():
    d = tempfile.mkdtemp(); open(d + "/e.png", "wb").close(); open(d + "/g.bin", "wb").write(b"\x00\x01\x02")
    assert not sniff(d + "/e.png").ok and not sniff(d + "/g.bin").ok
    open(d + "/big.png", "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 5000)
    assert "larger" in sniff(d + "/big.png", max_bytes=1000).problem

def test_sniff_detects_svg_dst_pes():
    d = tempfile.mkdtemp()
    open(d + "/a.svg", "w").write(svg("<rect width='1' height='1'/>")); assert sniff(d + "/a.svg").kind == "svg"
    open(d + "/m.pes", "wb").write(b"#PES0001" + b"0" * 50); assert sniff(d + "/m.pes").kind == "pes"
    assert sniff(os.path.join(os.path.dirname(__file__), "..", "samples", "hummingbird_sample.dst")).kind == "dst"

def test_machine_file_input_is_not_digitized():
    r = run_pipeline(os.path.join(os.path.dirname(__file__), "..", "samples", "hummingbird_sample.dst"), (50, 50))
    assert r.stopped and "NOT_DIGITIZABLE" in r.stopped
