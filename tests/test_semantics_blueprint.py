"""Semantics, hierarchy, occlusion. Ground truth is known by construction."""
import os, tempfile
import cv2, numpy as np

from embcopilot.vision import LocalVisionProvider
from embcopilot.blueprint import build_blueprint, merge_continuations, apply_blueprint
from embcopilot.semantics import apply_semantics, register_semantic_type, SEMANTIC_TYPES

C = (100.0, 100.0)

def _bp(img):
    p = os.path.join(tempfile.mkdtemp(), "t.png"); cv2.imwrite(p, img)
    return LocalVisionProvider().analyze(p, C)

def _white(n=400): return np.full((n, n, 3), 255, np.uint8)

def _pipeline(img):
    bp = _bp(img)
    g0 = build_blueprint(bp.objects, C)
    objs = merge_continuations(bp.objects, g0)
    g = build_blueprint(objs, C); apply_blueprint(objs, g)
    apply_semantics(objs, C, {c: p for c, p in g.parent.items() if p})
    return objs, g

def test_ring_with_dot_inside_gives_hierarchy_and_eye_candidate():
    img = _white()
    cv2.circle(img, (200, 200), 120, (0, 0, 180), -1, cv2.LINE_AA)
    cv2.circle(img, (200, 200), 70, (255, 255, 255), -1, cv2.LINE_AA)
    cv2.circle(img, (200, 200), 20, (0, 0, 0), -1, cv2.LINE_AA)
    objs, g = _pipeline(img)
    ring = max(objs, key=lambda o: len(o.holes_mm))
    dot = min(objs, key=lambda o: o.area_mm2)
    assert dot.parent_object_id == ring.object_id
    assert dot.object_id in ring.child_object_ids
    assert dot.z_order > ring.z_order
    assert ring.category == "decoration"
    assert dot.category == "eye" and dot.confidence["semantic"].value < 0.8  # heuristic, never near-certain
    assert any(a["type"] == "SMALL_DETAIL" for a in dot.semantic_alternatives)

def test_leaf_and_stem_classification_with_alternatives():
    img = _white()
    cv2.line(img, (40, 360), (360, 360), (30, 90, 30), 8, cv2.LINE_AA)
    cv2.ellipse(img, (200, 200), (90, 40), 20, 0, 360, (50, 160, 50), -1, cv2.LINE_AA)
    objs, _ = _pipeline(img)
    cats = sorted(o.category for o in objs)
    assert "leaf" in cats and "stem" in cats
    leaf = next(o for o in objs if o.category == "leaf")
    assert leaf.semantic_alternatives and leaf.semantic_alternatives[0]["type"] in ("PETAL", "WING")
    assert leaf.semantic_alternatives[0]["confidence"] < leaf.confidence["semantic"].value
    assert leaf.semantic_reason

def test_confidence_is_evidence_based_not_constant():
    img = _white()
    cv2.line(img, (40, 360), (360, 360), (30, 90, 30), 8, cv2.LINE_AA)
    cv2.ellipse(img, (200, 200), (90, 40), 20, 0, 360, (50, 160, 50), -1, cv2.LINE_AA)
    cv2.rectangle(img, (300, 40), (330, 60), (0, 0, 0), -1)
    objs, _ = _pipeline(img)
    vals = [round(o.confidence["semantic"].value, 2) for o in objs]
    assert len(set(vals)) >= 3, vals
    det = [round(o.confidence["detection"].value, 2) for o in objs]
    assert len(set(det)) >= 2

def test_occluded_branch_is_merged_and_occluders_sewn_after():
    img = _white()
    cv2.line(img, (40, 360), (360, 60), (30, 90, 30), 8, cv2.LINE_AA)
    for c, a in [((150, 250), 30), ((250, 160), -30)]:
        cv2.ellipse(img, c, (45, 20), a, 0, 360, (50, 160, 50), -1, cv2.LINE_AA)
    bp = _bp(img)
    n_before = len(bp.objects)
    g0 = build_blueprint(bp.objects, C)
    assert len(g0.continuations) == 2                      # two gaps hidden by two leaves
    objs = merge_continuations(bp.objects, g0)
    assert len(objs) == n_before - 2                       # 3 branch pieces -> 1 object
    branch = max(objs, key=lambda o: len(o.centerline_mm or []))
    xs = [p[0] for p in branch.centerline_mm]
    assert max(xs) - min(xs) > 60                          # spans the whole diagonal, gaps bridged
    g = build_blueprint(objs, C)
    deps = g0.dependencies()
    occluders = {c["occluder"] for c in g0.continuations}
    for c in g0.continuations:  # the occluder must be sewn AFTER every piece it hides
        assert set(c["pieces"]) <= set(deps[c["occluder"]])
    assert all(c["confidence"] < 0.96 for c in g0.continuations)  # never claims certainty

def test_touching_objects_are_related_not_merged():
    img = _white()
    cv2.rectangle(img, (60, 100), (200, 300), (0, 0, 200), -1)
    cv2.rectangle(img, (201, 100), (340, 300), (0, 160, 0), -1)
    objs, g = _pipeline(img)
    assert len(objs) == 2
    assert any(r.kind == "TOUCHES" for r in g.relations)

def test_taxonomy_is_extensible():
    register_semantic_type("feather")
    assert "FEATHER" in SEMANTIC_TYPES
    for base in ["BODY","HEAD","WING","TAIL","BEAK","EYE","LEG","FOOT","STEM","LEAF","FLOWER","PETAL","BRANCH","OUTLINE","TEXT","SMALL_DETAIL","BACKGROUND","DECORATION","UNKNOWN"]:
        assert base in SEMANTIC_TYPES

def test_text_row_detected_from_repeated_glyph_geometry():
    img = _white()
    for i in range(4):
        x = 60 + i * 80
        cv2.rectangle(img, (x, 150), (x + 50, 250), (0, 0, 0), -1)
        cv2.rectangle(img, (x + 15, 175), (x + 35, 225), (255, 255, 255), -1)
    objs, _ = _pipeline(img)
    assert sum(o.category == "text" for o in objs) == 4
