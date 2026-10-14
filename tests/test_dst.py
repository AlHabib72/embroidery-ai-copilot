"""Tests for embcopilot.dst — bit-level decode/encode and file validation."""

import os
import tempfile

import pytest

from embcopilot.dst import (
    CorruptDSTError,
    InvalidDSTError,
    _decode_dx,
    _decode_dy,
    _encode,
    parse_dst,
    read_dst,
    write_dst,
)
from embcopilot.models import Design, Record, StitchKind


def _record_bytes(dx, dy, kind=StitchKind.STITCH):
    return _encode(dx, dy, kind)


# ----------------------------------------------------------------------
# Bit-table round trip: every documented magnitude, both signs, both axes
# ----------------------------------------------------------------------


@pytest.mark.parametrize("mag", [1, 3, 9, 27, 81])
@pytest.mark.parametrize("sign", [1, -1])
def test_x_axis_round_trip(mag, sign):
    dx = mag * sign
    b0, b1, b2 = _record_bytes(dx, 0)
    assert _decode_dx(b0, b1, b2) == dx
    assert _decode_dy(b0, b1, b2) == 0


@pytest.mark.parametrize("mag", [1, 3, 9, 27, 81])
@pytest.mark.parametrize("sign", [1, -1])
def test_y_axis_round_trip(mag, sign):
    dy = mag * sign
    b0, b1, b2 = _record_bytes(0, dy)
    assert _decode_dy(b0, b1, b2) == dy
    assert _decode_dx(b0, b1, b2) == 0


@pytest.mark.parametrize("dx,dy", [(1, 1), (-1, -1), (12, -12), (-121, 121), (121, -121), (0, 0)])
def test_combined_movement_round_trip(dx, dy):
    b0, b1, b2 = _record_bytes(dx, dy)
    assert _decode_dx(b0, b1, b2) == dx
    assert _decode_dy(b0, b1, b2) == dy


def test_encode_rejects_out_of_range():
    with pytest.raises(ValueError):
        _encode(122, 0, StitchKind.STITCH)
    with pytest.raises(ValueError):
        _encode(0, -200, StitchKind.STITCH)


def test_control_bits_for_each_kind():
    assert (_record_bytes(0, 0, StitchKind.STITCH)[2] & 0xC3) == 0x03
    assert (_record_bytes(0, 0, StitchKind.JUMP)[2] & 0xC3) == 0x83
    assert (_record_bytes(0, 0, StitchKind.COLOR_CHANGE)[2] & 0xC3) == 0xC3
    assert _encode(0, 0, StitchKind.END) == bytes((0x00, 0x00, 0xF3))


# ----------------------------------------------------------------------
# Full file round trip via write_dst / read_dst
# ----------------------------------------------------------------------


def _sample_design() -> Design:
    recs = [
        Record(0, 0, 0, 0, StitchKind.STITCH),
        Record(10, 0, 10, 0, StitchKind.STITCH),
        Record(10, 10, 0, 10, StitchKind.STITCH),
        Record(20, 20, 10, 10, StitchKind.JUMP),
        Record(20, 20, 0, 0, StitchKind.COLOR_CHANGE),
        Record(30, 20, 10, 0, StitchKind.STITCH),
        Record(30, 20, 0, 0, StitchKind.END),
    ]
    return Design(records=recs, source="mem")


def test_write_then_read_round_trip():
    d = _sample_design()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.dst")
        write_dst(d, path, name="TESTDESIGN")
        back = read_dst(path)

    assert back.header["LA"].strip() == "TESTDESIGN"
    kinds = [r.kind for r in back.records]
    assert kinds == [
        StitchKind.STITCH,
        StitchKind.STITCH,
        StitchKind.STITCH,
        StitchKind.JUMP,
        StitchKind.COLOR_CHANGE,
        StitchKind.STITCH,
        StitchKind.END,
    ]
    # Absolute positions must survive the round trip exactly.
    assert (back.records[2].x, back.records[2].y) == (10, 10)
    assert (back.records[5].x, back.records[5].y) == (30, 20)


def test_negative_movement_round_trip():
    recs = [
        Record(0, 0, 0, 0, StitchKind.STITCH),
        Record(-50, -50, -50, -50, StitchKind.STITCH),
        Record(-50, -50, 0, 0, StitchKind.END),
    ]
    d = Design(records=recs)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.dst")
        write_dst(d, path)
        back = read_dst(path)
    assert (back.records[1].x, back.records[1].y) == (-50, -50)


def test_multiple_colors_counted_in_header():
    recs = [Record(0, 0, 0, 0, StitchKind.STITCH)]
    for _ in range(3):
        recs.append(Record(0, 0, 0, 0, StitchKind.COLOR_CHANGE))
        recs.append(Record(1, 0, 1, 0, StitchKind.STITCH))
    recs.append(Record(1, 0, 0, 0, StitchKind.END))
    d = Design(records=recs)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.dst")
        write_dst(d, path)
        back = read_dst(path)
    assert back.header["CO"].strip() == "4"  # 3 changes -> 4 colour blocks


# ----------------------------------------------------------------------
# Error handling: malformed / truncated / corrupt input never crashes
# with a raw traceback and always raises a documented exception.
# ----------------------------------------------------------------------


def test_too_short_file_is_invalid():
    with pytest.raises(InvalidDSTError):
        parse_dst(b"not a dst file")


def test_missing_la_field_is_invalid():
    junk_header = (b"ST:    100\rCO:  1\r" + b" " * 500)[:512]
    with pytest.raises(InvalidDSTError):
        parse_dst(junk_header + bytes((0x00, 0x00, 0xF3)))


def test_truncated_stitch_stream_is_corrupt():
    d = _sample_design()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.dst")
        write_dst(d, path)
        with open(path, "rb") as fh:
            raw = fh.read()
    # Cut off before the END marker.
    truncated = raw[:-3]
    with pytest.raises(CorruptDSTError):
        parse_dst(truncated, source="truncated.dst")


def test_garbled_control_byte_is_corrupt():
    d = _sample_design()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.dst")
        write_dst(d, path)
        with open(path, "rb") as fh:
            raw = fh.read()
    # Header is 512 bytes; corrupt the control bits of the first stitch record.
    raw = bytearray(raw)
    raw[514] = 0x00  # clears the mandatory 0x03 control bits
    with pytest.raises(CorruptDSTError):
        parse_dst(bytes(raw), source="garbled.dst")


def test_lenient_end_bitmask_is_not_silently_accepted():
    # 0xFF would satisfy the old, overly-lenient "(b2 & 0xF3) == 0xF3" check
    # used to detect end-of-design, but it is not the real end-of-design
    # byte (0x00 0x00 0xF3). A file whose terminator was corrupted to 0xFF
    # must be reported as truncated/corrupt, not silently accepted as a
    # normal end-of-design.
    d = _sample_design()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.dst")
        write_dst(d, path)
        with open(path, "rb") as fh:
            raw = fh.read()
    raw = bytearray(raw)
    raw[-1] = 0xFF  # corrupt the terminator's third byte
    with pytest.raises(CorruptDSTError):
        parse_dst(bytes(raw), source="corrupted_end.dst")


def test_read_dst_missing_file():
    with pytest.raises(InvalidDSTError):
        read_dst("/no/such/path/design.dst")
