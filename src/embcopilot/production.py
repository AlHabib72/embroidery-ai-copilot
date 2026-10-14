"""v0.6 production pass: takes a stitch plan from the existing pipeline and applies, in order,
protected zones -> travel policy/rerouting -> context-aware short-stitch cleanup -> QC v2 -> diff -> export package.

Every step reports original strategy / repair / reason / impact; nothing changes intent silently.
Cleanup runs on a copy; the pre-optimisation plan is kept for the OLD/NEW diff.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .designdiff import diff_plans
from .exportpkg import export_package
from .machine import get_machine
from .negative_space import build_protected_zones
from .qc_v2 import QCReport, plan_metrics, release_gates, run_qc
from .stitchclean import clean_short_stitches
from .travel import optimize_travel


@dataclass
class ProductionResult:
    plan_before: object
    plan_after: object
    zones: list
    qc_before: QCReport
    qc_after: QCReport
    diff: dict
    travel: dict
    cleanup: dict
    gates: list
    timings: dict
    package: dict | None = None

    def to_dict(self):
        return {"qc_before": {"errors": len(self.qc_before.errors), "codes": sorted({i.code for i in self.qc_before.issues})},
                "qc_after": self.qc_after.to_dict(), "diff": self.diff, "travel": self.travel, "cleanup": self.cleanup,
                "gates": self.gates, "zones": [z.to_dict() for z in self.zones], "timings_s": self.timings,
                "package": self.package}


def production_pass(R, machine="generic_flat", out_dir: str | None = None, formats=("dst", "exp"),
                    expert_override: dict | None = None, fabric_stretch: float = 0.0) -> ProductionResult:
    """``R`` is a PipelineResult (pipeline.run_pipeline)."""
    T, t0 = {}, time.time()
    m = get_machine(machine)
    zones = build_protected_zones(R.objects, R.plan.canvas_mm); T["zones"] = round(time.time() - t0, 3); t1 = time.time()
    qc0 = run_qc(R.plan, R.objects, zones, m)
    p1, trep = optimize_travel(R.plan, zones, m, fabric_stretch=fabric_stretch); T["travel"] = round(time.time() - t1, 3); t1 = time.time()
    p2, crep = clean_short_stitches(p1, m.min_safe_stitch_mm); T["cleanup"] = round(time.time() - t1, 3); t1 = time.time()
    qc1 = run_qc(p2, R.objects, zones, m); T["qc"] = round(time.time() - t1, 3)
    diff = diff_plans(R.plan, p2, zones)
    res = ProductionResult(R.plan, p2, zones, qc0, qc1, diff, trep.to_dict(), crep.to_dict(), release_gates(qc1), T)
    if out_dir:
        t1 = time.time()
        res.package = export_package(p2, R.objects, out_dir, qc1, formats, expert_override,
                                     extra_meta={"machine": m.machine_name, "diff": diff})
        T["export"] = round(time.time() - t1, 3)
        with open(os.path.join(out_dir, "production_report.json"), "w", encoding="utf-8") as f:
            json.dump(res.to_dict(), f, indent=1, default=str)
    T["total"] = round(time.time() - t0, 3)
    return res
