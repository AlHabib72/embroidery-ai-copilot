"""Structured intent schema + validation.  Anything not in this schema is rejected (reported, never executed)."""
from __future__ import annotations

from dataclasses import dataclass, field

STITCHES = ("run", "triple_run", "satin", "tatami", "complex_fill", "applique", "puff", "cross_stitch", "input_c")
UNDERLAYS = ("none", "center_run", "edge_run", "zigzag", "double_zigzag", "tatami")
FABRICS = ("CANVAS", "CAP", "DENIM", "FLEECE", "JERSEY", "PATCH", "PIQUE", "STRETCH_FABRIC", "TOWEL", "TWILL", "T_SHIRT")
OPTIMIZE = ("travel", "fewer_trims", "short_stitches")
EXPORTS = ("dst", "exp", "pes", "jef", "emb")
RANGES = {"density_mm": (0.25, 0.9), "pull_comp_mm": (0.0, 0.8), "pull_comp_pct": (0.0, 20.0), "max_stitch_mm": (4.0, 12.1),
          "min_stitch_mm": (0.2, 1.0), "width_mm": (0.8, 30.0), "angle_deg": (-360.0, 360.0),
          "underlay_spacing_mm": (0.8, 6.0), "underlay_inset_mm": (0.1, 2.0)}

# op -> (required params, optional params)
OPS = {
    "set_stitch_type": ({"stitch_type"}, {"width_mm"}),
    "set_width": ({"width_mm"}, set()),
    "set_density": ({"density_mm"}, set()),
    "set_pull_comp": (set(), {"pull_comp_mm", "pull_comp_pct"}),
    "set_underlay": ({"underlay"}, {"underlay_spacing_mm", "underlay_inset_mm"}),
    "set_angle": ({"angle_deg"}, set()),
    "set_fabric": ({"fabric"}, set()),
    "set_color": ({"color_hex"}, set()),
    "delete_object": (set(), set()),
    "auto_split": ({"max_stitch_mm"}, set()),
    "min_stitch": ({"min_stitch_mm"}, set()),
    "optimize": ({"what"}, set()),
    "protect": (set(), set()),
    "export": ({"format"}, set()),
    "emb_info": (set(), set()),
    "qc": (set(), set()),
}
GLOBAL_OPS = {"set_fabric", "auto_split", "min_stitch", "optimize", "export", "emb_info", "qc"}


@dataclass
class Step:
    op: str
    target: str | None = None
    params: dict = field(default_factory=dict)
    source_text: str = ""

    def to_dict(self):
        return {"op": self.op, "target": self.target, "params": self.params, "source_text": self.source_text}


def _num(v):
    if isinstance(v, bool) or v is None:
        raise ValueError("not a number")
    return float(v)


