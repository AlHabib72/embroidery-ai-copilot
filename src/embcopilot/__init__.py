"""
Embroidery AI Copilot.

Phase 1 is a rule-based embroidery QC analyzer, not an automatic AI
digitizer. It reads a Tajima DST stitch file, computes production
metrics and heuristic QC findings, and writes an HTML/JSON report.
It never modifies the input file.

Phase 2 adds a file-format-independent domain model (domain.py), a
rule-based digitizing knowledge engine (knowledge.py) that turns
geometry into stitch-type/density/underlay/pull-compensation/direction
recommendations with explicit reasoning and confidence, an offline
geometry-only vision engine (vision.py), a Technical Blueprint engine
(technical.py), a keyword-based command parser (command.py), connector
and sequence engines (connector.py, sequence.py), and a Wilcom
automation ARCHITECTURE (automation/) that currently only has a mock
adapter — no real Wilcom automation exists yet. See README.md for the
full scope, what's implemented vs architecture-only, and the roadmap.
"""

from __future__ import annotations

# SINGLE canonical version source. pyproject.toml reads this via
# [tool.setuptools.dynamic]; CLI, reports and metadata import it.
__version__ = "0.7.0"

from .config import AnalyzerConfig, FabricPreset, get_fabric_preset, list_fabrics
from .models import Analysis, Design, Issue, Record, Segment, StitchKind
from .dst import CorruptDSTError, DSTError, InvalidDSTError, read_dst, write_dst
from .analyze import analyze

__all__ = [
    "__version__",
    "AnalyzerConfig",
    "FabricPreset",
    "get_fabric_preset",
    "list_fabrics",
    "Analysis",
    "Design",
    "Issue",
    "Record",
    "Segment",
    "StitchKind",
    "CorruptDSTError",
    "DSTError",
    "InvalidDSTError",
    "read_dst",
    "write_dst",
    "analyze",
]
