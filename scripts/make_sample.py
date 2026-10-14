#!/usr/bin/env python3
"""
Build a synthetic DST with deliberate, known defects, so the analyser has
something concrete to catch. Run after `pip install -e .`:

    python scripts/make_sample.py

Writes samples/hummingbird_sample.dst.
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from embcopilot.dst import write_dst  # noqa: E402
from embcopilot.models import Design, Record, StitchKind  # noqa: E402

random.seed(7)
recs: list[Record] = []
x = y = 0


def move(nx: float, ny: float, kind: StitchKind) -> None:
    global x, y
    nx, ny = int(round(nx)), int(round(ny))
    while True:
        dx = max(-121, min(121, nx - x))
        dy = max(-121, min(121, ny - y))
        x += dx
        y += dy
        recs.append(Record(x, y, dx, dy, kind))
        if x == nx and y == ny:
            break


def satin_column(cx, cy, length, width, angle_deg, density_mm=0.4, tiny=False):
    """Zig-zag column. tiny=True deliberately doubles up penetrations."""
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)
    px, py = -uy, ux
    rows = max(2, int(length / density_mm))
    for i in range(rows):
        t = (i / (rows - 1)) * length - length / 2
        side = (width / 2) if i % 2 == 0 else -(width / 2)
        sx = (cx + ux * t + px * side) * 10
        sy = (cy + uy * t + py * side) * 10
        move(sx, sy, StitchKind.STITCH)
        if tiny and i % 5 == 0:
            move(sx + 1, sy, StitchKind.STITCH)  # 0.1 mm — thread-break bait


def tatami(cx, cy, w, h, angle_deg=45, spacing=0.4, stitch_len=3.5):
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)
    px, py = -uy, ux
    rows = int(h / spacing)
    for i in range(rows):
        off = (i - rows / 2) * spacing
        half = w / 2
        p0 = (cx + px * off - ux * half, cy + py * off - uy * half)
        p1 = (cx + px * off + ux * half, cy + py * off + uy * half)
        a_, b_ = (p0, p1) if i % 2 == 0 else (p1, p0)
        steps = max(1, int(w / stitch_len))
        for s in range(steps + 1):
            t = s / steps
            move(
                (a_[0] + (b_[0] - a_[0]) * t) * 10,
                (a_[1] + (b_[1] - a_[1]) * t) * 10,
                StitchKind.STITCH,
            )


def jump_to(nx, ny):
    move(nx * 10, ny * 10, StitchKind.JUMP)


# ---- colour 1: a fill plus scattered petals, sewn in a deliberately bad order ----
tatami(0, 0, 30, 22, 45)
for cx, cy in [(45, 30), (-40, 28), (48, -25), (-46, -30), (5, 40), (2, -42)]:
    jump_to(cx, cy)
    satin_column(cx, cy, 16, 5, random.choice([0, 30, 60, 90]))

# ---- colour 2: thin stems with tiny stitches ----
recs.append(Record(x, y, 0, 0, StitchKind.COLOR_CHANGE))
for cx, cy in [(20, 20), (-22, 18), (24, -18), (-20, -20)]:
    jump_to(cx, cy)
    satin_column(cx, cy, 24, 2.2, 15, tiny=True)

# ---- colour 3: an over-wide satin (long floats) on top of the fill ----
recs.append(Record(x, y, 0, 0, StitchKind.COLOR_CHANGE))
jump_to(0, 0)
satin_column(0, 0, 20, 14, 0)

recs.append(Record(x, y, 0, 0, StitchKind.END))

d = Design(records=recs, source="hummingbird_sample.dst")
out_dir = os.path.join(os.path.dirname(__file__), "..", "samples")
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "hummingbird_sample.dst")
write_dst(d, out_path, name="HUMMINGBIRD")
print(f"wrote {out_path} ({len(recs)} records)")
