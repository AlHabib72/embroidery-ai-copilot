"""
Fabric profiles and the push-pull (pull compensation) model.

HONESTY: every number here is an engineering starting point chosen for
reasonable relative behaviour (stretchier -> more compensation, etc.). None
of it is fitted to sew-out measurements. Outputs carry a confidence that is
deliberately capped below 0.9 and say so. Feed real sew-out data
(`SewOutRecord`) before trusting absolute millimetres.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .domain import StitchType


@dataclass(frozen=True)
class FabricProfile:
    name: str
    stretch: float  # 0 stable .. 1 very stretchy
    pile: float  # 0 flat .. 1 deep pile (towel/fleece)
    density_scale: float  # multiplies row spacing (>1 sparser)
    underlay_bias: int  # -1 lighter, 0, +1 heavier
    min_feature_mm: float
    max_satin_mm: float
    stitch_len_scale: float
    stabilization: str
    risk_tolerance: str  # low | medium | high
    warnings: tuple = ()


def _f(name, stretch, pile, dens, ub, minf, maxsat, sl, stab, risk, *warn):
    return FabricProfile(name, stretch, pile, dens, ub, minf, maxsat, sl, stab, risk, tuple(warn))


FABRICS: dict[str, FabricProfile] = {f.name: f for f in [
    _f("T_SHIRT", 0.45, 0.0, 1.00, 0, 0.6, 9.0, 1.0, "cut-away or no-show mesh", "medium", "Knit stretch can distort outlines: consider stabiliser."),
    _f("JERSEY", 0.75, 0.0, 1.05, 1, 0.8, 8.0, 1.0, "cut-away", "low", "High stretch: increase underlay and compensation; avoid tiny detail."),
    _f("DENIM", 0.05, 0.0, 1.00, 1, 0.5, 10.0, 1.15, "tear-away", "high", ""),
    _f("TOWEL", 0.20, 1.0, 0.95, 1, 1.2, 8.0, 1.0, "water-soluble topping + cut-away", "low",
       "Deep pile: high risk of detail loss.", "Increase underlay, minimum feature size and edge definition."),
    _f("PIQUE", 0.30, 0.15, 1.00, 0, 0.7, 9.0, 1.0, "cut-away", "medium", ""),
    _f("TWILL", 0.05, 0.0, 1.00, 0, 0.5, 10.0, 1.1, "tear-away", "high", ""),
    _f("CANVAS", 0.02, 0.0, 1.02, 1, 0.5, 10.0, 1.15, "tear-away", "high", ""),
    _f("FLEECE", 0.35, 0.7, 0.96, 1, 1.0, 8.0, 1.0, "topping + cut-away", "low", "Pile can swallow fine stitches: use topping, increase feature size."),
    _f("CAP", 0.05, 0.0, 0.97, 1, 0.7, 7.0, 0.95, "structured cap backing", "medium", "Curved surface: keep satin narrow, avoid dense large fills."),
    _f("PATCH", 0.0, 0.0, 1.00, 0, 0.5, 10.0, 1.1, "heavy backing", "high", ""),
    _f("STRETCH_FABRIC", 0.90, 0.0, 1.05, 1, 0.9, 7.0, 0.95, "cut-away, fusible mesh", "low", "Potential distortion: increase compensation, add underlay."),
]}


def get_fabric(name: str | FabricProfile | None) -> FabricProfile | None:
    """Returns a FabricProfile, or None for names of the legacy (v0.2) fabric set. Unknown names raise."""
    if isinstance(name, FabricProfile):
        return name
    if not name:
        return None
    key = name.strip().upper()
    if key in FABRICS:
        return FABRICS[key]
    return None


@dataclass
class PullCompensation:
    x_mm: float
    y_mm: float
    direction_deg: float
    amount_mm: float
    reason: str
    confidence: float

    def to_dict(self):
        return self.__dict__.copy()


def pull_compensation(fabric: FabricProfile, stitch_type: str, width_mm: float, length_mm: float, density_mm: float,
                      direction_deg: float | None, underlay: str, stitch_count: int = 0, pull_scale: float = 1.0) -> PullCompensation:
    """Pull compensation = f(fabric, stitch type, density, direction, width, length, underlay, stitch count).

    Satin/column:  base 0.2mm (+0.08 for widths > 6mm)
                   x (1 + 1.2*stretch + 0.4*pile)          stretchy/piled goods pull more
                   x (0.4/density)^0.5 clipped .8..1.3      denser rows pull more
                   x 0.85 if a zigzag/double-zigzag underlay stabilises the column
                   x (1 + min(0.3, stitch_count/20000))     large designs accumulate distortion
    Fills:         0.5 x the satin figure, applied across the fill direction.
    Run:           no compensation.
    Compensation acts perpendicular to the stitch direction (columns widen sideways; fills extend across rows).
    """
    if stitch_type not in (StitchType.SATIN.value, StitchType.TATAMI.value, StitchType.COMPLEX_FILL.value,
                           StitchType.PUFF.value, StitchType.APPLIQUE.value):
        return PullCompensation(0, 0, 0, 0.0, f"{stitch_type}: pull compensation not applicable", 0.9)
    base = 0.20 + (0.08 if width_mm > 6.0 else 0.0)
    f_fabric = 1 + 1.2 * fabric.stretch + 0.4 * fabric.pile
    f_density = min(1.3, max(0.8, (0.40 / max(density_mm, 0.15)) ** 0.5))
    f_under = 0.85 if underlay in ("zigzag", "double_zigzag") else 1.0
    f_count = 1 + min(0.3, stitch_count / 20000.0)
    amt = base * f_fabric * f_density * f_under * f_count * pull_scale
    if stitch_type != StitchType.SATIN.value:
        amt *= 0.5
    amt = round(amt, 3)
    d = direction_deg if direction_deg is not None else 0.0
    perp = d + 90.0
    import math
    conf = round(min(0.85, 0.75 - 0.05 * fabric.stretch - 0.03 * fabric.pile), 2)
    reason = (f"{fabric.name}: stretch {fabric.stretch:.2f}, pile {fabric.pile:.2f}; density {density_mm}mm; underlay {underlay}; "
              f"width {width_mm:.1f}mm -> factors fabric x{f_fabric:.2f}, density x{f_density:.2f}, underlay x{f_under:.2f}, "
              f"size x{f_count:.2f}. Heuristic model, not fitted to sew-out data.")
    return PullCompensation(round(amt * math.cos(math.radians(perp)), 3), round(amt * math.sin(math.radians(perp)), 3),
                            perp % 360, amt, reason, conf)


def fabric_warnings(fabric: FabricProfile, width_mm: float, stitch_type: str) -> list[str]:
    out = list(w for w in fabric.warnings if w)
    if width_mm and width_mm < fabric.min_feature_mm:
        out.append(f"feature ~{width_mm:.2f}mm is below this fabric's minimum {fabric.min_feature_mm}mm")
    if stitch_type == StitchType.SATIN.value and width_mm > fabric.max_satin_mm:
        out.append(f"satin width {width_mm:.1f}mm exceeds this fabric's maximum {fabric.max_satin_mm}mm")
    return out


@dataclass
class SewOutRecord:
    """Schema for future learning from real physical results. Nothing consumes it yet."""
    design_id: str
    fabric: str
    thread_brand: str
    needle: str = ""
    stabilizer: str = ""
    machine: str = ""
    stitch_count: int = 0
    sewout_photo: str = ""
    defects: tuple = ()
    expert_notes: str = ""


@dataclass(frozen=True)
class Hoop:
    name: str
    width_mm: float
    height_mm: float
    safe_margin_mm: float = 3.0


HOOPS: dict[str, Hoop] = {h.name: h for h in [
    Hoop("generic-100x100", 100, 100), Hoop("generic-130x180", 130, 180), Hoop("generic-200x300", 200, 300),
    Hoop("cap-60x130", 60, 130, 2.0),
]}


def check_hoop(design_w: float, design_h: float, hoop: Hoop) -> dict:
    aw, ah = hoop.width_mm - 2 * hoop.safe_margin_mm, hoop.height_mm - 2 * hoop.safe_margin_mm
    ok = design_w <= aw and design_h <= ah
    return {"status": "VALID" if ok else "DESIGN_EXCEEDS_HOOP", "design_mm": [round(design_w, 1), round(design_h, 1)],
            "hoop": hoop.name, "usable_mm": [aw, ah]}
