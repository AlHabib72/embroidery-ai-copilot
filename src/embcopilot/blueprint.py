"""
Object Blueprint relations (Phase D): containment hierarchy, touch/overlap
graph, occlusion-continuation detection, z-order and sewing dependencies.

What is REAL here: all relations are computed from rasterised object
geometry (holes respected). What is HEURISTIC: occlusion continuation
(a thin object whose two pieces line up across a third object is assumed to
be one object hidden beneath it) — reported with a confidence and always
reviewable; semantic grouping ("Bird > Head > Eye") is NOT inferred, only
geometric containment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .domain import EmbroideryObject
from .geometry import polygon_area, rasterize


@dataclass
class Relation:
    kind: str  # CONTAINS INSIDE INSIDE_HOLE TOUCHES OVERLAPS CONTINUES_UNDER OCCLUDES
    a: str
    b: str
    detail: str = ""
    confidence: float = 1.0


@dataclass
class BlueprintGraph:
    relations: list[Relation] = field(default_factory=list)
    parent: dict[str, str | None] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)
    z_order: dict[str, int] = field(default_factory=dict)
    continuations: list[dict] = field(default_factory=list)

    def dependencies(self) -> dict[str, list[str]]:
        """id -> ids that must be sewn BEFORE it (background -> foreground)."""
        dep: dict[str, set[str]] = {k: set() for k in self.z_order}
        for r in self.relations:
            if r.kind in ("CONTAINS", "INSIDE_HOLE_PARENT"):
                dep[r.b].add(r.a)  # container before child
            elif r.kind == "OCCLUDES":
                dep[r.a].add(r.b)  # a occludes b: b (hidden piece) before a
        return {k: sorted(v) for k, v in dep.items()}

    def to_dict(self) -> dict:
        return {
            "relations": [r.__dict__ for r in self.relations],
            "parent": self.parent, "children": self.children, "z_order": self.z_order,
            "continuations": self.continuations,
        }


def _masks(objs, canvas_mm):
    scale = min(6.0, 500.0 / max(canvas_mm))
    size = (max(8, int(math.ceil(canvas_mm[0] * scale))), max(8, int(math.ceil(canvas_mm[1] * scale))))
    solid, real = {}, {}
    for o in objs:
        solid[o.object_id] = rasterize(o.geometry, [], scale, size) > 0
        real[o.object_id] = rasterize(o.geometry, o.holes_mm, scale, size) > 0
    return scale, size, solid, real


def build_blueprint(objects: list[EmbroideryObject], canvas_mm: tuple[float, float],
                    max_gap_frac: float = 0.25) -> BlueprintGraph:
    import cv2

    g = BlueprintGraph()
    ids = [o.object_id for o in objects]
    byid = {o.object_id: o for o in objects}
    scale, size, solid, real = _masks(objects, canvas_mm)
    area = {i: int(real[i].sum()) for i in ids}
    contains: dict[str, list[str]] = {i: [] for i in ids}

    for a in objects:
        for b in objects:
            if a is b or area[a.object_id] <= area[b.object_id]:
                continue
            sa, rb = solid[a.object_id], real[b.object_id]
            nb = int(rb.sum())
            if nb == 0 or not (sa & rb).any():
                continue
            frac = float((sa & rb).sum()) / nb
            if frac >= 0.95:
                in_hole = not (real[a.object_id] & rb).any()
                kind = "INSIDE_HOLE" if in_hole else "INSIDE"
                contains[a.object_id].append(b.object_id)
                g.relations.append(Relation("CONTAINS", a.object_id, b.object_id, "inside a hole of the container" if in_hole else "fully inside the container's outline"))
    # parent = smallest container
    for i in ids:
        cands = [c for c in ids if i in contains[c]]
        g.parent[i] = min(cands, key=lambda c: area[c]) if cands else None
        g.children[i] = []
    for i, p in g.parent.items():
        if p:
            g.children[p].append(i)

    k = np.ones((5, 5), np.uint8)
    for ai, a in enumerate(objects):
        da = cv2.dilate(real[a.object_id].astype(np.uint8), k) > 0
        for b in objects[ai + 1:]:
            if g.parent[a.object_id] == b.object_id or g.parent[b.object_id] == a.object_id:
                continue
            inter = real[a.object_id] & real[b.object_id]
            if inter.any():
                g.relations.append(Relation("OVERLAPS", a.object_id, b.object_id, f"{inter.sum()/scale**2:.1f} mm² shared"))
            elif (da & real[b.object_id]).any():
                g.relations.append(Relation("TOUCHES", a.object_id, b.object_id, "boundaries within ~0.4mm"))

    _detect_continuations(g, objects, byid, real, scale, canvas_mm, max_gap_frac)

    # z-order: containment depth, occluders above what they hide
    def depth(i):
        d, p = 0, g.parent[i]
        while p:
            d, p = d + 1, g.parent[p]
        return d
    z = {i: depth(i) * 2 for i in ids}
    for c in g.continuations:
        for piece in c["pieces"]:
            z[c["occluder"]] = max(z[c["occluder"]], z[piece] + 1)
    g.z_order = z
    return g


def _end(o, which, span_mm: float = 6.0):
    """End point and outward unit tangent of a centerline. The tangent is
    measured over `span_mm` of ARC LENGTH (not a fixed number of samples, which
    made results depend on image resolution)."""
    cl = np.asarray(o.centerline_mm if which == 1 else o.centerline_mm[::-1], float)
    end = cl[-1]
    seg = np.hypot(*np.diff(cl[::-1], axis=0).T)
    cum = np.cumsum(seg)
    k = int(np.searchsorted(cum, span_mm)) + 1
    k = min(max(k, 1), len(cl) - 1)
    v = end - cl[::-1][k]
    n = float(np.hypot(*v))
    return end, (v / n if n > 1e-9 else v)


def _detect_continuations(g, objects, byid, real, scale, canvas_mm, max_gap_frac):
    thin = [o for o in objects if o.centerline_mm and len(o.centerline_mm) >= 4]
    max_gap = max_gap_frac * min(canvas_mm)
    used: set[tuple[str, int]] = set()
    for i, a in enumerate(thin):
        for b in thin[i + 1:]:
            if a.color_group != b.color_group:
                continue
            best = None
            for ea in (0, 1):
                for eb in (0, 1):
                    pa, ta = _end(a, ea)
                    pb, tb = _end(b, eb)
                    v = pb - pa
                    d = float(np.hypot(*v))
                    if d < 1e-6 or d > max_gap:
                        continue
                    u = v / d
                    if (a.object_id, ea) in used or (b.object_id, eb) in used:
                        continue
                    if float(ta @ u) < math.cos(math.radians(40)) or float(tb @ -u) < math.cos(math.radians(40)):
                        continue
                    wa = float(np.median(a.centerline_widths_mm or [1])); wb = float(np.median(b.centerline_widths_mm or [1]))
                    if not 0.6 <= wa / max(wb, 1e-6) <= 1.67:
                        continue
                    mid = (pa + pb) / 2
                    px, py = int(round(mid[0] * scale)), int(round(mid[1] * scale))
                    occ = None
                    for c in objects:
                        if c is a or c is b:
                            continue
                        m = real[c.object_id]
                        if 0 <= py < m.shape[0] and 0 <= px < m.shape[1] and m[py, px]:
                            occ = c.object_id
                            break
                    if occ is None:
                        continue
                    score = 0.5 + 0.3 * (float(ta @ u) + float(tb @ -u) - 2 * math.cos(math.radians(40))) / (2 - 2 * math.cos(math.radians(40))) + 0.2 * (1 - d / max_gap)
                    if best is None or score > best[0]:
                        best = (score, ea, eb, occ, d)
            if best:
                score, ea, eb, occ, d = best
                used.update({(a.object_id, ea), (b.object_id, eb)})
                g.relations.append(Relation("CONTINUES_UNDER", a.object_id, b.object_id, f"aligned across {d:.1f}mm hidden by {occ}", round(min(0.95, score), 3)))
                g.relations.append(Relation("OCCLUDES", occ, a.object_id, "covers the gap in a continuing object"))
                g.relations.append(Relation("OCCLUDES", occ, b.object_id, "covers the gap in a continuing object"))
                g.continuations.append({"pieces": [a.object_id, b.object_id], "ends": [ea, eb], "occluder": occ,
                                        "gap_mm": round(d, 2), "confidence": round(min(0.95, score), 3)})


def merge_continuations(objects: list[EmbroideryObject], g: BlueprintGraph) -> list[EmbroideryObject]:
    """Return a new object list where each CHAIN of continuation pieces
    (e.g. a branch split by two leaves into 3 pieces) becomes ONE object whose
    centerline runs through the hidden gaps and whose outline is rebuilt from
    offset rails. The occluders are sewn later, so they hide the bridged
    parts (underlap). Originals are not mutated."""
    import copy

    byid = {o.object_id: copy.deepcopy(o) for o in objects}
    adj: dict[str, dict[int, tuple[str, int]]] = {}
    for c in g.continuations:
        (a, b), (ea, eb) = c["pieces"], c["ends"]
        adj.setdefault(a, {})[ea] = (b, eb)
        adj.setdefault(b, {})[eb] = (a, ea)
    done: set[str] = set()
    for start in list(adj):
        if start in done or len(adj[start]) != 1:
            continue  # begin walking only from a chain end
        (enter_end,) = [e for e in (0, 1) if e not in adj[start]]  # free end = chain start
        chain, cur, free = [], start, enter_end
        while True:
            chain.append((cur, free)); done.add(cur)
            far = 1 - free
            nxt = adj.get(cur, {}).get(far)
            if not nxt or nxt[0] in done:
                break
            cur, free = nxt[0], nxt[1]
        cl, ws = [], []
        for oid, fe in chain:
            o = byid[oid]
            c, w = list(o.centerline_mm), list(o.centerline_widths_mm)
            if fe == 1:  # traverse from the free end inward
                c.reverse(); w.reverse()
            cl += c; ws += w
        n = np.asarray(cl); wv = np.asarray(ws) / 2
        d = np.gradient(n, axis=0); nn = np.stack([-d[:, 1], d[:, 0]], 1); nn /= np.maximum(np.hypot(*nn.T), 1e-9)[:, None]
        outline = [tuple(map(float, p)) for p in np.vstack([n + nn * wv[:, None], (n - nn * wv[:, None])[::-1]])]
        m = byid[chain[0][0]]
        m.centerline_mm, m.centerline_widths_mm, m.geometry = cl, ws, outline
        xs = [p[0] for p in outline]; ys = [p[1] for p in outline]
        m.bbox_mm = (min(xs), min(ys), max(xs), max(ys))
        m.area_mm2 = polygon_area(outline)
        m.holes_mm = []
        # the rebuilt object is one open path by construction; per-piece raster-skeleton twigs no longer apply
        m.skeleton_topology = {"endpoints": 2, "branch_points": 0, "closed_loop": False}
        m.source += "+merged_occlusion"
        for oid, _ in chain[1:]:
            m.relations.append({"kind": "MERGED_FROM", "target": oid})
            byid.pop(oid)
    return list(byid.values())


def apply_blueprint(objects: list[EmbroideryObject], g: BlueprintGraph) -> None:
    for o in objects:
        o.parent_object_id = g.parent.get(o.object_id)
        o.child_object_ids = list(g.children.get(o.object_id, []))
        o.z_order = g.z_order.get(o.object_id, 0)
        o.layer = o.z_order
        o.relations = [{"kind": r.kind, "target": r.b if r.a == o.object_id else r.a}
                       for r in g.relations if o.object_id in (r.a, r.b)]


def carry_occlusions(g0: BlueprintGraph, g: BlueprintGraph, merged_objects: list[EmbroideryObject]) -> None:
    """After merge_continuations, the pieces of a chain collapse into one
    object id, so the OCCLUDES relations computed on the ORIGINAL objects
    would silently vanish from the rebuilt graph (the merged branch would then
    be sewn over the leaves that hide it — a real bug found by rendering).
    Re-attach them: occluder depends on the merged object, and sits above it."""
    alias: dict[str, str] = {}
    for o in merged_objects:
        for r in o.relations:
            if r.get("kind") == "MERGED_FROM":
                alias[r["target"]] = o.object_id
    ids = {o.object_id for o in merged_objects}
    seen = set()
    for c in g0.continuations:
        occ = c["occluder"]
        head = alias.get(c["pieces"][0], c["pieces"][0])
        for piece in c["pieces"]:
            target = alias.get(piece, piece)
            if occ in ids and target in ids and occ != target and (occ, target) not in seen:
                seen.add((occ, target))
                g.relations.append(Relation("OCCLUDES", occ, target, "hides part of this (merged) object", c["confidence"]))
                g.z_order[occ] = max(g.z_order.get(occ, 0), g.z_order.get(target, 0) + 1)
