"""OLD vs NEW design diff (v0.6): metrics + per-metric delta, used after regeneration, repair or optimisation."""
from __future__ import annotations

from .qc_v2 import plan_metrics

KEYS = ("object_count", "stitch_count", "jump_count", "jump_moves", "trim_count", "color_count",
        "center_crossing_travel", "short_stitch_count", "estimated_minutes")


def _travel(plan) -> float:
    import math
    st = plan.stitches
    return round(sum(math.dist((st[i - 1].x, st[i - 1].y), (st[i].x, st[i].y)) for i in range(1, len(st))
                     if st[i].command == "JUMP" or st[i].part.startswith("connector")), 1)


def diff_plans(old, new, zones=None) -> dict:
    a, b = plan_metrics(old, zones), plan_metrics(new, zones)
    rows = {}
    for k in KEYS:
        rows[k] = {"old": a[k], "new": b[k], "delta": round(b[k] - a[k], 2)}
    ta, tb = _travel(old), _travel(new)
    rows["travel_mm"] = {"old": ta, "new": tb, "delta": round(tb - ta, 1)}
    rows["bbox_mm"] = {"old": a["bbox_mm"], "new": b["bbox_mm"], "delta": None}
    improved = [k for k in ("jump_count", "trim_count", "center_crossing_travel", "short_stitch_count", "travel_mm") if rows[k]["delta"] < 0]
    worse = [k for k in ("jump_count", "trim_count", "center_crossing_travel", "short_stitch_count", "travel_mm") if rows[k]["delta"] > 0]
    return {"metrics": rows, "improved": improved, "worse": worse,
            "note": "metrics are measured from the two StitchPlans; fewer is not automatically better (see trims vs thread dragged)"}
