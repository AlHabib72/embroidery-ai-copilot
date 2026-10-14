"""
Reference-vs-design comparison engine.

HONESTY NOTE: pixel-perfect comparison is NOT appropriate for embroidery
(project brief section 34) — a raster reference image and stitch geometry
are fundamentally different representations. This module does the coarse,
honest version of that comparison: it compares OBJECT COUNTS between what
the vision engine detected in the reference image and what the DST
analyser reconstructed as segments in the actual design, plus a rough size
(bounding-box) comparison. It does NOT claim shape similarity, exact
position matching, or semantic correctness (it cannot know that "the wing
in the reference" corresponds to "segment 7 in the design" without a
categorised blueprint and a real shape-matching algorithm, neither of
which this build has).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Analysis
from .vision import ObjectBlueprint


@dataclass
class ComparisonReport:
    reference_object_count: int
    design_segment_count: int
    object_coverage_note: str
    size_note: str
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "reference_object_count": self.reference_object_count,
            "design_segment_count": self.design_segment_count,
            "object_coverage_note": self.object_coverage_note,
            "size_note": self.size_note,
            "caveats": self.caveats,
        }


def compare(blueprint: ObjectBlueprint, analysis: Analysis) -> ComparisonReport:
    ref_count = len(blueprint.objects)
    design_count = len(analysis.segments)
    diff = design_count - ref_count

    if diff == 0:
        coverage = f"Reference detected {ref_count} object(s); the design has {design_count} reconstructed segment(s) — counts match."
    elif diff > 0:
        coverage = (
            f"The design has {diff} more reconstructed segment(s) ({design_count}) than "
            f"the reference had detected objects ({ref_count}). This can mean extra "
            "detail was added, or a single reference object was split across multiple "
            "segments during digitizing (e.g. an internal jump) — it is not proof of "
            "either on its own."
        )
    else:
        coverage = (
            f"The design has {-diff} fewer reconstructed segment(s) ({design_count}) than "
            f"the reference had detected objects ({ref_count}). This can mean an object "
            "is missing, or that multiple reference objects were sewn as one continuous "
            "segment — it is not proof of either on its own."
        )

    ref_w, ref_h = blueprint.target_size_mm
    size_note = (
        f"Reference target size: {ref_w:.1f} x {ref_h:.1f} mm. "
        f"Design bounding box: {analysis.width_mm:.1f} x {analysis.height_mm:.1f} mm."
    )

    return ComparisonReport(
        reference_object_count=ref_count,
        design_segment_count=design_count,
        object_coverage_note=coverage,
        size_note=size_note,
        caveats=[
            "This is a coarse, count-based comparison, not a shape-matching or "
            "position-matching algorithm — it cannot confirm objects correspond "
            "to each other, only that their counts and overall sizes differ.",
            "Reconstructed segments are DST-inferred (see models.Segment), not "
            "native object boundaries from either the reference or the design.",
        ],
    )
