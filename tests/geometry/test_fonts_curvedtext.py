import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from embcopilot.golden import find_default_font as font
from embcopilot.fonts import glyph_outline, kern_pair_mm, analyse_text, scan_fonts, FontError, _open
from embcopilot.curvedtext import layout_text_on_circle, counters_preserved, TextMode

F = font()

def test_font_found_and_scan():
    assert F and os.path.exists(F)
    assert len(scan_fonts(limit=20)) >= 1

def test_counters_preserved_for_O_B_A_P_R_D_Q():
    for ch, n in (("O", 1), ("B", 2), ("A", 1), ("P", 1), ("R", 1), ("D", 1), ("Q", 1)):
        g = glyph_outline(F, ch, 10.0)
        assert len(g.shapes) >= 1
        assert sum(len(s.holes) for s in g.shapes) == n, ch   # never a solid disc

def test_cap_height_scaling():
    g = glyph_outline(F, "H", 10.0)
    ys = [p[1] for s in g.shapes for p in s.outer]
    assert abs(max(ys) - 10.0) < 0.25

def test_missing_glyph_is_reported_not_substituted():
    g = glyph_outline(F, "\u2603", 10.0)   # snowman, absent from most fonts
    if g.missing:
        from embcopilot.fonts import analyse_glyph
        assert analyse_glyph(g).status == "UNSUITABLE"

def test_bad_font_path_friendly_error():
    try:
        _open("/no/such/font.ttf"); assert False
    except FontError as e:
        assert "FONT_NOT_FOUND" in str(e)

def test_kerning_pair_returns_number():
    assert isinstance(kern_pair_mm(F, "A", "V", 10.0), float)

def test_suitability_size_warning_and_status():
    big = analyse_text(F, "OBA", 12.0)
    small = analyse_text(F, "OBA", 3.0)
    assert small["status"] in ("WARNING", "UNSUITABLE")
    assert any("SIZE_WARNING" in n for n in small["notes"])
    assert big["status"] in ("SAFE", "WARNING")

def _dists(ct):
    return [math.hypot(x - ct.center[0], y - ct.center[1]) for g in ct.placements for s in g.shapes for x, y in s.outer]

def test_arc_length_spacing_equal_advance():
    ct = layout_text_on_circle("HHHH", F, 30, 4, (50, 50), 90, "OUTWARD_READABLE", letter_spacing_mm=1.0, kerning=False)
    steps = [ct.placements[i + 1].arc_start_mm - ct.placements[i].arc_start_mm for i in range(3)]
    assert max(steps) - min(steps) < 1e-6   # constant pitch along the baseline

def test_top_text_upright_bottom_text_flipped():
    top = layout_text_on_circle("TOP", F, 30, 4, (50, 50), 90, "BOTTOM_AUTO_FLIP")
    bot = layout_text_on_circle("BOTTOM", F, 30, 4, (50, 50), 270, "BOTTOM_AUTO_FLIP")
    assert top.upright_fraction == 1.0 and not top.inward
    assert bot.upright_fraction == 1.0 and bot.inward          # inward => readable at the bottom
    # bottom glyphs sit INSIDE the baseline, top glyphs OUTSIDE
    assert min(_dists(bot)) < 30 - 3 and min(_dists(top)) > 30 - 0.3

def test_radius_envelope_follow_baseline_bends():
    ct = layout_text_on_circle("BEND", F, 20, 5, (0, 0), 90, "FOLLOW_BASELINE")
    assert abs(ct.inner_radius_mm - 20) < 0.6 and abs(ct.outer_radius_mm - 25) < 0.8

def test_counters_survive_circular_layout():
    ct = layout_text_on_circle("OBA", F, 30, 6, (0, 0), 90, "OUTWARD_READABLE")
    assert counters_preserved(ct) == {"O": 1, "B": 2, "A": 1}

def test_sector_justification_solves_spacing():
    ct = layout_text_on_circle("ABCDE", F, 30, 4, (0, 0), 90, "OUTWARD_READABLE", start_angle_deg=130, end_angle_deg=50)
    assert abs(abs(ct.end_angle_deg - ct.start_angle_deg) - 80) < 1.0

def test_too_long_text_warns():
    ct = layout_text_on_circle("X" * 80, F, 10, 4, (0, 0), 90, "OUTWARD_READABLE")
    assert any("TEXT_" in w for w in ct.warnings)

def test_rotation_continuity_no_flips():
    ct = layout_text_on_circle("CONTINUITY", F, 30, 4, (0, 0), 90, "OUTWARD_READABLE")
    rots = [p.rotation_deg for p in ct.placements]
    assert all(abs(rots[i + 1] - rots[i]) < 25 for i in range(len(rots) - 1))
