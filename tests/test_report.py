"""Tests for embcopilot.report — HTML and JSON output."""

import json
import os
import tempfile

from embcopilot.analyze import analyze
from embcopilot.config import AnalyzerConfig
from embcopilot.models import Design, Record, StitchKind
from embcopilot.report import render_html, render_json


def _small_design() -> Design:
    recs = [
        Record(0, 0, 0, 0, StitchKind.STITCH),
        Record(100, 0, 100, 0, StitchKind.STITCH),
        Record(100, 100, 0, 100, StitchKind.STITCH),
        Record(150, 150, 50, 50, StitchKind.JUMP),
        Record(150, 150, 0, 0, StitchKind.COLOR_CHANGE),
        Record(160, 150, 10, 0, StitchKind.STITCH),
        Record(160, 150, 0, 0, StitchKind.END),
    ]
    return Design(records=recs, header={"LA": "SAMPLE"}, source="sample.dst")


def test_render_html_produces_expected_sections():
    d = _small_design()
    a = analyze(d, AnalyzerConfig())
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "out.html")
        render_html(a, d, path)
        assert os.path.exists(path)
        html = open(path, encoding="utf-8").read()

    assert "SAMPLE" in html
    assert "QC Heuristic Score" in html
    assert "Reconstructed segments" in html
    assert "not a certification of" in html  # disclaimer present
    assert "<svg" in html  # stitch preview rendered


def test_render_html_never_modifies_source_design_object():
    d = _small_design()
    original_record_count = len(d.records)
    a = analyze(d, AnalyzerConfig())
    with tempfile.TemporaryDirectory() as tmp:
        render_html(a, d, os.path.join(tmp, "out.html"))
    assert len(d.records) == original_record_count


def test_render_json_round_trips_key_fields():
    d = _small_design()
    a = analyze(d, AnalyzerConfig())
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "out.json")
        render_json(a, path)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

    assert data["name"] == "SAMPLE"
    assert data["stitches"] == a.stitches
    assert data["score"] == a.score
    assert "issues" in data
    assert "segments" in data
    assert data["analyzer_version"]


def test_report_handles_zero_stitch_design_without_crashing():
    d = Design(records=[Record(0, 0, 0, 0, StitchKind.END)], header={"LA": "EMPTY"})
    a = analyze(d, AnalyzerConfig())
    with tempfile.TemporaryDirectory() as tmp:
        html_path = os.path.join(tmp, "out.html")
        json_path = os.path.join(tmp, "out.json")
        render_html(a, d, html_path)
        render_json(a, json_path)
        assert os.path.getsize(html_path) > 0
        assert os.path.getsize(json_path) > 0


def test_render_html_creates_output_directory_if_missing():
    d = _small_design()
    a = analyze(d, AnalyzerConfig())
    with tempfile.TemporaryDirectory() as tmp:
        nested = os.path.join(tmp, "reports", "sub")
        path = os.path.join(nested, "out.html")
        render_html(a, d, path)
        assert os.path.exists(path)
