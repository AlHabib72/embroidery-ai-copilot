"""
Central configuration for every threshold the analyser uses.

Priority when the CLI runs:

    built-in defaults  ->  fabric preset  ->  explicit CLI override

Nothing here is an industry standard. These are working starting points;
tune them for your machine, thread and fabric and keep the numbers in one
place instead of scattered through the analysis code.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class AnalyzerConfig:
    # Machine / timing
    spm: int = 1000  # stitches per minute, for the runtime estimate
    trim_seconds: float = 1.6  # handling time charged per estimated trim
    color_change_seconds: float = 12.0  # handling time per colour change

    # Estimated-trim heuristic (DST stores no explicit trim command)
    machine_trim_mm: float = 7.0  # a jump longer than this is assumed to trim
    walkable_mm: float = 7.0  # travel this short could plausibly be a walked run instead of a trim

    # Stitch-length review bands
    tiny_stitch_mm: float = 0.3  # below this: dense needle penetrations in one spot
    short_stitch_mm: float = 0.5  # below this: worth a look (tiny_stitch_mm is a subset of this)
    long_stitch_mm: float = 10.0  # above this: flagged as "long stitch, review required"
    max_satin_mm: float = 12.0  # above this a long stitch is treated as more severe

    # Estimated local thread density (mm of thread per mm^2 of grid cell)
    density_warning: float = 4.5
    density_critical: float = 6.5
    grid_mm: float = 1.0  # density raster cell size

    # Thread/bobbin estimate multipliers — NOT physical constants, just the
    # working ratios this analyser uses; override them if your thread/machine
    # combination behaves differently.
    thread_factor: float = 1.15
    bobbin_factor: float = 0.42

    # QC finding trigger points (kept here, not hard-coded in rules.py)
    min_trims_to_flag: int = 12
    min_trim_cut_pct: int = 25
    min_jumps_to_flag: int = 40
    min_travel_cut_pct: int = 20
    max_tiny_stitches: int = 20
    max_short_stitches: int = 60
    max_dense_cells: int = 30
    max_small_segment_stitches: int = 12
    max_small_segments: int = 8
    max_stitches_per_cm2: int = 900

    # Provenance — which preset (if any) this config started from
    fabric: str = "custom"

    def override(self, **kwargs) -> "AnalyzerConfig":
        """Return a copy with only the non-None keyword arguments applied."""
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return replace(self, **clean) if clean else self

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class FabricPreset:
    name: str
    description: str
    config: AnalyzerConfig


FABRIC_PRESETS: dict[str, FabricPreset] = {
    "woven": FabricPreset(
        "woven",
        "Poplin, twill, canvas — baseline flat goods. Used when no preset is given.",
        AnalyzerConfig(fabric="woven"),
    ),
    "knit": FabricPreset(
        "knit",
        "Jersey, pique, interlock — stretch fabric distorts more per stitch.",
        AnalyzerConfig(
            fabric="knit",
            density_warning=4.0,
            density_critical=5.8,
            short_stitch_mm=0.6,
        ),
    ),
    "cap": FabricPreset(
        "cap",
        "Structured cap panel — tighter hoop, shorter safe travel before a trim.",
        AnalyzerConfig(
            fabric="cap",
            machine_trim_mm=5.0,
            walkable_mm=5.0,
            density_warning=4.0,
            density_critical=5.5,
            long_stitch_mm=8.0,
            spm=750,
        ),
    ),
    "towel": FabricPreset(
        "towel",
        "Terry, fleece — pile absorbs more thread before density becomes visible.",
        AnalyzerConfig(
            fabric="towel",
            density_warning=5.5,
            density_critical=7.5,
            long_stitch_mm=12.0,
        ),
    ),
    "leather": FabricPreset(
        "leather",
        "Leather, vinyl — every needle hole is permanent, run the machine slower.",
        AnalyzerConfig(
            fabric="leather",
            density_warning=3.2,
            density_critical=4.5,
            spm=600,
        ),
    ),
}


def list_fabrics() -> list[FabricPreset]:
    return [FABRIC_PRESETS[k] for k in sorted(FABRIC_PRESETS)]


def get_fabric_preset(name: str) -> FabricPreset:
    try:
        return FABRIC_PRESETS[name]
    except KeyError as exc:
        available = ", ".join(sorted(FABRIC_PRESETS))
        raise ValueError(
            f"Unknown fabric preset {name!r}. Available presets: {available}"
        ) from exc
