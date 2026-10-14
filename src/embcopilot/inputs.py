"""Input sniffing (spec 6): decide the file kind from CONTENT, not the extension, and detect corruption."""

from __future__ import annotations

import os
from dataclasses import dataclass

MAX_BYTES_DEFAULT = 50 * 1024 * 1024
ACCEPTED = {"png", "jpg", "webp", "svg", "ai", "dst", "exp", "pes", "jef", "emb"}


@dataclass
class Sniff:
    kind: str  # png jpg webp svg ai pdf dst exp pes jef emb unknown
    ok: bool
    problem: str = ""
    extension_matches: bool = True

    def to_dict(self):
        return self.__dict__.copy()


def sniff(path: str, max_bytes: int = MAX_BYTES_DEFAULT) -> Sniff:
    if not os.path.isfile(path):
        return Sniff("unknown", False, "not a file")
    size = os.path.getsize(path)
    if size == 0:
        return Sniff("unknown", False, "empty file")
    if size > max_bytes:
        return Sniff("unknown", False, f"file larger than {max_bytes // (1024*1024)} MB")
    head = open(path, "rb").read(4096)
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    ext = {"jpeg": "jpg"}.get(ext, ext)
    kind, ok, prob = "unknown", True, ""
    if head.startswith(b"\x89PNG\r\n\x1a\n"): kind = "png"
    elif head.startswith(b"\xff\xd8\xff"): kind = "jpg"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP": kind = "webp"
    elif head.startswith(b"%PDF"): kind = "ai" if ext == "ai" else "pdf"
    elif head.lstrip().startswith((b"<?xml", b"<svg")) and b"<svg" in head + open(path, "rb").read(65536):
        kind = "svg"
    elif head.startswith(b"#PES"): kind = "pes"
    elif head.startswith(b"LA:"): kind = "dst"
    elif head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": kind = "emb"  # OLE2 container; extension must agree
    elif ext in ("exp", "jef"): kind = ext  # no magic bytes: trust extension, flagged below
    if kind == "emb" and ext != "emb":
        kind = "unknown"
    if kind in ("png", "jpg", "webp"):
        import cv2, numpy as np
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            ok, prob = False, f"{kind.upper()} header present but the image cannot be decoded (corrupt or truncated)"
    if kind == "svg":
        import xml.etree.ElementTree as ET
        try:
            ET.parse(path)
        except ET.ParseError as e:
            ok, prob = False, f"SVG is not well-formed XML: {e}"
    if kind == "dst":
        try:
            from .dst import read_dst
            read_dst(path)
        except Exception as e:  # noqa: BLE001
            ok, prob = False, f"DST cannot be parsed: {e}"
    if kind == "unknown":
        ok, prob = False, "unrecognised file content"
    return Sniff(kind, ok, prob, extension_matches=(ext == kind or (kind == "ai" and ext in ("ai", "pdf")) or (kind == "pdf" and ext == "pdf")))
