"""
Rule-based embroidery knowledge engine.

This is deliberately NOT an LLM. Every recommendation here comes from
explicit geometry thresholds (width, aspect ratio, area) that a professional
digitizer would recognise — the same rules established for this project's
Phase 1 planning conversation (satin/tatami/run width bands, density,
underlay, pull compensation). An LLM can sit in front of this engine later
(e.g. to turn its structured output into prose, or to handle genuinely
ambiguous cases) but the embroidery domain logic itself lives here, in
code that can be read, tested, and tuned without touching a model.

Every function returns (value, Confidence) or (value, reason) — never a bare
recommendation. See PROJECT_CONTEXT section 13: "Never return only: Satin —
without reasoning."
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .domain import Confidence, StitchType, UnderlayType
from .profiles import ProductionProfile, get_profile

# ----------------------------------------------------------------------
# Geometry helpers
# ----------------------------------------------------------------------


def estimate_width_mm(bbox_mm: tuple[float, float, float, float]) -> float:
    """Bounding-box-based width proxy.

    APPROXIMATION: this is min(bbox_width, bbox_height), which is a
    reasonable proxy for column width on a roughly axis-aligned elongated
    shape (a straight stem, a horizontal wing) but overstates the true
    column width on a diagonal or curved shape of the same bounding box.
    A medial-axis measurement (distance-transform based, as planned for the
    Phase 1B image analyzer) would be exact; this bbox proxy is what is
    available without that pipeline built yet.
    """
    x0, y0, x1, y1 = bbox_mm
    return min(abs(x1 - x0), abs(y1 - y0))


def principal_axis_deg(geometry_mm: list[tuple[float, float]]) -> tuple[float, float]:
    """Principal axis angle (degrees, 0-180) of a set of outline points, via PCA.

    This is a real geometric computation (eigenvector of the point
    covariance matrix), not a semantic "this leaf's vein points this way"
    understanding — it just finds the long axis of the point cloud. Returns
    (angle_degrees, elongation_ratio) where elongation_ratio is
    major_eigenvalue / minor_eigenvalue (1.0 = round/symmetric, large =
    strongly elongated). Falls back to (0.0, 1.0) for degenerate input.
    """
    if len(geometry_mm) < 3:
        return 0.0, 1.0

    n = len(geometry_mm)
    mean_x = sum(p[0] for p in geometry_mm) / n
    mean_y = sum(p[1] for p in geometry_mm) / n
    sxx = sum((p[0] - mean_x) ** 2 for p in geometry_mm) / n
    syy = sum((p[1] - mean_y) ** 2 for p in geometry_mm) / n
    sxy = sum((p[0] - mean_x) * (p[1] - mean_y) for p in geometry_mm) / n

    # Eigenvalues/vectors of the 2x2 covariance matrix [[sxx, sxy],[sxy, syy]]
    trace = sxx + syy
    det = sxx * syy - sxy * sxy
    disc = max(0.0, trace**2 / 4 - det)
    sqrt_disc = math.sqrt(disc)
    lam1 = trace / 2 + sqrt_disc  # major
    lam2 = trace / 2 - sqrt_disc  # minor

    if abs(sxy) < 1e-9 and abs(sxx - syy) < 1e-9:
        return 0.0, 1.0  # circular / degenerate: no defined axis

    angle_rad = 0.5 * math.atan2(2 * sxy, sxx - syy)
    angle_deg = math.degrees(angle_rad) % 180.0
    elongation = (lam1 / lam2) if lam2 > 1e-9 else 99.0
    return angle_deg, elongation


# ----------------------------------------------------------------------
# Stitch type decision
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class StitchThresholds:
    """Width bands, in mm. Configurable — see AnalyzerConfig for the parallel
    QC-side thresholds (max_satin_mm etc); these are the digitizing-side
    equivalents and are deliberately kept separate since they answer a
    different question (what to create vs what to flag)."""

    run_max_mm: float = 1.0
    satin_max_mm: float = 10.0
    split_satin_max_mm: float = 12.0
    # above split_satin_max_mm: tatami / complex fill


def recommend_stitch_type(
    width_mm: float, area_mm2: float, elongation: float, th: StitchThresholds | None = None
) -> tuple[str, Confidence, str]:
    """Recommend a stitch type from geometry alone.

    Returns (StitchType value, Confidence, human-readable reason). This
    mirrors the width-band rule established for this project:
        < 1mm            -> Run
        1-10mm           -> Satin
        10-12mm          -> Split satin (reported as Satin; caller should
                             note the split requirement separately)
        > 12mm           -> Tatami / Complex fill
    A very low elongation (round/blobby shape) with non-trivial area pulls
    the recommendation toward a fill even inside the satin width band,
    since a true satin column implies a clearly-defined long axis.
    """
    th = th or StitchThresholds()

    if width_mm <= 0:
        return (
            StitchType.UNDECIDED.value,
            Confidence(0.2, "degenerate geometry (zero width)"),
            "Degenerate geometry (zero width) — cannot recommend a stitch type.",
        )

    if width_mm < th.run_max_mm:
        conf = Confidence(
            0.85 if elongation > 3 else 0.65,
            f"width {width_mm:.2f}mm is under the {th.run_max_mm}mm run threshold",
        )
        return StitchType.RUN.value, conf, (
            f"Width is {width_mm:.2f}mm, under the {th.run_max_mm}mm run threshold — "
            "too narrow for a satin column to look controlled."
        )

    if width_mm <= th.satin_max_mm:
        if elongation < 1.8 and area_mm2 > 30:
            conf = Confidence(
                0.55,
                f"width {width_mm:.2f}mm is in the satin band but elongation "
                f"({elongation:.1f}) is low — shape looks more like a blob than a column",
            )
            return StitchType.TATAMI.value, conf, (
                f"Width ({width_mm:.2f}mm) falls inside the satin band, but this "
                f"shape isn't elongated (elongation ratio {elongation:.1f}) — a "
                "satin column needs a clear long axis, so a fill suits the "
                "geometry better than a column here."
            )
        conf = Confidence(
            0.9 if elongation > 2.5 else 0.75,
            f"width {width_mm:.2f}mm sits in the satin band with elongation {elongation:.1f}",
        )
        return StitchType.SATIN.value, conf, (
            f"Width is {width_mm:.2f}mm, inside the "
            f"{th.run_max_mm}-{th.satin_max_mm}mm satin band, with a clearly "
            f"elongated shape (ratio {elongation:.1f}) — a controlled column fits."
        )

    if width_mm <= th.split_satin_max_mm:
        conf = Confidence(
            0.6,
            f"width {width_mm:.2f}mm is in the split-satin/tatami boundary band",
        )
        return StitchType.SATIN.value, conf, (
            f"Width is {width_mm:.2f}mm, in the {th.satin_max_mm}-"
            f"{th.split_satin_max_mm}mm boundary band — satin is workable if "
            "split into two columns; tatami is the safer default without a "
            "human confirming the split is wanted."
        )

    conf = Confidence(
        0.8,
        f"width {width_mm:.2f}mm exceeds {th.split_satin_max_mm}mm — too wide for satin",
    )
    return StitchType.TATAMI.value, conf, (
        f"Width is {width_mm:.2f}mm, over the {th.split_satin_max_mm}mm satin "
        "ceiling — a fill stitch is the standard choice at this width."
    )


# ----------------------------------------------------------------------
# Density / underlay / pull compensation
# ----------------------------------------------------------------------


def recommend_density_mm(
    stitch_type: str, fabric: str = "woven", profile: ProductionProfile | str | None = None
) -> tuple[float, str]:
    """Row spacing (mm). `profile.spacing_scale` genuinely scales the result."""
    prof = get_profile(profile)
    table = {
        StitchType.SATIN.value: 0.40,
        StitchType.TATAMI.value: 0.42,
        StitchType.COMPLEX_FILL.value: 0.42,
        StitchType.RUN.value: prof.run_stitch_length_mm,
        StitchType.TRIPLE_RUN.value: prof.run_stitch_length_mm,
    }
    base = table.get(stitch_type, 0.40)
    if fabric == "knit" and stitch_type == StitchType.SATIN.value:
        base += 0.02
    is_run = stitch_type in (StitchType.RUN.value, StitchType.TRIPLE_RUN.value)
    value = round(base if is_run else base * prof.spacing_scale, 3)
    reason = (
        f"standard {stitch_type} value for {fabric} goods"
        + ("" if is_run else f", spacing x{prof.spacing_scale} for profile '{prof.name}'")
        + (f", run stitch length {prof.run_stitch_length_mm}mm for profile '{prof.name}'" if is_run else "")
    )
    return value, reason


_SATIN_TIERS = [UnderlayType.CENTER_RUN.value, UnderlayType.ZIGZAG.value, UnderlayType.DOUBLE_ZIGZAG.value]


def recommend_underlay(
    stitch_type: str, width_mm: float, profile: ProductionProfile | str | None = None
) -> tuple[str, str]:
    """Underlay tier from width, shifted by `profile.underlay_bias` (clamped)."""
    prof = get_profile(profile)
    if stitch_type == StitchType.SATIN.value:
        tier = 0 if width_mm <= 3.0 else (1 if width_mm <= 8.0 else 2)
        shifted = max(0, min(len(_SATIN_TIERS) - 1, tier + prof.underlay_bias))
        why = {0: "narrow satin (<=3mm)", 1: "mid-width satin (3-8mm)", 2: "wide satin (>8mm)"}[tier]
        if shifted != tier:
            why += f"; tier shifted {prof.underlay_bias:+d} by profile '{prof.name}'"
        return _SATIN_TIERS[shifted], why
    if stitch_type in (StitchType.TATAMI.value, StitchType.COMPLEX_FILL.value):
        return UnderlayType.TATAMI.value, "fill stitch: edge run + tatami underlay at an offset angle"
    return UnderlayType.NONE.value, f"{stitch_type} does not conventionally use underlay"


def recommend_pull_compensation_mm(
    stitch_type: str, width_mm: float, fabric: str = "woven", profile: ProductionProfile | str | None = None
) -> tuple[float, str]:
    prof = get_profile(profile)
    if stitch_type != StitchType.SATIN.value:
        return 0.0, f"{stitch_type} is not a column stitch; pull compensation does not apply the same way"

    base = 0.20 if width_mm <= 6.0 else 0.28
    fabric_add = {"knit": 0.10, "cap": 0.10, "towel": 0.08, "leather": 0.0, "woven": 0.0}.get(fabric, 0.0)
    value = round((base + fabric_add) * prof.pull_scale, 3)
    reason = (
        f"base {base}mm for a {'narrow' if width_mm <= 6.0 else 'wide'} satin column"
        + (f", +{fabric_add}mm for {fabric} stretch" if fabric_add else "")
        + (f", x{prof.pull_scale} for profile '{prof.name}'" if prof.pull_scale != 1.0 else "")
        + " (approximate rule, not a physical simulation)"
    )
    return value, reason


def recommend_direction(geometry_mm: list[tuple[float, float]], stitch_type: str) -> tuple[float | None, str, Confidence]:
    """Recommend a stitch angle in degrees, from the shape's own geometry.

    For a satin column, direction is reported perpendicular to the
    principal axis (the standard convention: stitches cross the column,
    not run along it). For a fill, the 45-degree default is used unless
    the shape's own axis strongly suggests otherwise, avoiding 0/90 degrees
    which visibly track the fabric grain.
    """
    if not geometry_mm:
        return None, "no geometry available to compute a direction", Confidence(0.1, "no geometry")

    axis_deg, elongation = principal_axis_deg(geometry_mm)

    if stitch_type == StitchType.SATIN.value:
        direction = (axis_deg + 90.0) % 180.0
        conf = Confidence(
            0.85 if elongation > 2.5 else 0.5,
            f"perpendicular to the shape's principal axis ({axis_deg:.0f}\u00b0), elongation {elongation:.1f}",
        )
        return direction, f"Perpendicular to the long axis ({axis_deg:.0f}\u00b0) so stitches cross the column.", conf

    if stitch_type in (StitchType.TATAMI.value, StitchType.COMPLEX_FILL.value):
        # Avoid 0/90 (tracks fabric grain visibly); default to 45, nudged by
        # the shape's own axis only when it's clearly elongated.
        if elongation > 2.0 and 20 < axis_deg < 70:
            return axis_deg, f"Shape has a clear axis at {axis_deg:.0f}\u00b0; following it avoids the fabric grain.", Confidence(0.6, "elongated fill, using its own axis")
        return 45.0, "Default 45\u00b0 fill angle \u2014 0/90\u00b0 would visibly track the fabric grain.", Confidence(0.55, "default fill angle, no strong shape axis")

    return None, f"{stitch_type} does not use a fill/column angle", Confidence(0.3, "not applicable to this stitch type")
