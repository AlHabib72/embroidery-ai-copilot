"""
Technical Blueprint engine.

Converts an Object Blueprint (geometry only, from vision.py) into a
Technical Blueprint (a digitizing plan per object: stitch type, density,
underlay, pull compensation, direction) by calling the rule-based
knowledge engine for every object, and folds in the small-detail check
(section 63 of the project brief): flag features that are too small to
sew reliably at the target physical size, but never silently delete them.

The output is machine-independent (section 67): it describes embroidery
INTENT, not a Wilcom-specific action. automation/ is what would translate
this into Wilcom operations, and does not exist as a working bridge yet —
see automation/wilcom_adapter.py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import knowledge
from .domain import Confidence, Decision, RiskLevel, StitchType, new_id
from .fabrics import fabric_warnings, get_fabric, pull_compensation as _fab_pull
from .profiles import get_profile
from .vision import ObjectBlueprint


@dataclass
class SmallDetailFlag:
    object_id: str
    object_name: str
    measured_mm: float
    threshold_mm: float
    recommendation: str


@dataclass
class TechnicalObjectPlan:
    object_id: str
    object_name: str
    stitch_type: str
    stitch_reason: str
    stitch_confidence: Confidence
    density_mm: float
    underlay: str
    underlay_reason: str
    pull_compensation_mm: float
    pull_compensation_reason: str
    direction_deg: float | None
    direction_reason: str
    direction_confidence: Confidence
    width_mm: float
    overall_confidence: Confidence  # combined, for the approval UI
    warnings: list = field(default_factory=list)


@dataclass
class TechnicalBlueprint:
    fabric: str
    production_profile: str
    target_size_mm: tuple[float, float]
    plans: list[TechnicalObjectPlan] = field(default_factory=list)
    small_detail_flags: list[SmallDetailFlag] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "fabric": self.fabric,
            "production_profile": self.production_profile,
            "target_size_mm": self.target_size_mm,
            "plans": [
                {**asdict(p), "stitch_confidence": asdict(p.stitch_confidence),
                 "direction_confidence": asdict(p.direction_confidence),
                 "overall_confidence": asdict(p.overall_confidence)}
                for p in self.plans
            ],
            "small_detail_flags": [asdict(f) for f in self.small_detail_flags],
        }


# Minimum practical embroidery feature size. Below this, stitches start
# competing for the same needle penetrations regardless of digitizing
# skill. This is a conservative working default, not a law of physics —
# thread weight, fabric and stabiliser all shift the real floor.
MIN_PRACTICAL_FEATURE_MM = 0.5


class TechnicalBlueprintEngine:
    def __init__(self, min_practical_feature_mm: float | None = None):
        """`min_practical_feature_mm=None` (default) uses the production
        profile's own minimum; pass a number to force one for every profile."""
        self.min_practical_feature_mm = min_practical_feature_mm

    def generate(
        self,
        blueprint: ObjectBlueprint,
        fabric: str = "woven",
        production_profile: str = "balanced",
        overrides: dict | None = None,
    ) -> tuple[TechnicalBlueprint, list[Decision]]:
        prof = get_profile(production_profile)  # unknown name -> ValueError, never ignored
        min_feature = (
            self.min_practical_feature_mm if self.min_practical_feature_mm is not None else prof.min_feature_mm
        )
        _fb = get_fabric(fabric)
        if _fb is not None:
            min_feature = max(min_feature, _fb.min_feature_mm)
        tb = TechnicalBlueprint(
            fabric=fabric,
            production_profile=prof.name,
            target_size_mm=blueprint.target_size_mm,
        )
        decisions: list[Decision] = []

        for obj in blueprint.objects:
            measured = obj.width_stats_mm is not None
            # Measured (distance-transform) width when Vision V2 supplied it;
            # otherwise the bbox proxy, which overstates diagonal shapes.
            width_mm = obj.width_stats_mm["median"] if measured else knowledge.estimate_width_mm(obj.bbox_mm)

            if 0 < width_mm < min_feature:
                tb.small_detail_flags.append(
                    SmallDetailFlag(
                        object_id=obj.object_id,
                        object_name=obj.name,
                        measured_mm=round(width_mm, 2),
                        threshold_mm=min_feature,
                        recommendation=(
                            f"This detail is approximately {width_mm:.2f}mm at "
                            f"target size — below the {min_feature}mm "
                            "practical floor. Consider simplifying or enlarging "
                            "rather than digitizing it at this size."
                        ),
                    )
                )

            area_mm2 = obj.area_mm2 if obj.area_mm2 is not None else self._approx_area_mm2(obj.bbox_mm)
            _axis_deg, elongation = knowledge.principal_axis_deg(obj.geometry)

            stitch_type, stitch_conf, stitch_reason = knowledge.recommend_stitch_type(
                width_mm, area_mm2, elongation
            )
            topo = obj.skeleton_topology or {}
            ring_ok = self._is_concentric_ring(obj, width_mm)
            if ring_ok and stitch_type in (StitchType.TATAMI.value, StitchType.COMPLEX_FILL.value):
                stitch_type = StitchType.SATIN.value
                stitch_conf = Confidence(0.8, "closed concentric ring band with one hole")
                stitch_reason = (f"Closed ring/band ({width_mm:.1f}mm wide, one concentric hole): outer and hole "
                                 "contours are natural satin rails -> closed satin ring.")
            elif stitch_type == StitchType.SATIN.value and not ring_ok:
                # Satin needs real rails. Without a single clean centerline the engine cannot build them,
                # so decide FILL up front rather than promise a satin it cannot generate.
                complex_topo = topo.get("branch_points", 0) >= 1 or bool(obj.holes_mm)
                if not obj.centerline_mm or complex_topo:
                    stitch_type = StitchType.TATAMI.value
                    why = "branched/holed shape" if complex_topo else "no single centerline available for rails"
                    stitch_conf = Confidence(min(stitch_conf.value, 0.7), f"satin -> fill: {why}")
                    stitch_reason = f"Width suggests satin, but {why}; a fill is the geometry the engine can generate correctly here."
            # User / command overrides always win and are recorded as such (key = object_id or lower-case category)
            ov = (overrides or {}).get(obj.object_id) or (overrides or {}).get(obj.category)
            if ov:
                obj.params["_recommended_stitch"] = stitch_type  # kept so a failing override can fall back, visibly
                stitch_type = ov["stitch_type"] if isinstance(ov, dict) else ov
                if isinstance(ov, dict) and ov.get("params"):
                    obj.params.update(ov["params"])
                stitch_conf = Confidence(1.0, "explicit user override")
                stitch_reason = f"User override -> {stitch_type}"
            density_mm, density_reason = knowledge.recommend_density_mm(stitch_type, fabric, prof)
            underlay, underlay_reason = knowledge.recommend_underlay(stitch_type, width_mm, prof)
            pull_mm, pull_reason = knowledge.recommend_pull_compensation_mm(stitch_type, width_mm, fabric, prof)
            direction_deg, direction_reason, direction_conf = knowledge.recommend_direction(
                obj.geometry, stitch_type
            )
            plan_warnings: list = []
            fab = get_fabric(fabric)
            if fab is not None:  # new FabricProfile set: density/underlay/pull come from the fabric model
                is_run = stitch_type in (StitchType.RUN.value, StitchType.TRIPLE_RUN.value)
                if not is_run:
                    density_mm = round(density_mm * fab.density_scale, 3)
                    density_reason += f"; x{fab.density_scale} for fabric {fab.name}"
                if stitch_type == StitchType.SATIN.value and fab.underlay_bias:
                    tiers = knowledge._SATIN_TIERS
                    k = tiers.index(underlay) if underlay in tiers else 0
                    underlay = tiers[max(0, min(len(tiers) - 1, k + fab.underlay_bias))]
                    underlay_reason += f"; tier shifted {fab.underlay_bias:+d} for fabric {fab.name}"
                length_mm = (obj.width_stats_mm or {}).get("centerline_length_mm", max(obj.width_mm, obj.height_mm))
                pc = _fab_pull(fab, stitch_type, width_mm, length_mm, density_mm, direction_deg, underlay, 0, prof.pull_scale)
                pull_mm, pull_reason = pc.amount_mm, f"{pc.reason} (confidence {pc.confidence})"
                plan_warnings = fabric_warnings(fab, width_mm, stitch_type)

            overall_value = (stitch_conf.value + direction_conf.value) / 2.0
            overall_conf = Confidence(
                overall_value,
                "average of stitch-type and direction confidence — the two "
                "judgement calls most likely to need a human's eye",
            )

            obj.stitch_type = stitch_type
            obj.density_mm = density_mm
            obj.underlay = underlay
            obj.pull_compensation_mm = pull_mm
            obj.stitch_direction_deg = direction_deg
            obj.confidence["stitch_type"] = stitch_conf
            obj.confidence["direction"] = direction_conf
            obj.confidence["overall"] = overall_conf

            plan = TechnicalObjectPlan(
                object_id=obj.object_id,
                object_name=obj.name,
                stitch_type=stitch_type,
                stitch_reason=stitch_reason,
                stitch_confidence=stitch_conf,
                density_mm=density_mm,
                underlay=underlay,
                underlay_reason=underlay_reason,
                pull_compensation_mm=pull_mm,
                pull_compensation_reason=pull_reason,
                direction_deg=direction_deg,
                direction_reason=direction_reason,
                direction_confidence=direction_conf,
                width_mm=round(width_mm, 2),
                overall_confidence=overall_conf,
                warnings=plan_warnings,
            )
            tb.plans.append(plan)

            decisions.append(
                Decision(
                    decision_id=new_id("dec"),
                    subject_object_id=obj.object_id,
                    action=f"set_stitch_type:{stitch_type}",
                    reason=stitch_reason,
                    confidence=stitch_conf,
                    risk=RiskLevel.LOW,
                    requires_approval=stitch_conf.value < 0.85,
                )
            )

        return tb, decisions

    @staticmethod
    def _is_concentric_ring(obj, width_mm: float) -> bool:
        if not (len(obj.holes_mm) == 1 and (obj.skeleton_topology or {}).get("closed_loop") and 1.0 <= width_mm <= 10.0):
            return False
        import numpy as np
        o = np.asarray(obj.geometry); h = np.asarray(obj.holes_mm[0])
        co, ch = o.mean(0), h.mean(0)
        r_out = float(np.hypot(*(o - co).T).mean())
        return float(np.hypot(*(co - ch))) < 0.25 * r_out and float(np.hypot(*(h - ch).T).std() / max(np.hypot(*(h - ch).T).mean(), 1e-9)) < 0.35

    @staticmethod
    def _approx_area_mm2(bbox_mm: tuple[float, float, float, float]) -> float:
        x0, y0, x1, y1 = bbox_mm
        return abs(x1 - x0) * abs(y1 - y0)
