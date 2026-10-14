"""Context-aware short-stitch classification and cleanup (v0.6).

Never "delete everything under 0.3 mm".  Each short segment is classified:
  duplicate / zero_length -> remove
  noise                   -> remove (isolated reversal between long stitches)
  accidental_cluster      -> merge / resample (>= 3 consecutive short, nearly collinear)
  corner                  -> preserve (sharp direction change = required geometry)
  curve_control           -> preserve (short stitches turning consistently on a small radius)
  intentional             -> preserve (lock/tie stitches, satin transitions, explicit short parts)
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field


@dataclass
class ShortStitchReport:
    counts: dict = field(default_factory=dict)
    removed: int = 0
    merged: int = 0
    preserved: int = 0

    def to_dict(self):
        return {"classes": self.counts, "removed": self.removed, "merged": self.merged, "preserved": self.preserved}


def _turn(a, b, c) -> float:
    v1 = (b[0] - a[0], b[1] - a[1]); v2 = (c[0] - b[0], c[1] - b[1])
    l1, l2 = math.hypot(*v1), math.hypot(*v2)
    if l1 < 1e-9 or l2 < 1e-9:
        return 0.0
    cosv = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
    return math.degrees(math.acos(cosv))


def classify_short_stitches(stitches, short_mm: float = 0.3) -> list[tuple[int, str]]:
    """Return [(index_of_stitch_that_ends_the_short_segment, class)] for every short STITCH->STITCH segment."""
    out = []
    n = len(stitches)
    for i in range(1, n):
        a, b = stitches[i - 1], stitches[i]
        if a.command != "STITCH" or b.command != "STITCH" or a.object_id != b.object_id:
            continue
        d = math.dist((a.x, a.y), (b.x, b.y))
        if d >= short_mm:
            continue
        if d < 0.01:
            out.append((i, "duplicate")); continue
        part = (b.part or "")
        if "lock" in part or "tie" in part or "corner" in part or "transition" in part:
            out.append((i, "intentional")); continue
        prev = stitches[i - 2] if i >= 2 and stitches[i - 2].command == "STITCH" else None
        nxt = stitches[i + 1] if i + 1 < n and stitches[i + 1].command == "STITCH" and stitches[i + 1].object_id == b.object_id else None
        t_in = _turn((prev.x, prev.y), (a.x, a.y), (b.x, b.y)) if prev else 0.0
        t_out = _turn((a.x, a.y), (b.x, b.y), (nxt.x, nxt.y)) if nxt else 0.0
        if max(t_in, t_out) >= 60 and min(t_in, t_out) < 150:
            out.append((i, "corner")); continue
        # isolated reversal between longer neighbours -> noise
        if prev and nxt and t_in > 150 and t_out > 150:
            out.append((i, "noise")); continue
        out.append((i, "accidental_candidate"))
    # promote runs of >=3 consecutive candidates to accidental_cluster / curve_control
    res, k = [], 0
    while k < len(out):
        i, c = out[k]
        if c != "accidental_candidate":
            res.append((i, c)); k += 1; continue
        j = k
        while j + 1 < len(out) and out[j + 1][0] == out[j][0] + 1 and out[j + 1][1] == "accidental_candidate":
            j += 1
        run = out[k:j + 1]
        if len(run) >= 3:
            idx = [r[0] for r in run]
            turns = [_turn((stitches[x - 2].x, stitches[x - 2].y), (stitches[x - 1].x, stitches[x - 1].y), (stitches[x].x, stitches[x].y))
                     for x in idx if x >= 2]
            consistent_curve = turns and all(8 <= t <= 50 for t in turns)
            lbl = "curve_control" if consistent_curve else "accidental_cluster"
        else:
            lbl = "accidental_isolated"
        res += [(r[0], lbl) for r in run]
        k = j + 1
    return res


def clean_short_stitches(plan, short_mm: float = 0.3):
    """Return (new_plan, ShortStitchReport).  Input is never mutated."""
    cls = classify_short_stitches(plan.stitches, short_mm)
    rep = ShortStitchReport()
    for _, c in cls:
        rep.counts[c] = rep.counts.get(c, 0) + 1
    drop = set()
    for i, c in cls:
        if c in ("duplicate", "noise"):
            drop.add(i); rep.removed += 1
        elif c == "accidental_cluster":
            drop.add(i); rep.merged += 1      # point folded into its neighbour (resample)
        else:
            rep.preserved += 1
    # never drop the last stitch of an object part boundary (keeps entry/exit intact)
    safe = set()
    for i in drop:
        s, nx = plan.stitches[i], plan.stitches[i + 1] if i + 1 < len(plan.stitches) else None
        if nx is None or nx.command != "STITCH" or nx.object_id != s.object_id:
            continue
        safe.add(i)
    new = copy.copy(plan)
    new.stitches = [s for i, s in enumerate(plan.stitches) if i not in safe]
    rep.removed = sum(1 for i, c in cls if i in safe and c in ("duplicate", "noise"))
    rep.merged = sum(1 for i, c in cls if i in safe and c == "accidental_cluster")
    return new, rep


def split_long_stitches(plan, max_mm: float):
    """Split STITCH->STITCH segments longer than ``max_mm`` into equal parts (same object, direction preserved).  Returns (plan, n_split)."""
    from .stitch import Stitch
    new = copy.copy(plan)
    out, n = [], 0
    st = plan.stitches
    for i, s in enumerate(st):
        if i > 0 and s.command == "STITCH" and st[i - 1].command == "STITCH" and st[i - 1].object_id == s.object_id:
            a = st[i - 1]
            L = math.dist((a.x, a.y), (s.x, s.y))
            if L > max_mm + 1e-9:
                k = int(math.ceil(L / max_mm))
                for j in range(1, k):
                    out.append(Stitch(a.x + (s.x - a.x) * j / k, a.y + (s.y - a.y) * j / k, "STITCH", s.color, s.object_id, s.layer, s.part))
                n += 1
        out.append(s)
    new.stitches = out
    return new, n


def count_over(plan, max_mm: float) -> int:
    st = plan.stitches
    return sum(1 for i in range(1, len(st)) if st[i].command == st[i - 1].command == "STITCH" and st[i].object_id == st[i - 1].object_id
               and math.dist((st[i].x, st[i].y), (st[i - 1].x, st[i - 1].y)) > max_mm + 1e-9)
