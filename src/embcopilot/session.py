"""
CopilotSession + project file persistence.

Project files are plain JSON (extension `.ecp.json` by default) — never a
proprietary binary, and never an embedded copy of a `.emb` file's bytes
(section 48: "Do NOT embed proprietary EMB binary content ... instead
store a reference/path/hash"). If a native design file is part of the
session, only its path and a SHA-256 hash are stored, so the project file
can later verify whether that file has changed without ever copying it.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .automation.wilcom_adapter import WilcomScreenState
from .command import StructuredCommand
from .domain import ApprovalState, DesignProject, new_id
from .technical import TechnicalBlueprint
from .vision import ObjectBlueprint

PROJECT_FILE_SUFFIX = ".ecp.json"


def _hash_file(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class NativeFileReference:
    """A reference to a native design file (e.g. a .emb) — path + hash only,
    never the file's own bytes. See module docstring."""

    path: str
    sha256: str | None = None

    @classmethod
    def from_path(cls, path: str) -> "NativeFileReference":
        return cls(path=path, sha256=_hash_file(path))


@dataclass
class CopilotSession:
    session_id: str = field(default_factory=lambda: new_id("session"))
    project: DesignProject | None = None
    native_file: NativeFileReference | None = None
    object_blueprint: ObjectBlueprint | None = None
    technical_blueprint: TechnicalBlueprint | None = None
    screen_state: WilcomScreenState | None = None
    selected_object_id: str | None = None
    pending_command: StructuredCommand | None = None
    approval_state: str = ApprovalState.PROPOSED.value
    qc_state: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "project": self.project.to_dict() if self.project else None,
            "native_file": asdict(self.native_file) if self.native_file else None,
            "object_blueprint": self.object_blueprint.to_dict() if self.object_blueprint else None,
            "technical_blueprint": self.technical_blueprint.to_dict() if self.technical_blueprint else None,
            "screen_state": asdict(self.screen_state) if self.screen_state else None,
            "selected_object_id": self.selected_object_id,
            "pending_command": asdict(self.pending_command) if self.pending_command else None,
            "approval_state": self.approval_state,
            "qc_state": self.qc_state,
            "created_at": self.created_at,
        }

    def save(self, path: str) -> str:
        """Write this session as a project file. Does not touch any native
        design file — only records its path/hash (see NativeFileReference)."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        return path

    @staticmethod
    def default_project_path(name: str, directory: str = "projects") -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "project"
        return os.path.join(directory, f"{safe}{PROJECT_FILE_SUFFIX}")

    @classmethod
    def load_raw(cls, path: str) -> dict:
        """Load a project file's raw dict. Reconstructing full dataclass
        trees (ObjectBlueprint, TechnicalBlueprint, etc) from JSON is left
        to callers that need it, since most tooling only needs specific
        fields (e.g. QC state or the native file reference) rather than a
        full round-trip object graph."""
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def verify_native_file_unchanged(self) -> bool | None:
        """None means "no native file recorded", not "unchanged"."""
        if self.native_file is None:
            return None
        current = _hash_file(self.native_file.path)
        return current == self.native_file.sha256
