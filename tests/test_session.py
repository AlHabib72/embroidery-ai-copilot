"""Tests for embcopilot.session — CopilotSession + project file persistence."""

import json
import os
import tempfile

from embcopilot.domain import DesignProject, EmbroideryObject
from embcopilot.session import CopilotSession, NativeFileReference


def test_session_save_writes_valid_json():
    session = CopilotSession(project=DesignProject(project_id="p1", name="Test"))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.ecp.json")
        session.save(path)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    assert data["project"]["project_id"] == "p1"


def test_session_save_creates_missing_directories():
    session = CopilotSession()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nested", "sub", "test.ecp.json")
        session.save(path)
        assert os.path.exists(path)


def test_native_file_reference_stores_hash_not_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        native_path = os.path.join(tmp, "design.emb")
        with open(native_path, "wb") as fh:
            fh.write(b"proprietary binary content")
        ref = NativeFileReference.from_path(native_path)
    assert ref.path == native_path
    assert ref.sha256 is not None
    assert len(ref.sha256) == 64  # sha256 hex digest length
    assert not hasattr(ref, "content")  # never stores the bytes themselves


def test_session_to_dict_never_embeds_native_file_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        native_path = os.path.join(tmp, "design.emb")
        with open(native_path, "wb") as fh:
            fh.write(b"proprietary binary content")
        session = CopilotSession(native_file=NativeFileReference.from_path(native_path))
        d = session.to_dict()
    serialized = json.dumps(d)
    assert "proprietary binary content" not in serialized
    assert d["native_file"]["sha256"] is not None


def test_verify_native_file_unchanged_detects_modification():
    with tempfile.TemporaryDirectory() as tmp:
        native_path = os.path.join(tmp, "design.emb")
        with open(native_path, "wb") as fh:
            fh.write(b"version 1")
        session = CopilotSession(native_file=NativeFileReference.from_path(native_path))
        assert session.verify_native_file_unchanged() is True

        with open(native_path, "wb") as fh:
            fh.write(b"version 2 -- modified")
        assert session.verify_native_file_unchanged() is False


def test_verify_native_file_unchanged_none_when_no_file_recorded():
    session = CopilotSession()
    assert session.verify_native_file_unchanged() is None


def test_default_project_path_sanitises_name():
    path = CopilotSession.default_project_path("My Design #1!", directory="projects")
    assert path.startswith("projects" + os.sep) or path.startswith("projects/")
    assert " " not in path
    assert "#" not in path
    assert "!" not in path