def validate_step(raw: dict) -> Step:
    if not isinstance(raw, dict):
        raise ValueError("step is not an object")
    op = str(raw.get("op", ""))
    if op not in OPS:
        raise ValueError(f"unknown operation {op!r}")
    req, opt = OPS[op]
    params = {k: v for k, v in raw.items() if k not in ("op", "target", "source_text")}
    unknown = set(params) - req - opt
    if unknown:
        raise ValueError(f"{op}: unexpected parameter(s) {sorted(unknown)}")
    missing = req - set(params)
    if missing:
        raise ValueError(f"{op}: missing {sorted(missing)}")
    if op == "set_pull_comp" and not (set(params) & {"pull_comp_mm", "pull_comp_pct"}):
        raise ValueError("set_pull_comp needs pull_comp_mm or pull_comp_pct")
    clean: dict = {}
    for k, v in params.items():
        if k in RANGES:
            x = _num(v)
            lo, hi = RANGES[k]
            if not lo <= x <= hi:
                raise ValueError(f"{op}: {k}={x:g} outside allowed range {lo:g}..{hi:g}")
            clean[k] = x
        elif k == "stitch_type":
            if str(v) not in STITCHES:
                raise ValueError(f"unknown stitch_type {v!r}")
            clean[k] = str(v)
        elif k == "underlay":
            vals = v if isinstance(v, list) else [v]
            if not vals or any(str(x) not in UNDERLAYS for x in vals):
                raise ValueError(f"unknown underlay in {v!r}; allowed {UNDERLAYS}")
            clean[k] = [str(x) for x in vals]
        elif k == "fabric":
            f = str(v).upper().replace("-", "_").replace(" ", "_")
            if f not in FABRICS:
                raise ValueError(f"unknown fabric {v!r}; allowed {FABRICS}")
            clean[k] = f
        elif k == "what":
            vals = v if isinstance(v, list) else [v]
            if any(str(x) not in OPTIMIZE for x in vals):
                raise ValueError(f"optimize.what must be within {OPTIMIZE}")
            clean[k] = [str(x) for x in vals]
        elif k == "format":
            if str(v).lower() not in EXPORTS:
                raise ValueError(f"unknown export format {v!r}")
            clean[k] = str(v).lower()
        elif k == "color_hex":
            s = str(v).strip()
            if not (len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in s[1:])):
                raise ValueError(f"bad colour {v!r}; expected #RRGGBB")
            clean[k] = s.upper()
    target = raw.get("target")
    if op not in GLOBAL_OPS and not target:
        target = "all"
    if target is not None and (not isinstance(target, str) or len(target) > 64):
        raise ValueError("bad target")
    return Step(op, target, clean, str(raw.get("source_text", ""))[:200])


def validate_steps(raw_steps) -> tuple[list[Step], list[str]]:
    steps, errs = [], []
    if not isinstance(raw_steps, list):
        return [], ["'steps' must be a list"]
    for i, r in enumerate(raw_steps[:25]):
        try:
            steps.append(validate_step(r))
        except (ValueError, TypeError) as e:
            errs.append(f"step {i + 1}: {e}")
    if len(raw_steps) > 25:
        errs.append("more than 25 steps: extra steps ignored")
    return steps, errs


def from_spec_style(d: dict) -> list[dict]:
    """Accept the {fabric_preset, objects[], underlay[], optimize[]} shape from the product spec and convert to steps."""
    out: list[dict] = []
    if d.get("fabric_preset"):
        out.append({"op": "set_fabric", "fabric": d["fabric_preset"]})
    for o in d.get("objects", []) or []:
        t = o.get("target")
        if o.get("stitch_type"):
            s = {"op": "set_stitch_type", "target": t, "stitch_type": o["stitch_type"]}
            if o.get("width_mm") is not None:
                s["width_mm"] = o["width_mm"]
            out.append(s)
        elif o.get("width_mm") is not None:
            out.append({"op": "set_width", "target": t, "width_mm": o["width_mm"]})
    if d.get("underlay"):
        out.append({"op": "set_underlay", "target": "all", "underlay": d["underlay"]})
    opt = [{"travel": "travel", "trim": "fewer_trims", "trims": "fewer_trims"}.get(x, x) for x in (d.get("optimize") or [])]
    if opt:
        out.append({"op": "optimize", "what": opt})
    return out


def system_prompt(roles: list[str]) -> str:
    return ("You convert an embroidery digitizer's request into JSON. Output ONLY a JSON object {\"steps\":[...]}. "
            "Each step: {\"op\":..., \"target\":..., <params>}. Allowed ops and params: "
            + "; ".join(f"{k}({', '.join(sorted(v[0] | v[1])) or '-'})" for k, v in OPS.items())
            + f". stitch_type in {STITCHES}; underlay in {UNDERLAYS}; fabric in {FABRICS}; optimize.what in {OPTIMIZE}. "
            f"Valid targets: {roles + ['all', 'type:satin', 'type:tatami']}. Numbers are millimetres. "
            "Never output code, commands, file paths or stitch coordinates. If the request is unclear return {\"steps\":[]}.")
