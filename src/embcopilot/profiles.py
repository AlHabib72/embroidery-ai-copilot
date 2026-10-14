"""
Production profiles.

Before this module existed, `production_profile` was accepted by
`TechnicalBlueprintEngine.generate()` and stored in the output, but it never
influenced a single decision (found in the audit -- see AUDIT_REPORT.md).
Every field below is consumed by knowledge.py / technical.py / connector.py.

HONESTY NOTE: the numbers are conservative engineering STARTING POINTS, not
values validated against real sewing trials on any machine, fabric or thread.
They exist so behaviour differs in a documented, testable, configurable way.
Override any profile with `dataclasses.replace(profile, ...)`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ProductionProfile:
    name: str
    description: str
    # Multiplies row spacing: >1 = sparser (faster, lighter), <1 = denser.
    spacing_scale: float = 1.0
    # Multiplies satin pull compensation (stretchy / heavy goods need more).
    pull_scale: float = 1.0
    # -1 lighter underlay tier, 0 default, +1 heavier tier.
    underlay_bias: int = 0
    # Smallest feature (mm) worth digitizing under this profile.
    min_feature_mm: float = 0.5
    # Run-stitch length (mm) for RUN / TRIPLE_RUN objects.
    run_stitch_length_mm: float = 2.2
    # Longest gap (mm) a connector may try to hide under later stitching.
    hidden_travel_max_mm: float = 4.0
    # Connector cost multipliers (see connector.ConnectorCostWeights).
    trim_weight_scale: float = 1.0
    visible_travel_scale: float = 1.0
    jump_distance_scale: float = 1.0


_P = ProductionProfile
PROFILES: dict[str, ProductionProfile] = {
    p.name: p
    for p in [
        _P("balanced", "General-purpose defaults."),
        _P("quality_first", "Denser, heavier underlay, visible travel and trims penalised less than poor coverage.",
           spacing_scale=0.95, pull_scale=1.1, underlay_bias=1, min_feature_mm=0.6,
           run_stitch_length_mm=2.0, hidden_travel_max_mm=3.0,
           trim_weight_scale=0.8, visible_travel_scale=3.0, jump_distance_scale=0.8),
        _P("speed_first", "Sparser fills, lighter underlay, fewer trims.",
           spacing_scale=1.10, pull_scale=0.9, underlay_bias=-1, min_feature_mm=0.4,
           run_stitch_length_mm=2.8, hidden_travel_max_mm=6.0,
           trim_weight_scale=1.6, visible_travel_scale=0.6, jump_distance_scale=1.2),
        _P("cap", "Structured cap front: firm, dense, more pull compensation.",
           spacing_scale=0.95, pull_scale=1.2, underlay_bias=1, min_feature_mm=0.7,
           run_stitch_length_mm=2.0, hidden_travel_max_mm=3.0, trim_weight_scale=0.9),
        _P("jacket", "Heavy outerwear: sturdier underlay, slightly sparser fill.",
           spacing_scale=1.03, pull_scale=1.0, underlay_bias=1, min_feature_mm=0.6,
           run_stitch_length_mm=2.5, hidden_travel_max_mm=5.0),
        _P("left_chest", "Small logo on a shirt: fine detail, quality bias.",
           spacing_scale=0.97, pull_scale=1.05, underlay_bias=0, min_feature_mm=0.7,
           run_stitch_length_mm=2.0, hidden_travel_max_mm=3.0, visible_travel_scale=1.5),
        _P("flat_fabric", "Stable flat goods.", spacing_scale=1.0, pull_scale=0.95),
        _P("knit", "Stretch goods: more pull comp, heavier underlay, avoid tiny detail.",
           spacing_scale=1.05, pull_scale=1.35, underlay_bias=1, min_feature_mm=0.8,
           run_stitch_length_mm=2.4),
        _P("denim", "Dense heavy woven.", spacing_scale=1.02, pull_scale=0.9, underlay_bias=1,
           min_feature_mm=0.6, run_stitch_length_mm=2.6, hidden_travel_max_mm=5.0),
        _P("light_fabric", "Delicate goods: sparser, lighter underlay to avoid puckering.",
           spacing_scale=1.10, pull_scale=1.0, underlay_bias=-1, min_feature_mm=0.6,
           run_stitch_length_mm=2.4),
        _P("heavy_fabric", "Thick goods: denser, heavier underlay.",
           spacing_scale=0.96, pull_scale=1.1, underlay_bias=1, min_feature_mm=0.5,
           run_stitch_length_mm=2.6, hidden_travel_max_mm=5.0),
    ]
}


def get_profile(name: str | ProductionProfile | None) -> ProductionProfile:
    """Resolve a profile by name. Unknown names raise (never silently ignored)."""
    if isinstance(name, ProductionProfile):
        return name
    key = (name or "balanced").strip().lower()
    if key not in PROFILES:
        raise ValueError(f"Unknown production profile {name!r}. Known: {', '.join(sorted(PROFILES))}")
    return PROFILES[key]


def override(profile: ProductionProfile, **changes) -> ProductionProfile:
    return replace(profile, **changes)
