"""
Thread palette engine.

LICENSING: real brand shade cards (Madeira, Isacord, Gunold ...) are
proprietary. This project ships NO brand data. It ships one clearly labelled
generic palette ("Generic-24", project-defined colours, codes are G01..G24 and
mean nothing to any brand) so the engine works out of the box, and it LOADS
palettes you are licensed to use from CSV (columns: code,name,hex[,material]).

Matching: sRGB -> CIE Lab (D65) -> CIEDE2000. Delta-E is reported honestly:
it measures colour difference on a screen-referred sRGB value, NOT the
appearance of sewn thread (sheen, fabric, lighting). Nothing is called exact.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"bad hex colour {h!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(round(v)) for v in rgb)


def rgb_to_lab(rgb) -> tuple[float, float, float]:
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(float(v)) for v in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.0
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116  # noqa: E731
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e2000(l1, l2) -> float:
    """CIEDE2000 (Sharma, Wu, Dalal 2005)."""
    L1, a1, b1 = l1; L2, a2, b2 = l2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb**7 / (Cb**7 + 25**7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if C1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if C2p else 0.0
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        dhp -= 360 if dhp > 180 else 0
        dhp += 360 if dhp < -180 else 0
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dth = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp**7 / (Cbp**7 + 25**7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dth)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def describe_delta_e(de: float) -> str:
    """Conventional rule-of-thumb wording; not a promise about sewn thread."""
    if de < 1: return "not perceptible"
    if de < 2: return "perceptible on close inspection"
    if de < 5: return "close"
    if de < 10: return "noticeable difference"
    return "different colour"


@dataclass(frozen=True)
class ThreadColor:
    brand: str
    code: str
    name: str
    rgb: tuple[int, int, int]
    material: str = ""
    metadata: tuple = ()

    @property
    def lab(self):
        return rgb_to_lab(self.rgb)

    @property
    def hex(self) -> str:
        return rgb_to_hex(self.rgb)


_GENERIC = [  # project-defined generic colours (NOT any brand's shades)
    ("G01", "Black", "#000000"), ("G02", "White", "#FFFFFF"), ("G03", "Light Grey", "#BFBFBF"), ("G04", "Dark Grey", "#4D4D4D"),
    ("G05", "Red", "#C8102E"), ("G06", "Dark Red", "#7A0019"), ("G07", "Orange", "#F58220"), ("G08", "Yellow", "#FFD100"),
    ("G09", "Light Green", "#7BC043"), ("G10", "Green", "#2E8B3D"), ("G11", "Dark Green", "#1B5E20"), ("G12", "Teal", "#008C8C"),
    ("G13", "Sky Blue", "#5DADE2"), ("G14", "Blue", "#1F5FBF"), ("G15", "Navy", "#0B2545"), ("G16", "Purple", "#6A2C91"),
    ("G17", "Pink", "#F48FB1"), ("G18", "Magenta", "#C2185B"), ("G19", "Brown", "#6D4C41"), ("G20", "Tan", "#C9A66B"),
    ("G21", "Cream", "#F5E9C8"), ("G22", "Gold", "#C9A227"), ("G23", "Silver", "#A8A9AD"), ("G24", "Olive", "#6B7A1F"),
]


class ThreadDatabase:
    def __init__(self):
        self.threads: list[ThreadColor] = []

    @classmethod
    def generic(cls) -> "ThreadDatabase":
        db = cls()
        for code, name, hx in _GENERIC:
            db.threads.append(ThreadColor("Generic-24", code, name, hex_to_rgb(hx), "unspecified", (("source", "project-defined, not a brand"),)))
        return db

    def load_csv(self, path: str, brand: str, material: str = "") -> int:
        n = 0
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                self.threads.append(ThreadColor(brand, r["code"], r.get("name", ""), hex_to_rgb(r["hex"]), r.get("material", material)))
                n += 1
        return n

    def add(self, t: ThreadColor) -> None:
        self.threads.append(t)

    @property
    def brands(self) -> list[str]:
        return sorted({t.brand for t in self.threads})

    def candidates(self, rgb, brand: str | None = None, k: int = 3):
        lab = rgb_to_lab(rgb)
        pool = [t for t in self.threads if brand is None or t.brand == brand]
        if not pool:
            raise ValueError(f"no threads for brand {brand!r} (loaded brands: {self.brands})")
        scored = sorted(((delta_e2000(lab, t.lab), t) for t in pool), key=lambda x: x[0])
        return [(round(d, 2), t) for d, t in scored[:k]]


@dataclass
class ColorwayEntry:
    design_color: str  # "rgb(r,g,b)" as used by objects
    thread: ThreadColor
    delta_e: float
    note: str = ""

    def to_dict(self):
        return {"design_color": self.design_color, "brand": self.thread.brand, "code": self.thread.code, "name": self.thread.name,
                "hex": self.thread.hex, "delta_e2000": self.delta_e, "quality": describe_delta_e(self.delta_e), "note": self.note}


@dataclass
class Colorway:
    name: str
    brand: str
    mode: str
    entries: list[ColorwayEntry] = field(default_factory=list)

    @property
    def thread_count(self) -> int:
        return len({(e.thread.brand, e.thread.code) for e in self.entries})

    def to_dict(self):
        return {"name": self.name, "brand": self.brand, "mode": self.mode, "thread_count": self.thread_count,
                "entries": [e.to_dict() for e in self.entries],
                "disclaimer": "Delta-E is a screen-referred sRGB comparison, not a prediction of sewn appearance."}


def _parse_group(cg: str):
    a = cg[4:-1].split(",")
    return tuple(int(v) for v in a)


def build_colorway(design_colors: list[str], db: ThreadDatabase, brand: str | None, mode: str = "nearest",
                   name: str = "Colorway 1", manual: dict[str, str] | None = None, merge_delta_e: float = 6.0) -> Colorway:
    """mode: nearest | min_threads | manual.
    min_threads reuses one thread for design colours whose best matches are within `merge_delta_e` of each other
    (fewer colour changes / cones, at some colour error - the error is reported per entry)."""
    manual = manual or {}
    cw = Colorway(name, brand or "any", mode)
    chosen: list[ThreadColor] = []
    for dc in design_colors:
        rgb = _parse_group(dc)
        if dc in manual:
            code = manual[dc]
            t = next((t for t in db.threads if t.code == code and (brand is None or t.brand == brand)), None)
            if t is None:
                raise ValueError(f"manual thread {code!r} not in database")
            cw.entries.append(ColorwayEntry(dc, t, round(delta_e2000(rgb_to_lab(rgb), t.lab), 2), "manual choice"))
            chosen.append(t)
            continue
        cands = db.candidates(rgb, brand, k=3)
        de, t = cands[0]
        note = ""
        if mode == "min_threads":
            for d2, t2 in cands:
                if t2 in chosen and d2 <= de + merge_delta_e:
                    de, t, note = d2, t2, "reused a thread already in the colourway to reduce thread count"
                    break
        chosen.append(t)
        cw.entries.append(ColorwayEntry(dc, t, de, note))
    return cw
