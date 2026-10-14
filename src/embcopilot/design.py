"""Canonical, format-independent, object-based design model (spec 34).

EmbroideryDesign is what exporters/adapters consume. It is built from a
pipeline result, serialises to an open JSON project format (".ecd.json"),
and carries colorways so thread mapping never requires re-generating geometry.
It is NOT an EMB file and never pretends to be."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import __version__
from .stitch import Stitch, StitchPlan


@dataclass
class DesignObject:
    object_id: str
    name: str
    semantic_type: str
    stitch_type: str
    density_mm: float | None
    angle_deg: float | None
    underlay: str | None
    pull_compensation_mm: float | None
    color: str  # design colour "rgb(r,g,b)"
    sequence_index: int
    outline_mm: list = field(default_factory=list)
    holes_mm: list = field(default_factory=list)


@dataclass
class EmbroideryDesign:
    metadata: dict
    canvas_mm: tuple[float, float]
    objects: list[DesignObject]
    stitches: list[Stitch]
    colorways: dict = field(default_factory=dict)  # name -> Colorway.to_dict()
    version: str = __version__

    def plan(self) -> StitchPlan:
        return StitchPlan(self.canvas_mm, list(self.stitches), [o.object_id for o in sorted(self.objects, key=lambda o: o.sequence_index)])

    def design_colors(self) -> list[str]:
        seen: list[str] = []
        for s in self.stitches:
            if s.color and s.color not in seen and s.command in ("STITCH", "COLOR_CHANGE"):
                seen.append(s.color)
        return seen

    def to_json(self) -> str:
        return json.dumps({
            "format": "embcopilot-design", "version": self.version, "metadata": self.metadata, "canvas_mm": self.canvas_mm,
            "objects": [o.__dict__ for o in self.objects], "stitches": [s.to_dict() for s in self.stitches], "colorways": self.colorways,
        }, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "EmbroideryDesign":
        d = json.loads(text)
        if d.get("format") != "embcopilot-design":
            raise ValueError("not an embcopilot design file")
        objs = [DesignObject(**o) for o in d["objects"]]
        st = [Stitch(s["x"], s["y"], s["command"], s["color"], s["object_id"], s["layer"], s["part"]) for s in d["stitches"]]
        return cls(d["metadata"], tuple(d["canvas_mm"]), objs, st, d.get("colorways", {}), d.get("version", ""))


def design_from_result(res, source_name: str = "", fabric: str = "", profile: str = "") -> EmbroideryDesign:
    plans = {p.object_id: p for p in res.technical.plans}
    pos = {o: i for i, o in enumerate(res.plan.order)}
    objs = []
    for o in res.objects:
        if o.object_id not in pos:
            continue
        tp = plans[o.object_id]
        st = res.plan.object_status[o.object_id]["strategy"]
        objs.append(DesignObject(o.object_id, o.name, o.category, st or tp.stitch_type, tp.density_mm, tp.direction_deg, tp.underlay,
                                 tp.pull_compensation_mm, o.color_group, pos[o.object_id],
                                 [list(p) for p in o.geometry], [[list(p) for p in h] for h in o.holes_mm]))
    return EmbroideryDesign({"source": source_name, "fabric": fabric, "profile": profile, "generator": f"embcopilot {__version__}",
                             "note": "Object-based project model. Not a Wilcom EMB file."},
                            res.plan.canvas_mm, objs, list(res.plan.stitches))
