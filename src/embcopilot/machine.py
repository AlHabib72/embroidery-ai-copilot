"""Machine + hoop profiles and the production-aware trim policy (v0.6).

Presets are GENERIC engineering defaults, not measurements of a specific machine.  Override per machine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class HoopProfile:
    name: str
    hoop_width_mm: float
    hoop_height_mm: float
    safe_margin_mm: float = 5.0

    def check(self, min_x, min_y, max_x, max_y) -> dict:
        w, h = max_x - min_x, max_y - min_y
        aw, ah = self.hoop_width_mm - 2 * self.safe_margin_mm, self.hoop_height_mm - 2 * self.safe_margin_mm
        return {"fits": w <= aw and h <= ah, "design_w_mm": round(w, 2), "design_h_mm": round(h, 2),
                "usable_w_mm": aw, "usable_h_mm": ah}

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class MachineProfile:
    machine_name: str
    format: str = "dst"
    max_width_mm: float = 200.0
    max_height_mm: float = 200.0
    max_stitch_mm: float = 12.1          # DST record limit
    min_safe_stitch_mm: float = 0.3
    trim_capability: bool = True
    color_change_capability: bool = True
    hidden_run_max_mm: float = 3.0       # max travel that may be run as hidden stitches
    visible_trim_min_mm: float = 2.0     # visible travel longer than this is trimmed
    max_jump_mm: float = 60.0
    hoop: HoopProfile | None = None

    def to_dict(self):
        d = asdict(self)
        return d


MACHINES: dict[str, MachineProfile] = {
    "generic_flat": MachineProfile("generic_flat", hoop=HoopProfile("generic 200x200", 200, 200)),
    "generic_cap": MachineProfile("generic_cap", max_width_mm=120, max_height_mm=55, hidden_run_max_mm=2.5,
                                  hoop=HoopProfile("cap frame", 120, 55, 3.0)),
    "no_trim_machine": MachineProfile("no_trim_machine", trim_capability=False, visible_trim_min_mm=1e9,
                                      hoop=HoopProfile("generic 200x200", 200, 200)),
    "small_hoop_100": MachineProfile("small_hoop_100", max_width_mm=100, max_height_mm=100,
                                     hoop=HoopProfile("100x100", 100, 100)),
}


def get_machine(name: str | MachineProfile | None) -> MachineProfile:
    if isinstance(name, MachineProfile):
        return name
    key = (name or "generic_flat").lower()
    if key not in MACHINES:
        raise ValueError(f"unknown machine profile {name!r}; known: {sorted(MACHINES)}")
    return MACHINES[key]


@dataclass
class TravelContext:
    distance_mm: float
    same_object: bool
    hidden: bool                 # will be covered by later stitches / sits inside the object
    crosses_protected: bool = False
    fabric_stretch: float = 0.0  # 0..1
    thread_visibility: float = 0.5  # 0..1 contrast of thread vs fabric (user supplied; default neutral)


def decide_travel(ctx: TravelContext, m: MachineProfile) -> dict:
    """Not a single 'distance > 2 mm -> trim' rule: protected zones, visibility, object relationship,
    fabric and machine capability all contribute.  Returns {"action", "reason"}."""
    if ctx.crosses_protected:
        return {"action": "route_around", "reason": "path crosses a protected zone; reroute instead of trimming or jumping through it"}
    d = ctx.distance_mm
    hidden_cap = m.hidden_run_max_mm * (1.0 if ctx.same_object else 0.7)
    if ctx.hidden and d <= hidden_cap:
        return {"action": "run_connector", "reason": f"hidden travel {d:.1f} mm <= {hidden_cap:.1f} mm; will be covered, no trim needed"}
    # visible travel: threshold shrinks for stretch fabrics / high-contrast thread (loose jumps show and snag)
    thr = m.visible_trim_min_mm * (1.0 - 0.3 * ctx.fabric_stretch) * (1.0 - 0.3 * ctx.thread_visibility)
    if d <= thr:
        return {"action": "jump", "reason": f"short visible travel {d:.1f} mm <= {thr:.1f} mm: jump without trim"}
    if not m.trim_capability:
        return {"action": "jump", "reason": "machine has no trim capability; jump (operator must trim)", "warning": "NO_TRIM_CAPABILITY"}
    return {"action": "trim_jump", "reason": f"visible travel {d:.1f} mm > {thr:.1f} mm: trim + jump"}
