"""
QC rule definitions.

Each `rule_*` function inspects the already-computed Analysis fields against
the active AnalyzerConfig and returns an Issue, or None if it doesn't fire.
Severities and trigger points are this software's own heuristic judgement —
not a certified industry standard for embroidery quality. See README.md.

Keeping this as a flat list of small, independent functions (rather than one
long branching block) is deliberate: each rule can be unit-tested and tuned
on its own without touching parsing or metrics code.
"""

from __future__ import annotations

from .config import AnalyzerConfig
from .models import Analysis, Issue

_SEVERITY_PENALTY = {"critical": 14, "high": 8, "medium": 4, "low": 2}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_GRADE_BANDS = (
    (85, "Production ready"),
    (70, "Good"),
    (50, "Needs work"),
    (0, "Rework"),
)


def _pct_cut(before: float, after: float) -> int:
    if before <= 0:
        return 0
    return max(0, int(round(100 * (before - after) / before)))


def rule_estimated_trims(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    cut = _pct_cut(a.estimated_trims, a.trims_theoretical)
    if a.estimated_trims <= cfg.min_trims_to_flag or cut < cfg.min_trim_cut_pct:
        return None
    saved = a.estimated_trims - a.trims_theoretical
    return Issue(
        severity="critical" if a.estimated_trims > 2 * cfg.min_trims_to_flag else "high",
        code="estimated_trims",
        title="High estimated trim count",
        detail=(
            f"{a.estimated_trims} estimated trims, inferred from jump length "
            f"and jump-run patterns (DST stores no explicit trim command, so "
            f"this is an estimate, not a machine-recorded count). A "
            f"nearest-neighbour re-ordering of segments within each colour "
            f"gives a theoretical total of about {a.trims_theoretical}."
        ),
        why_it_matters=(
            "Each trim adds machine time and is a point where the thread "
            "tail can tangle or the next stitch can start with a loose end."
        ),
        recommendation=(
            "Review the sew order of objects within each colour block; "
            "group nearby shapes together before crossing the hoop."
        ),
        saving=f"~{saved} fewer trims ({cut}% estimated reduction)" if saved > 0 else "",
        count=a.estimated_trims,
    )


def rule_wasted_travel(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    cut = _pct_cut(a.travel_actual_mm, a.travel_theoretical_mm)
    if a.jumps <= cfg.min_jumps_to_flag or cut < cfg.min_travel_cut_pct:
        return None
    return Issue(
        severity="critical" if cut > 50 else "high",
        code="wasted_travel",
        title="Wasted travel between objects",
        detail=(
            f"{a.jumps} jump stitches cover an estimated {a.travel_actual_mm:.0f} mm "
            f"of travel. A theoretical nearest-neighbour ordering of the same "
            f"segments covers roughly {a.travel_theoretical_mm:.0f} mm. This is a "
            "preview calculation, not a rewritten file."
        ),
        why_it_matters="Extra travel means more jump stitches, more places for the machine to trim, and a longer cycle time.",
        recommendation="Re-sequence objects within each colour so nearby shapes are sewn back-to-back.",
        saving=f"~{a.travel_actual_mm - a.travel_theoretical_mm:.0f} mm ({cut}% estimated reduction)",
        count=a.jumps,
    )


def rule_tiny_stitches(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    if a.tiny_stitches <= cfg.max_tiny_stitches:
        return None
    return Issue(
        severity="critical",
        code="tiny_stitches",
        title=f"Stitches under {cfg.tiny_stitch_mm} mm",
        detail=(
            f"{a.tiny_stitches} stitches are shorter than {cfg.tiny_stitch_mm} mm — "
            "the needle penetrates almost the same spot as the previous stitch."
        ),
        why_it_matters=(
            "Potential risk: a cluster of very short stitches can mean excessive "
            "needle penetrations in one spot, added thread stress, and — "
            "depending on fabric, thread and machine condition — a higher chance "
            "of thread breaks or fabric damage. This is a risk indicator, not a guarantee."
        ),
        recommendation="Inspect the affected area in the original digitizing file; consider stitch elimination or cleaning up outline nodes.",
        count=a.tiny_stitches,
        bbox=a.tiny_stitch_bbox,
    )


def rule_short_stitches(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    # Tiny stitches are a subset of short stitches; only raise this one when
    # the tiny-stitch rule didn't already fire, so the same cluster isn't
    # reported twice at two severities.
    if a.tiny_stitches > cfg.max_tiny_stitches:
        return None
    if a.short_stitches <= cfg.max_short_stitches:
        return None
    return Issue(
        severity="high",
        code="short_stitches",
        title=f"Short stitches under {cfg.short_stitch_mm} mm",
        detail=(
            f"{a.short_stitches} stitches are shorter than {cfg.short_stitch_mm} mm, "
            "often where a column turns sharply."
        ),
        why_it_matters="Potential risk: a high count of short stitches can indicate corners that need short-stitch control.",
        recommendation="Add or increase short-stitch handling on sharp turns.",
        count=a.short_stitches,
        bbox=a.short_stitch_bbox,
    )


def rule_long_stitches(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    if a.long_stitches <= 0:
        return None
    over_satin_ceiling = a.stitch_length_max_mm > cfg.max_satin_mm
    return Issue(
        severity="critical" if over_satin_ceiling else "high",
        code="long_stitches",
        title="Long stitch — review required",
        detail=(
            f"{a.long_stitches} stitches exceed {cfg.long_stitch_mm} mm "
            f"(longest measured: {a.stitch_length_max_mm:.1f} mm)."
            + (
                f" This is above the configured {cfg.max_satin_mm} mm ceiling."
                if over_satin_ceiling
                else ""
            )
        ),
        why_it_matters=(
            "A long stitch can be an unsplit satin column, an intentional long "
            "run, or a digitizing artefact — the stitch file alone doesn't say "
            "which. Long floats generally snag more easily and can lose sheen."
        ),
        recommendation="Review the flagged area in the digitizing software and confirm the stitch type/length is intentional.",
        count=a.long_stitches,
        bbox=a.long_stitch_bbox,
    )


def rule_density_critical(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    if a.critical_cells <= 0:
        return None
    return Issue(
        severity="critical",
        code="density_critical",
        title="Estimated local thread-density proxy is very high",
        detail=(
            f"{a.critical_cells} mm\u00b2 of the design carries an estimated thread-"
            f"density proxy above {cfg.density_critical} mm of thread per mm\u00b2. "
            "This is a geometric proxy computed from stitch coordinates, not "
            "Wilcom's own density figure."
        ),
        why_it_matters="Potential risk: very high stitch density can lead to puckering, needle heat, or thread breakage depending on fabric, stabiliser and thread.",
        recommendation="Check for stacked/overlapping objects or underlay that was not reduced where a top stitch covers it.",
        count=a.critical_cells,
        bbox=a.worst_density_bbox,
    )


def rule_density_warning(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    if a.critical_cells > 0:
        return None  # already covered by the critical rule
    if a.dense_cells <= cfg.max_dense_cells:
        return None
    return Issue(
        severity="medium",
        code="density_warning",
        title="Estimated local thread-density proxy: dense overlap zones",
        detail=(
            f"{a.dense_cells} mm\u00b2 sit above an estimated thread-density "
            f"proxy of {cfg.density_warning} mm of thread per mm\u00b2, commonly "
            "where two fills or a border and a fill overlap."
        ),
        why_it_matters="Worth a visual check for puckering risk before production.",
        recommendation="Reduce fill density under an overlapping border, or trim the overlap to a smaller margin.",
        count=a.dense_cells,
        bbox=a.worst_density_bbox,
    )


def rule_fragmented_segments(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    small = [s for s in a.segments if s.stitches < cfg.max_small_segment_stitches]
    if len(small) <= cfg.max_small_segments:
        return None
    xs0 = [s.bbox[0] for s in small]
    ys0 = [s.bbox[1] for s in small]
    xs1 = [s.bbox[2] for s in small]
    ys1 = [s.bbox[3] for s in small]
    return Issue(
        severity="medium",
        code="fragmented_segments",
        title="Many small reconstructed segments",
        detail=(
            f"{len(small)} reconstructed segments are under "
            f"{cfg.max_small_segment_stitches} stitches each."
        ),
        why_it_matters="Each small segment costs its own tie-off, lock stitch and likely a trim, adding time without adding visible detail.",
        recommendation="Consider merging small fragments into a neighbouring object or removing ones that add no visible value.",
        count=len(small),
        bbox=(min(xs0), min(ys0), max(xs1), max(ys1)),
    )


def rule_heavy_stitch_count(a: Analysis, cfg: AnalyzerConfig) -> Issue | None:
    """Area-normalised stitch count — NOT an embroidery density figure.

    stitches / bounding-box-area is a coarse size-vs-count ratio. It says
    nothing about how thread is distributed inside that area (a sparse
    outline and a solid fill of the same stitch count score identically),
    so this is reported as its own "heavy stitch count" finding rather than
    folded into the thread-density proxy above.
    """
    if a.width_mm <= 0 or a.stitches <= 0:
        return None
    area_cm2 = (a.width_mm / 10.0) * (a.height_mm / 10.0)
    if area_cm2 <= 0:
        return None
    per_cm2 = a.stitches / area_cm2
    if per_cm2 <= cfg.max_stitches_per_cm2:
        return None
    return Issue(
        severity="medium",
        code="heavy_stitch_count",
        title="Heavy stitch count for the design's bounding-box area",
        detail=(
            f"An area-normalized stitch metric of about {per_cm2:.0f} "
            f"stitches per cm\u00b2 across a {a.width_mm:.0f} x {a.height_mm:.0f} mm "
            "bounding box. This is stitch-count-over-area, not an embroidery "
            "density measurement — it does not know how thread is actually "
            "distributed within that box."
        ),
        why_it_matters="A high stitch-per-area ratio tends to produce a stiff, board-like feel rather than soft embroidery.",
        recommendation="Consider lightening fill density before adding further detail.",
    )


RULES = (
    rule_estimated_trims,
    rule_wasted_travel,
    rule_tiny_stitches,
    rule_short_stitches,
    rule_long_stitches,
    rule_density_critical,
    rule_density_warning,
    rule_fragmented_segments,
    rule_heavy_stitch_count,
)


def evaluate(a: Analysis, cfg: AnalyzerConfig) -> list[Issue]:
    """Run every rule and return the triggered Issues, most severe first."""
    issues = [issue for rule in RULES if (issue := rule(a, cfg)) is not None]
    issues.sort(key=lambda i: _SEVERITY_ORDER[i.severity])
    return issues


def score(issues: list[Issue]) -> tuple[int, str]:
    """A software heuristic score (0-100), not a certified quality metric.

    Starts at 100 and subtracts a fixed penalty per issue severity. This is
    intentionally simple and documented so it can be argued with — see
    README.md "QC Heuristic Score" for the disclaimer this pairs with.
    """
    penalty = sum(_SEVERITY_PENALTY[i.severity] for i in issues)
    value = max(0, min(100, 100 - penalty))
    grade = next(label for floor, label in _GRADE_BANDS if value >= floor)
    return value, grade


def severity_counts(issues: list[Issue]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for i in issues:
        counts[i.severity] += 1
    return counts
