"""
Tajima DST reader/writer. Pure Python, zero third-party dependencies.

DST units are 0.1 mm. Every stitch record is 3 bytes, X and Y each
ternary-encoded across the three bytes using weights 1, 3, 9, 27, 81.
The bit layout below is the widely published Tajima DST table (the same
mapping used by other open embroidery tools); it is validated in
tests/test_dst.py by round-tripping known coordinate deltas.

This module only understands DST. EXP, PES, JEF and EMB are out of scope
for Phase 1 (EMB in particular is Wilcom's undocumented proprietary format
and is not read here at all — see README.md "Limitations").
"""

from __future__ import annotations

from .models import Design, Record, StitchKind

HEADER_SIZE = 512
END_BYTE = 0xF3  # exact end-of-design byte 3 (dx=0, dy=0)


class DSTError(Exception):
    """Base class for anything wrong with a DST file."""


class InvalidDSTError(DSTError):
    """The file does not look like a valid Tajima DST design at all."""


class CorruptDSTError(DSTError):
    """The file has a plausible header but the stitch data is damaged or truncated."""


# ----------------------------------------------------------------------
# Bit-level decode / encode
# ----------------------------------------------------------------------


def _decode_dx(b0: int, b1: int, b2: int) -> int:
    x = 0
    if b0 & 0x01:
        x += 1
    if b0 & 0x02:
        x -= 1
    if b0 & 0x04:
        x += 9
    if b0 & 0x08:
        x -= 9
    if b1 & 0x01:
        x += 3
    if b1 & 0x02:
        x -= 3
    if b1 & 0x04:
        x += 27
    if b1 & 0x08:
        x -= 27
    if b2 & 0x04:
        x += 81
    if b2 & 0x08:
        x -= 81
    return x


def _decode_dy(b0: int, b1: int, b2: int) -> int:
    y = 0
    if b0 & 0x80:
        y += 1
    if b0 & 0x40:
        y -= 1
    if b0 & 0x20:
        y += 9
    if b0 & 0x10:
        y -= 9
    if b1 & 0x80:
        y += 3
    if b1 & 0x40:
        y -= 3
    if b1 & 0x20:
        y += 27
    if b1 & 0x10:
        y -= 27
    if b2 & 0x20:
        y += 81
    if b2 & 0x10:
        y -= 81
    return y


def _encode_axis(value: int, masks: tuple) -> tuple[int, int, int]:
    """Balanced-ternary decomposition of one axis into DST bit masks.

    DST movement is balanced ternary: each of the five weights (1, 3, 9, 27,
    81) contributes -1, 0, or +1 times its weight, and every integer in
    [-121, 121] has exactly one such representation. `masks` gives
    (byte_index, positive_mask, negative_mask) for weights 1, 3, 9, 27, 81
    in that order.

    NOTE: an earlier version of this function used a greedy "largest
    magnitude first" decomposition, which is wrong for balanced ternary —
    e.g. -50 greedily consumes at most 27+9+3+1=40 and can never reach -50,
    because the correct representation actually needs -81 (a magnitude
    LARGER than the remaining value) offset by smaller positive digits
    (+1+3+27-81=-50). This version uses the standard base-3 remainder
    algorithm, which is correct for every value in range.
    """
    if abs(value) > 121:
        raise ValueError(
            f"Movement of {value} units exceeds the +/-121 unit (12.1 mm) "
            "limit for a single DST stitch record."
        )
    out = [0, 0, 0]
    n = value
    for byte_idx, pos_mask, neg_mask in masks:
        r = n % 3  # Python's % is always 0, 1, or 2 here (n can be negative)
        if r == 2:
            r = -1
            n += 1
        if r == 1:
            out[byte_idx] |= pos_mask
        elif r == -1:
            out[byte_idx] |= neg_mask
        n //= 3
    if n != 0:  # pragma: no cover - defensive; the range check above prevents this
        raise ValueError(f"Movement of {value} units could not be exactly encoded.")
    return tuple(out)  # type: ignore[return-value]


# (byte_index, positive_mask, negative_mask) for weights 1, 3, 9, 27, 81, in order.
_X_MASKS = (
    (0, 0x01, 0x02),
    (1, 0x01, 0x02),
    (0, 0x04, 0x08),
    (1, 0x04, 0x08),
    (2, 0x04, 0x08),
)
_Y_MASKS = (
    (0, 0x80, 0x40),
    (1, 0x80, 0x40),
    (0, 0x20, 0x10),
    (1, 0x20, 0x10),
    (2, 0x20, 0x10),
)


def _encode(dx: int, dy: int, kind: StitchKind) -> bytes:
    if kind == StitchKind.END:
        return bytes((0x00, 0x00, END_BYTE))

    xb = _encode_axis(dx, _X_MASKS)
    yb = _encode_axis(dy, _Y_MASKS)
    b0 = xb[0] | yb[0]
    b1 = xb[1] | yb[1]
    b2 = xb[2] | yb[2]

    if kind == StitchKind.STITCH:
        b2 |= 0x03
    elif kind == StitchKind.JUMP:
        b2 |= 0x83
    elif kind == StitchKind.COLOR_CHANGE:
        b2 |= 0xC3
    elif kind == StitchKind.SEQUIN:
        b2 |= 0x43
    else:  # pragma: no cover - defensive
        raise ValueError(f"Cannot encode stitch kind: {kind!r}")

    return bytes((b0, b1, b2))


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------


