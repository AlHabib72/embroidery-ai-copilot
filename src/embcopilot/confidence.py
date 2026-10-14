"""Multi-dimensional confidence + explicit risk (Phase 13). Nothing here is a
trained probability; each dimension is computed from a named piece of
evidence and the reasons are kept so a human can see WHY approval is needed."""

from __future__ import annotations

from dataclasses import dataclass, field

from .domain import ConfidenceThresholds, EmbroideryObject


@dataclass
class ObjectAssessment:
    object_id: str
    dimensions: dict[str, float]
    risk: str  # SAFE LOW MEDIUM HIGH CRITICAL
    reasons: list[str] = field(default_factory=list)
    approval: str = "ask_approval"  # band from ConfidenceThresholds
    requires_human: bool = True

    def to_dict(self):
        return self.__dict__.copy()


_RISK = ["SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


def assess_object(o: EmbroideryObject, plan_status: dict, findings: list, merged: bool,
                  thresholds: ConfidenceThresholds | None = None, min_feature_mm: float = 0.5) -> ObjectAssessment:
    th = thresholds or ConfidenceThresholds()
    c = lambda k, d=0.5: o.confidence[k].value if k in o.confidence else d  # noqa: E731
    dims = {"vision": c("detection"), "semantic": c("semantic", 0.3), "stitch_type": c("stitch_type"),
            "direction": c("direction")}
    reasons: list[str] = []
    risk = 0

    geom = 1.0
    if not plan_status.get("ok"):
        geom = 0.0; risk = max(risk, 4); reasons.append("stitch geometry could not be generated: " + "; ".join(plan_status.get("errors", [])))
    if any("FALLBACK" in w for w in plan_status.get("warnings", [])):
        geom = min(geom, 0.7); risk = max(risk, 2); reasons.append("requested satin was invalid; fell back to fill")
    if merged:
        geom = min(geom, 0.75); risk = max(risk, 2); reasons.append("object was reconstructed across an occluder (heuristic) — verify continuation")
    dims["geometry"] = geom

    sev = [f for f in findings if f.object_id == o.object_id]
    if any(f.severity == "ERROR" for f in sev):
        risk = max(risk, 4); reasons += [f"{f.code}: {f.message}" for f in sev if f.severity == "ERROR"]
    elif any(f.severity == "WARNING" for f in sev):
        risk = max(risk, 1)
    dims["stitch_geometry_validation"] = 0.0 if any(f.severity == "ERROR" for f in sev) else (0.85 if sev else 1.0)

    if dims["semantic"] < 0.6:
        risk = max(risk, 2); reasons.append(f"semantic classification uncertain ({dims['semantic']:.2f}) — heuristic only")
    if o.semantic_reason and "AMBIGUOUS" in o.semantic_reason:
        risk = max(risk, 2); reasons.append("semantic ambiguity: " + o.semantic_reason.split("AMBIGUOUS:")[1].strip())
    w = (o.width_stats_mm or {}).get("median", 99)
    if w < min_feature_mm * 1.5:
        risk = max(risk, 2); reasons.append(f"very small feature (~{w:.2f}mm)")
    if dims["vision"] < 0.6:
        risk = max(risk, 2); reasons.append(f"low detection confidence ({dims['vision']:.2f})")

    # Overall decision confidence: the WEAKEST dimension bounds it (no averaging away a bad one).
    overall = min(dims.values())
    band = th.band(overall)
    if not reasons and band != "automatic_safe":
        reasons.append(f"confidence {overall:.2f} is below the automatic threshold {th.auto_safe:.2f}")
    return ObjectAssessment(o.object_id, {k: round(v, 3) for k, v in dims.items()}, _RISK[risk], reasons, band,
                            requires_human=(band != "automatic_safe" or risk >= 2))
