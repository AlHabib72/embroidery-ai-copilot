"""
Data models shared across the analyser.

Nothing in this module does any file I/O or number-crunching — it only
defines the shapes that the parser (dst.py), the metrics (metrics.py), the
rules (rules.py) and the report (report.py) all pass around.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum


class StitchKind(str, Enum):
    """The record types found in a Tajima DST stitch stream.

    STITCH, JUMP and COLOR_CHANGE are universal. SEQUIN appears in files
    that place sequins and is tracked separately but not deeply analysed
    in Phase 1. END marks the single end-of-design record.
    """

    STITCH = "stitch"
    JUMP = "jump"
    COLOR_CHANGE = "color_change"
    SEQUIN = "sequin"
    END = "end"


UNITS_PER_MM = 10.0  # Tajima DST stores coordinates in 0.1 mm units


@dataclass(frozen=True)
class Record:
    """One machine movement, in absolute 0.1 mm coordinates.

    `dx`/`dy` are this record's own movement relative to the position
    immediately before it (whatever kind that prior record was), decoded
    straight from the file's ternary-encoded bytes.
    """

    x: int
    y: int
    dx: int
    dy: int
    kind: StitchKind

    @property
    def length_mm(self) -> float:
        """Straight-line length of this movement, in millimetres."""
        return math.hypot(self.dx, self.dy) / UNITS_PER_MM


@dataclass
class Design:
    """A parsed stitch file: header fields plus the full record stream."""

    records: list[Record] = field(default_factory=list)
    header: dict[str, str] = field(default_factory=dict)
    source: str = ""

    def bounds_mm(self) -> tuple[float, float, float, float]:
        """(min_x, min_y, max_x, max_y) over every needle/jump position, in mm."""
        pts = [
            (r.x, r.y)
            for r in self.records
            if r.kind in (StitchKind.STITCH, StitchKind.JUMP)
        ]
        if not pts:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (
            min(xs) / UNITS_PER_MM,
            min(ys) / UNITS_PER_MM,
            max(xs) / UNITS_PER_MM,
            max(ys) / UNITS_PER_MM,
        )

    def size_mm(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bounds_mm()
        return (x1 - x0, y1 - y0)


@dataclass
class Segment:
    """A reconstructed, unbroken run of needle penetrations between two travel moves.

    IMPORTANT: Tajima DST does not preserve Wilcom's native object
    boundaries — a DST is just a stream of needle moves. A "segment" here
    is this analyser's own reconstruction (one run between jumps/colour
    changes), used as a stand-in for "one object". It is usually close to
    the original object count but is not guaranteed to match it exactly
    (e.g. a single Wilcom object sewn with an internal jump becomes two
    segments here; two objects sewn back-to-back with no jump between
    them become one segment here).
    """

    color_index: int
    stitches: int
    start: tuple[float, float]
    end: tuple[float, float]
    length_mm: float
    bbox: tuple[float, float, float, float]

    @property
    def width_mm(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height_mm(self) -> float:
        return self.bbox[3] - self.bbox[1]


@dataclass
class Issue:
    """One QC finding.

    `severity` and `title` are the heuristic judgement; `detail` carries the
    evidence (the actual numbers); `why_it_matters` and `recommendation` are
    kept separate so the report can lay them out as distinct fields instead
    of one run-on paragraph. `bbox` is the affected area in design-space mm,
    when the analyser could localise it — None when it could not.
    """

    severity: str  # "critical" | "high" | "medium" | "low"
    code: str
    title: str
    detail: str
    why_it_matters: str = ""
    recommendation: str = ""
    saving: str = ""
    count: int = 0
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class Analysis:
    """The full result of analysing one Design under one AnalyzerConfig.

    Field names ending in `_mm`, `_metres`, `_minutes` etc. are ESTIMATES
    derived purely from stitch geometry, not measurements taken from a
    real machine or a real spool. See README.md "Limitations" before
    treating any of these as exact production figures.
    """

    # --- identity / provenance -----------------------------------------
    name: str = ""
    source: str = ""
    analyzer_version: str = ""
    fabric_preset: str = ""
    generated_at: str = ""
    config_used: dict = field(default_factory=dict)

    # --- raw counts -------------------------------------------------------
    stitches: int = 0
    jumps: int = 0
    sequins: int = 0
    colors: int = 0
    width_mm: float = 0.0
    height_mm: float = 0.0

    # --- reconstructed structure -----------------------------------------
    segments: list[Segment] = field(default_factory=list)
    color_blocks: list[dict] = field(default_factory=list)

    # --- stitch-length findings -------------------------------------------
    stitch_length_avg_mm: float = 0.0
    stitch_length_max_mm: float = 0.0
    needle_length_mm: float = 0.0
    short_stitches: int = 0
    tiny_stitches: int = 0
    long_stitches: int = 0
    short_stitch_bbox: tuple[float, float, float, float] | None = None
    tiny_stitch_bbox: tuple[float, float, float, float] | None = None
    long_stitch_bbox: tuple[float, float, float, float] | None = None

    # --- estimated production figures -------------------------------------
    thread_metres: float = 0.0
    bobbin_metres: float = 0.0
    runtime_minutes: float = 0.0

    # --- estimated trims / travel ------------------------------------------
    estimated_trims: int = 0
    travel_actual_mm: float = 0.0
    travel_theoretical_mm: float = 0.0
    trims_theoretical: int = 0

    # --- estimated local density -------------------------------------------
    dense_cells: int = 0
    critical_cells: int = 0
    worst_density_bbox: tuple[float, float, float, float] | None = None

    # --- QC outcome ---------------------------------------------------------
    issues: list[Issue] = field(default_factory=list)
    score: int = 0
    grade: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