def read_dst(path: str) -> Design:
    """Read a DST file from disk. Raises InvalidDSTError / CorruptDSTError."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raise InvalidDSTError(f"No such file: {path}") from None
    except PermissionError:
        raise InvalidDSTError(f"Permission denied reading: {path}") from None
    return parse_dst(raw, source=path)


def parse_dst(raw: bytes, source: str = "") -> Design:
    """Parse DST bytes already in memory.

    Raises:
        InvalidDSTError: the file is too short or missing the mandatory
            'LA:' header field — this does not look like a DST at all.
        CorruptDSTError: the header looked fine but the stitch stream is
            truncated (no end-of-design marker found) or contains a byte
            triplet whose control bits are not set (an impossible/garbled
            record).
    """
    if len(raw) < HEADER_SIZE + 3:
        raise InvalidDSTError(
            f"'{source or '<memory>'}' is only {len(raw)} bytes — too short "
            f"to contain a {HEADER_SIZE}-byte Tajima DST header plus even "
            "one stitch record."
        )

    header = _parse_header(raw[:HEADER_SIZE])
    if "LA" not in header:
        raise InvalidDSTError(
            f"'{source or '<memory>'}' has no 'LA:' design-name field in its "
            "header. This does not look like a valid Tajima DST file "
            "(wrong format, or the extension was changed on a different file type)."
        )

    design = Design(header=header, source=source)
    x = y = 0
    i = HEADER_SIZE
    end_seen = False

    while i + 3 <= len(raw):
        b0, b1, b2 = raw[i], raw[i + 1], raw[i + 2]
        offset = i
        i += 3

        if (b2 & 0x03) != 0x03:
            raise CorruptDSTError(
                f"Corrupted stitch record at byte offset {offset}: control "
                f"bits not set (byte3=0x{b2:02X}). The stitch data stream is "
                "damaged from this point on."
            )

        if b2 == END_BYTE:
            design.records.append(Record(x, y, 0, 0, StitchKind.END))
            end_seen = True
            break

        ctrl = b2 & 0xC3
        if ctrl == 0xC3:
            kind = StitchKind.COLOR_CHANGE
        elif ctrl == 0x83:
            kind = StitchKind.JUMP
        elif ctrl == 0x43:
            kind = StitchKind.SEQUIN
        else:
            kind = StitchKind.STITCH

        dx = _decode_dx(b0, b1, b2)
        dy = _decode_dy(b0, b1, b2)
        x += dx
        y += dy
        design.records.append(Record(x, y, dx, dy, kind))

    if not end_seen:
        raise CorruptDSTError(
            f"'{source or '<memory>'}' has no end-of-design marker (0x00 0x00 "
            f"0x{END_BYTE:02X}) anywhere in its stitch data — the file "
            "appears to be truncated."
        )

    return design


def _parse_header(block: bytes) -> dict[str, str]:
    try:
        text = block.split(b"\x1a")[0].decode("latin-1")
    except Exception:
        text = ""
    out: dict[str, str] = {}
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if len(line) > 3 and line[2] == ":":
            out[line[:2]] = line[3:].strip()
    return out


# ----------------------------------------------------------------------
# Writing (used by the sample generator and, later, by any file-emitting phase)
# ----------------------------------------------------------------------


def write_dst(design: Design, path: str, name: str = "DESIGN") -> None:
    """Write a Design back out as a DST file. Never used on the analyser's input path."""
    body = bytearray()
    for r in design.records:
        if r.kind == StitchKind.END:
            continue
        body += _encode(r.dx, r.dy, r.kind)
    body += _encode(0, 0, StitchKind.END)

    x0, y0, x1, y1 = design.bounds_mm()
    stitches = sum(1 for r in design.records if r.kind == StitchKind.STITCH)
    colors = sum(1 for r in design.records if r.kind == StitchKind.COLOR_CHANGE) + 1

    fields = [
        f"LA:{name[:16]:<16}",
        f"ST:{stitches:7d}",
        f"CO:{colors:3d}",
        f"+X:{int(x1 * 10):5d}",
        f"-X:{int(abs(x0) * 10):5d}",
        f"+Y:{int(y1 * 10):5d}",
        f"-Y:{int(abs(y0) * 10):5d}",
        "AX:+    0",
        "AY:+    0",
        "MX:+    0",
        "MY:+    0",
        "PD:******",
    ]
    head = "\r".join(fields) + "\r"
    header = head.encode("latin-1")
    header += b"\x1a"
    if len(header) > HEADER_SIZE:
        raise ValueError("DST header fields exceed the 512-byte header block.")
    header += b" " * (HEADER_SIZE - len(header))

    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(body)
