import math, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from embcopilot.circlefit import fit_circle, infer_circle, circle_polygon, Circle
from embcopilot.badge import CircularBadgePlanner
from embcopilot.satin_v2 import satin_ring, satin_from_centerline, satin_split_strategy

def test_fit_circle_recovers_noisy_circle():
    rng = np.random.default_rng(1)
    t = np.linspace(0, 2 * math.pi, 200, endpoint=False)
    pts = [(30 + 12 * math.cos(a) + rng.normal(0, .05), 40 + 12 * math.sin(a) + rng.normal(0, .05)) for a in t]
    c = fit_circle(pts)
    assert abs(c.cx - 30) < .1 and abs(c.cy - 40) < .1 and abs(c.r - 12) < .1 and c.coverage_deg > 350

def test_infer_circle_rejects_square_and_arc():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)] * 1
    sq = [(x, y) for i in range(10) for x, y in [(i, 0)]] + [(10, i) for i in range(10)] + [(10 - i, 10) for i in range(10)] + [(0, 10 - i) for i in range(10)]
    assert infer_circle(sq) is None
    arc = [(10 * math.cos(a), 10 * math.sin(a)) for a in np.linspace(0, 2.0, 60)]
    assert infer_circle(arc) is None            # coverage too small -> keep raw contour

def test_circle_polygon_chord_error():
    c = Circle(0, 0, 20, 0, 0, 360)
    poly = circle_polygon(c, 0.03)
    r = [math.hypot(*p) for p in poly]
    mid = [math.hypot((poly[i][0] + poly[(i + 1) % len(poly)][0]) / 2, (poly[i][1] + poly[(i + 1) % len(poly)][1]) / 2) for i in range(len(poly))]
    assert 20 - min(mid) <= 0.031

def test_satin_ring_rails_are_exact_circles():
    r = satin_ring(0, 0, 20, 3.5, 0.4)
    assert all(abs(math.hypot(*p) - 18.25) < 1e-6 for p in r.left_rail)
    assert all(abs(math.hypot(*p) - 21.75) < 1e-6 for p in r.right_rail)
    assert r.oscillations == 0

def test_input_c_variable_width_and_no_oscillation():
    s = satin_from_centerline([(0, 0), (10, 0), (20, 5), (30, 5)], lambda t: 2 + 4 * t, 0.4)
    assert abs(min(s.widths_mm) - 2) < 0.05 and abs(max(s.widths_mm) - 6) < 0.05
    assert s.oscillations == 0

def test_satin_corner_warns_unsafe_turn():
    s = satin_from_centerline([(0, 0), (10, 0), (10, 10)], 3.0, 0.4)
    assert any("UNSAFE_TURN" in w for w in s.warnings)

def test_satin_split_strategy_never_forces_wide_satin():
    assert satin_split_strategy(3.5)["strategy"] == "single_column"
    assert satin_split_strategy(12)["columns"] == 2
    assert satin_split_strategy(30)["strategy"] == "tatami_conversion" and satin_split_strategy(30)["warning"]

def _badge_mask():
    import cv2
    m = np.zeros((400, 400), np.uint8)
    cv2.circle(m, (200, 200), 190, 255, -1); cv2.circle(m, (200, 200), 170, 0, -1)
    cv2.circle(m, (200, 200), 110, 255, -1); cv2.circle(m, (200, 200), 98, 0, -1)
    for k in range(12):
        a = math.radians(30 + k * 10); x = int(200 + 140 * math.cos(a)); y = int(200 - 140 * math.sin(a)); cv2.rectangle(m, (x - 5, y - 8), (x + 5, y + 8), 255, -1)
    cv2.rectangle(m, (170, 170), (230, 230), 255, -1); cv2.rectangle(m, (185, 185), (215, 215), 0, -1)
    return m

def test_badge_planner_detects_parts_in_order():
    plan = CircularBadgePlanner().analyze(_badge_mask(), 0.25)
    assert plan.detected
    assert plan.roles()[:4] == ["outer_border", "inner_ring", "circular_text", "center_emblem"]
    assert "negative_space" in plan.roles()
    d = {p.role: p for p in plan.parts}
    assert abs(d["outer_border"].detail["width_mm"] - 20 * 0.25) < 0.4

def test_badge_planner_rejects_non_badge():
    import cv2
    m = np.zeros((300, 300), np.uint8); cv2.rectangle(m, (50, 50), (250, 250), 255, -1)
    assert not CircularBadgePlanner().analyze(m).detected
    m2 = np.zeros((300, 300), np.uint8); cv2.circle(m2, (150, 150), 100, 255, -1)
    assert not CircularBadgePlanner().analyze(m2).detected      # solid disc is not a badge
