"""
Phase 2 core domain model.

Everything in this module is deliberately independent of DST, EMB, or any
other file format — the point is that the AI reasons about an abstract
embroidery design (objects, stitch intent, sequence, confidence) rather
than directly about file bytes. File-format adapters (dst.py today, an EMB
adapter, future PES/JEF adapters) translate between this model and a
specific format; the model itself never imports them.

Nothing here performs vision, digitizing, or automation — see vision.py,
technical.py, and automation/ for that. This module only defines the
shapes those subsystems pass around, same spirit as models.py in Phase 1.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum


# ----------------------------------------------------------------------
# Object categories — open, not a closed enum, so new design types don't
# require a code change. The Enum below is a set of well-known starting
# categories; anything else is a plain string and still valid.
# ----------------------------------------------------------------------


class ObjectCategory(str, Enum):
    BACKGROUND = "background"
    OUTLINE = "outline"
    BODY = "body"
    WING = "wing"
    FEATHER = "feather"
    LEAF = "leaf"
    FLOWER = "flower"
    STEM = "stem"
    BRANCH = "branch"
    TEXT = "text"
    BADGE = "badge"
    APPLIQUE = "applique"
    BORDER = "border"
    DETAIL = "detail"
    UNKNOWN = "unknown"


class StitchType(str, Enum):
    RUN = "run"
    TRIPLE_RUN = "triple_run"
    MANUAL_RUN = "manual_run"
    SATIN = "satin"
    TATAMI = "tatami"
    COMPLEX_FILL = "complex_fill"
    ZIGZAG = "zigzag"
    APPLIQUE = "applique"
    MOTIF = "motif"
    CROSS_STITCH = "cross_stitch"
    PUFF = "puff"
    GRADIENT = "gradient"
    TEXT = "text"
    UNDECIDED = "undecided"


class UnderlayType(str, Enum):
    NONE = "none"
    EDGE_RUN = "edge_run"
    CENTER_RUN = "center_run"
    ZIGZAG = "zigzag"
    DOUBLE_ZIGZAG = "double_zigzag"
    TATAMI = "tatami"


class ConnectorStrategy(str, Enum):
    TRIM = "trim"
    JUMP = "jump"
    HIDDEN_TRAVEL = "hidden_travel"
    EDGE_TRAVEL = "edge_travel"
    RUN_TRAVEL = "run_travel"


class RiskLevel(str, Enum):
    SAFE = "safe"
    LOW = "low_risk"
    MEDIUM = "medium_risk"
    HIGH = "high_risk"
    IRREVERSIBLE = "irreversible"


class ApprovalState(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionStatus(str, Enum):
    """Never report SUCCESS unless the result was actually verified."""

    PLANNED = "planned"
    EXECUTING = "executing"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"  # screen state could not be verified -- NOT success


class CommandKind(str, Enum):
    """What kind of thing a parsed natural-language command asked for."""

    QUERY = "query"
    SUGGESTION = "suggestion"
    SIMULATION = "simulation"
    ACTION = "action"
    IRREVERSIBLE_ACTION = "irreversible_action"


class CapabilityState(str, Enum):
    """Honesty labels for any capability this project might claim to have."""

    SUPPORTED = "supported"
    PARTIAL = "partial"
    EXPERIMENTAL = "experimental"
    PLANNED = "planned"
    UNAVAILABLE = "unavailable"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------------------------------------------------------------


@dataclass
class Confidence:
    """A confidence score with the reasoning behind it — never a bare number.

    Confidence must be object-specific (one score per decision, e.g. "wing
    separation: 94%", "underlay: 76%"), not one global figure for an entire
    design — see knowledge.py / technical.py for where these get created.
    """

    value: float  # 0.0 - 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        self.value = max(0.0, min(1.0, self.value))

    @property
    def pct(self) -> int:
        return round(self.value * 100)


@dataclass
class ConfidenceThresholds:
    """Configurable — these are default engineering thresholds, not universal truths."""

    # Bands follow the project spec: >=95% low-risk auto allowed; 85-95%
    # preview/confirmation; 70-85% mandatory approval; <70% never automatic.
    # (Phase 1/2 shipped auto_safe=0.90, which contradicted the spec.)
    auto_safe: float = 0.95  # >= this: low-risk automatic action allowed
    ask_approval: float = 0.85  # [0.85, 0.95): preview + confirmation
    require_review: float = 0.70  # [0.70, 0.85): mandatory approval
    # below require_review: do not automatically execute

    def __post_init__(self) -> None:
        if not (0.0 <= self.require_review <= self.ask_approval <= self.auto_safe <= 1.0):
            raise ValueError(
                "ConfidenceThresholds must satisfy 0 <= require_review <= "
                "ask_approval <= auto_safe <= 1"
            )

    def band(self, value: float) -> str:
        if value >= self.auto_safe:
            return "automatic_safe"
        if value >= self.ask_approval:
            return "ask_approval"
        if value >= self.require_review:
            return "require_review"
        return "do_not_auto_execute"


@dataclass
class EmbroideryObject:
    """One embroidery object in the abstract domain model.

    `geometry` is deliberately typed loosely (a list of (x, y) mm points
    for a simplified outline) rather than tied to a specific vision
    library's contour type — see vision.py for how it gets populated.
    """

    object_id: str
    name: str
    category: str = ObjectCategory.UNKNOWN.value
    geometry: list[tuple[float, float]] = field(default_factory=list)
    bbox_mm: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    layer: int = 0
    color_group: str = ""
    stitch_type: str = StitchType.UNDECIDED.value
    stitch_direction_deg: float | None = None
    density_mm: float | None = None
    underlay: str = UnderlayType.NONE.value
    pull_compensation_mm: float | None = None
    start_point_mm: tuple[float, float] | None = None
    end_point_mm: tuple[float, float] | None = None
    connector_strategy: str = ConnectorStrategy.JUMP.value
    sequence_index: int | None = None
    confidence: dict[str, Confidence] = field(default_factory=dict)
    parent_object_id: str | None = None
    child_object_ids: list[str] = field(default_factory=list)
    source: str = ""  # e.g. "vision:local_contour", "manual", "dst:segment_3"
    # --- Vision V2 geometry (all optional; None/empty = not measured) -------
    holes_mm: list[list[tuple[float, float]]] = field(default_factory=list)
    islands_mm: list[list[tuple[float, float]]] = field(default_factory=list)
    centerline_mm: list[tuple[float, float]] | None = None
    area_mm2: float | None = None  # true filled area (outer minus holes)
    # Measured from a distance transform / skeleton, NOT the bbox proxy:
    # keys: min, median, max, p05, p95 (mm), plus "centerline_length_mm".
    width_stats_mm: dict | None = None
    skeleton_topology: dict | None = None  # {"endpoints": n, "branch_points": n}
    centerline_widths_mm: list[float] | None = None  # local width at each centerline point
    # --- Phase C/D: semantics + blueprint relations ---------------------------
    semantic_alternatives: list[dict] = field(default_factory=list)  # [{"type","confidence"}]
    semantic_reason: str = ""
    z_order: int = 0
    relations: list[dict] = field(default_factory=list)  # [{"kind","target"}]
    params: dict = field(default_factory=dict)  # per-object stitch options (applique border, puff density, cross count, ...)

    @property
    def width_mm(self) -> float:
        return self.bbox_mm[2] - self.bbox_mm[0]

    @property
    def height_mm(self) -> float:
        return self.bbox_mm[3] - self.bbox_mm[1]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = {k: asdict(v) for k, v in self.confidence.items()}
        return d


@dataclass
class Decision:
    """One reasoned output of the knowledge/decision engine.

    Every decision the system makes that a human might question should be
    representable as one of these: what, why, how sure, how risky, does it
    need a human to say yes.
    """

    decision_id: str
    subject_object_id: str
    action: str
    reason: str
    confidence: Confidence
    risk: RiskLevel = RiskLevel.LOW
    requires_approval: bool = True
    approval_state: str = ApprovalState.PROPOSED.value

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class SequenceItem:
    object_id: str
    color_group: str
    stage: str  # e.g. "underlay", "base_fill", "detail", "outline"
    dependencies: list[str] = field(default_factory=list)
    start_point_mm: tuple[float, float] | None = None
    end_point_mm: tuple[float, float] | None = None


@dataclass
class DesignProject:
    """Top-level container tying a design's whole life-cycle together."""

    project_id: str
    name: str = "Untitled"
    source_files: list[str] = field(default_factory=list)
    reference_image: str | None = None
    canvas_mm: tuple[float, float] = (0.0, 0.0)
    objects: list[EmbroideryObject] = field(default_factory=list)
    sequence: list[SequenceItem] = field(default_factory=list)
    color_plan: list[dict] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    qc_results: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "source_files": self.source_files,
            "reference_image": self.reference_image,
            "canvas_mm": self.canvas_mm,
            "objects": [o.to_dict() for o in self.objects],
            "sequence": [asdict(s) for s in self.sequence],
            "color_plan": self.color_plan,
            "decisions": [d.to_dict() for d in self.decisions],
            "qc_results": self.qc_results,
            "metadata": self.metadata,
            "version": self.version,
        }
