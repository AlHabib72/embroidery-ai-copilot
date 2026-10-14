"""Tests for embcopilot.cli — subcommand dispatch and backward compatibility."""

import contextlib
import io
import os
import tempfile

from embcopilot.cli import main
from embcopilot.dst import write_dst
from embcopilot.models import Design, Record, StitchKind


def _make_sample_dst(path: str) -> None:
    recs = [
        Record(0, 0, 0, 0, StitchKind.STITCH),
        Record(100, 0, 100, 0, StitchKind.STITCH),
        Record(100, 0, 0, 0, StitchKind.END),
    ]
    write_dst(Design(records=recs), path, name="TESTDESIGN")


def _make_test_image(path: str) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (100, 100), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([10, 10, 60, 60], fill=(200, 30, 30))
    img.save(path)


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


# ----------------------------------------------------------------------
# Backward compatibility
# ----------------------------------------------------------------------


def test_bare_file_argument_works_without_a_subcommand():
    with tempfile.TemporaryDirectory() as tmp:
        dst_path = os.path.join(tmp, "d.dst")
        _make_sample_dst(dst_path)
        code, out = _run([dst_path, "--quiet", "--output", tmp])
        assert code == 0
        assert os.path.exists(os.path.join(tmp, "d_report.html"))


def test_analyze_subcommand_matches_bare_form():
    with tempfile.TemporaryDirectory() as tmp:
        dst_path = os.path.join(tmp, "d.dst")
        _make_sample_dst(dst_path)
        code, out = _run(["analyze", dst_path, "--quiet", "--output", tmp])
    assert code == 0


def test_qc_is_an_alias_for_analyze():
    with tempfile.TemporaryDirectory() as tmp:
        dst_path = os.path.join(tmp, "d.dst")
        _make_sample_dst(dst_path)
        code, out = _run(["qc", dst_path, "--quiet", "--output", tmp])
    assert code == 0


def test_list_fabrics_still_works_at_top_level():
    code, out = _run(["--list-fabrics"])
    assert code == 0
    assert "woven" in out
    assert "cap" in out


def test_missing_dst_file_is_a_clean_error_not_a_crash():
    code, out = _run(["no_such.dst"])
    assert code != 0


# ----------------------------------------------------------------------
# Phase 2 subcommands
# ----------------------------------------------------------------------


def test_blueprint_subcommand_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, "ref.png")
        _make_test_image(img_path)
        out_json = os.path.join(tmp, "bp.json")
        code, out = _run(
            ["blueprint", img_path, "--target-width-mm", "40", "--target-height-mm", "40", "--output", out_json]
        )
        assert code == 0
        assert os.path.exists(out_json)
        assert "region(s) detected" in out
        assert "category=unknown" in out


def test_technical_plan_subcommand_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, "ref.png")
        _make_test_image(img_path)
        out_json = os.path.join(tmp, "tp.json")
        code, out = _run(
            ["technical-plan", img_path, "--target-width-mm", "40", "--target-height-mm", "40",
             "--fabric", "cap", "--output", out_json]
        )
        assert code == 0
        assert os.path.exists(out_json)
        assert "Technical blueprint" in out


def test_simulate_without_image_refuses_instead_of_using_placeholder_geometry():
    code, out = _run(["simulate", "--dry-run"])
    assert code != 0
    assert "EXECUTING" not in out


def test_simulate_dry_run_uses_real_object_geometry_and_executes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, "ref.png")
        _make_test_image(img_path)
        code, out = _run(["simulate", img_path, "--target-width-mm", "40", "--target-height-mm", "40", "--dry-run"])
    assert code == 0
    assert "DRY RUN" in out and "EXECUTING" not in out
    assert "(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)" not in out  # the old dummy triangle
    assert "create_geometry" in out and "points_mm" in out


def test_simulate_executes_real_geometry_on_mock_adapter():
    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, "ref.png")
        _make_test_image(img_path)
        code, out = _run(["simulate", img_path, "--target-width-mm", "40", "--target-height-mm", "40"])
    assert code == 0
    assert "action(s) logged" in out and "MockWilcomAdapter" in out


def test_blueprint_subcommand_missing_file_is_clean_error():
    code, out = _run(["blueprint", "no_such.png"])
    assert code != 0


def test_unknown_bare_flag_falls_through_to_help():
    # No subcommand, no file -> help/usage, non-zero-ish but no crash
    code, out = _run([])
    assert isinstance(code, int)
