"""Golden artwork generator + regression metrics (v0.6).

All golden designs are SYNTHETIC (generated here, no licensing issue).  Real user artwork can be dropped
into ``golden_user/`` and benchmarked the same way.  Circular text is drawn with the CurvedTextEngine so
the badge golden exercises the real glyph pipeline.
"""
from __future__ import annotations

import json
import math
import os
import time

import numpy as np


def _canvas(px=600):
    return np.full((px, px, 3), 255, np.uint8)


def g01_circle(px=600):
    import cv2
    im = _canvas(px); cv2.circle(im, (px // 2, px // 2), int(px * 0.38), (20, 20, 20), -1); return im


def g02_ring(px=600):
    import cv2
    im = _canvas(px); c = (px // 2, px // 2)
    cv2.circle(im, c, int(px * 0.40), (20, 20, 20), -1); cv2.circle(im, c, int(px * 0.33), (255, 255, 255), -1); return im


def _fill_shapes(im, shapes, color, mm_to_px, off):
    import cv2
    for sh in shapes:
        cv2.fillPoly(im, [np.array([(x * mm_to_px + off[0], y * mm_to_px + off[1]) for x, y in sh.outer], np.int32)], color)
        for h in sh.holes:
            cv2.fillPoly(im, [np.array([(x * mm_to_px + off[0], y * mm_to_px + off[1]) for x, y in h], np.int32)], (255, 255, 255))


def find_default_font() -> str | None:
    from .fonts import scan_fonts
    pref = ("DejaVuSans-Bold", "DejaVuSans", "LiberationSans-Bold", "Arial")
    fs = scan_fonts(limit=400)
    for p in pref:
        for f in fs:
            if os.path.basename(f.path).startswith(p):
                return f.path
    return fs[0].path if fs else None


def g11_complex_badge(px=700, text_top="LOUIS VUITTON", text_bottom="PARIS 1854", font=None):
    """Circular badge: outer border ring, circular text, inner ring, central 'LV' monogram."""
    import cv2
    from .curvedtext import layout_text_on_circle
    font = font or find_default_font()
    im = _canvas(px); c = px // 2; S = px / 100.0   # 100 mm design -> px
    cv2.circle(im, (c, c), int(47 * S), (20, 20, 20), -1); cv2.circle(im, (c, c), int(43.5 * S), (255, 255, 255), -1)
    cv2.circle(im, (c, c), int(27 * S), (20, 20, 20), -1); cv2.circle(im, (c, c), int(24.5 * S), (255, 255, 255), -1)
    for txt, ang in ((text_top, 90), (text_bottom, 270)):
        ct = layout_text_on_circle(txt, font, 35.5 if ang == 90 else 39.0, 5.0, (50, 50), ang, "BOTTOM_AUTO_FLIP", letter_spacing_mm=0.8)
        for g in ct.placements:
            _fill_shapes(im, g.shapes, (20, 20, 20), S, (0, 0))
    # monogram
    from .fonts import glyph_outline
    for ch, dx in (("L", -5.5), ("V", 3.0)):
        g = glyph_outline(font, ch, 14.0)
        shapes = [type(s)([(x + 50 + dx - g.advance / 2, 50 + 7 - y) for x, y in s.outer], [[(x + 50 + dx - g.advance / 2, 50 + 7 - y) for x, y in h] for h in s.holes]) for s in g.shapes]
        _fill_shapes(im, shapes, (20, 20, 20), S, (0, 0))
    return im


def g12_multi_object(px=600):
    import cv2
    im = _canvas(px)
    cv2.circle(im, (200, 200), 90, (180, 30, 30), -1)
    cv2.rectangle(im, (330, 120), (520, 300), (30, 30, 180), -1); cv2.rectangle(im, (390, 180), (460, 240), (255, 255, 255), -1)
    cv2.ellipse(im, (300, 450), (140, 60), 0, 0, 360, (30, 140, 30), -1)
    return im


GOLDEN = {"01_circle": g01_circle, "02_ring": g02_ring, "11_complex_badge": g11_complex_badge, "12_multi_object_logo": g12_multi_object}


def write_golden(out_dir: str) -> dict:
    import cv2
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for name, fn in GOLDEN.items():
        p = os.path.join(out_dir, name + ".png")
        cv2.imwrite(p, fn()); paths[name] = p
    return paths


def benchmark(out_dir: str, target_mm=(100.0, 100.0), profile="balanced", fabric="woven", names=None, machine="generic_flat") -> dict:
    """Run the full pipeline + QC v2 over the golden set and write benchmark.json.  Timings are real, measured."""
    from .pipeline import run_pipeline
    from .negative_space import build_protected_zones
    from .qc_v2 import run_qc, plan_metrics
    paths = write_golden(os.path.join(out_dir, "golden"))
    rows = {}
    for name, p in paths.items():
        if names and name not in names:
            continue
        t0 = time.time()
        try:
            R = run_pipeline(p, target_mm, profile=profile, fabric=fabric)
            zones = build_protected_zones(R.objects, R.plan.canvas_mm)
            rep = run_qc(R.plan, R.objects, zones, machine)
            m = plan_metrics(R.plan, zones)
            from .production import production_pass
            t1 = time.time()
            pp = production_pass(R, machine)
            rows[name] = {"ok": True, "seconds": round(time.time() - t0, 2), "metrics": m,
                          "qc_errors": len(rep.errors), "qc_warnings": len(rep.by_severity("WARNING")),
                          "qc_codes": sorted({i.code for i in rep.issues}), "protected_zones": len(zones),
                          "after_production_pass": {"seconds": round(time.time() - t1, 2), "metrics": plan_metrics(pp.plan_after, zones),
                                                    "qc_errors": len(pp.qc_after.errors), "qc_codes": sorted({i.code for i in pp.qc_after.issues}),
                                                    "gates_passed": [g["passed"] for g in pp.gates]}}
        except Exception as e:  # noqa: BLE001 - benchmark must report, never crash
            rows[name] = {"ok": False, "seconds": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}
    res = {"target_mm": target_mm, "profile": profile, "fabric": fabric, "machine": machine, "designs": rows,
           "note": "synthetic goldens; timings measured on this machine; metrics = v0.5 pipeline baseline, after_production_pass = v0.6 pass; not a sew-out result"}
    with open(os.path.join(out_dir, "benchmark.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    return res
