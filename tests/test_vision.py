"""Tests for embcopilot.vision — LocalVisionProvider on synthetic images."""

import os
import tempfile

import pytest

from embcopilot.vision import CloudVisionProvider, LocalVisionProvider, NotImplementedCapability


def _make_test_image(path: str) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (200, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, 90, 90], fill=(200, 30, 30))
    d.rectangle([110, 110, 180, 180], fill=(30, 120, 200))
    img.save(path)


def test_local_provider_detects_distinct_regions():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "shapes.png")
        _make_test_image(path)
        bp = LocalVisionProvider().analyze(path, target_size_mm=(50.0, 50.0))

    assert bp.image_size_px == (200, 200)
    assert len(bp.objects) >= 2  # at least the circle and the square
    # every object should carry a confidence with a documented reason
    for obj in bp.objects:
        assert "detection" in obj.confidence
        assert obj.confidence["detection"].value > 0
        assert "proxy" in obj.confidence["detection"].reason


def test_local_provider_never_assigns_semantic_category():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "shapes.png")
        _make_test_image(path)
        bp = LocalVisionProvider().analyze(path, target_size_mm=(50.0, 50.0))

    from embcopilot.domain import ObjectCategory

    for obj in bp.objects:
        assert obj.category == ObjectCategory.UNKNOWN.value


def test_px_to_mm_scaling_is_not_1_to_1():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "shapes.png")
        _make_test_image(path)
        bp = LocalVisionProvider().analyze(path, target_size_mm=(50.0, 50.0))

    # image is 200px wide targeting 50mm -> scale factor 0.25, never 1:1
    x_mm, _ = bp.px_to_mm(200.0, 0.0)
    assert abs(x_mm - 50.0) < 1e-6
    assert x_mm != 200.0


def test_cloud_provider_raises_not_implemented_rather_than_faking():
    with pytest.raises(NotImplementedCapability):
        CloudVisionProvider().analyze("whatever.png", (10.0, 10.0))


def test_local_provider_raises_on_missing_file():
    with pytest.raises(ValueError):
        LocalVisionProvider().analyze("/no/such/image.png", (10.0, 10.0))
