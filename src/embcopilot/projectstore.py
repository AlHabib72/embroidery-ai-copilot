"""Directory-based project format ``*.eacproject`` (v0.6) with version history, undo/redo and resume.

NOT a machine format.  Layout::

    my.eacproject/
      project.json            name, created, head, pointer, approval state, settings
      reference/original.<ext>
      versions/v0001_<stage>.json   immutable snapshots (design model, parameters, QC summary, approvals)
      exports/                (written by exportpkg)

Snapshots are immutable; undo/redo only move the pointer; ``restore`` creates a NEW version that copies an old one
(history is never rewritten).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

STAGES = ("reference", "segmentation", "blueprint", "approved_plan", "stitch", "repaired", "manual_edit")


class ProjectError(Exception):
    pass


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)
    os.replace(tmp, path)


class ProjectStore:
    def __init__(self, path: str):
        self.path = path
        self.meta_path = os.path.join(path, "project.json")

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def create(cls, path: str, name: str, reference_file: str | None = None, settings: dict | None = None) -> "ProjectStore":
        if os.path.exists(path) and os.listdir(path):
            raise ProjectError(f"PROJECT_EXISTS: {path}")
        os.makedirs(os.path.join(path, "versions"), exist_ok=True)
        os.makedirs(os.path.join(path, "reference"), exist_ok=True)
        os.makedirs(os.path.join(path, "exports"), exist_ok=True)
        ref = None
        if reference_file:
            ext = os.path.splitext(reference_file)[1].lower()
            ref = f"reference/original{ext}"
            shutil.copyfile(reference_file, os.path.join(path, ref))
        meta = {"name": name, "created": _now(), "format": "eacproject/1", "reference": ref, "versions": [],
                "pointer": -1, "settings": settings or {}, "approval": {"plan_approved": False, "repairs_approved": []}}
        _atomic_write(os.path.join(path, "project.json"), meta)
        return cls(path)

    @classmethod
    def open(cls, path: str) -> "ProjectStore":
        if not os.path.isfile(os.path.join(path, "project.json")):
            raise ProjectError(f"NOT_A_PROJECT: {path}")
        return cls(path)

    def _meta(self) -> dict:
        with open(self.meta_path, encoding="utf-8") as f:
            return json.load(f)

    def _save_meta(self, m: dict):
        _atomic_write(self.meta_path, m)

    # ---------------------------------------------------------------- versions
    def commit(self, stage: str, state: dict, note: str = "") -> int:
        """Store an immutable snapshot and move the pointer to it (drops any redo tail from the pointer, not from disk)."""
        if not re.fullmatch(r"[a-z_]+", stage):
            raise ProjectError("BAD_STAGE_NAME")
        m = self._meta()
        n = len(m["versions"]) + 1
        fn = f"versions/v{n:04d}_{stage}.json"
        _atomic_write(os.path.join(self.path, fn), {"version": n, "stage": stage, "time": _now(), "note": note, "state": state})
        m["versions"].append({"version": n, "stage": stage, "file": fn, "time": _now(), "note": note, "parent": m["pointer"] + 1 if m["pointer"] >= 0 else None})
        m["pointer"] = len(m["versions"]) - 1
        m["redo"] = []
        self._save_meta(m)
        return n

    def history(self) -> list[dict]:
        m = self._meta()
        return [dict(v, current=(i == m["pointer"])) for i, v in enumerate(m["versions"])]

    def current(self) -> dict | None:
        m = self._meta()
        if m["pointer"] < 0:
            return None
        with open(os.path.join(self.path, m["versions"][m["pointer"]]["file"]), encoding="utf-8") as f:
            return json.load(f)

    def undo(self) -> dict | None:
        m = self._meta()
        if m["pointer"] <= 0:
            return None
        m.setdefault("redo", []).append(m["pointer"])
        m["pointer"] -= 1
        self._save_meta(m)
        return self.current()

    def redo(self) -> dict | None:
        m = self._meta()
        if not m.get("redo"):
            return None
        m["pointer"] = m["redo"].pop()
        self._save_meta(m)
        return self.current()

    def restore(self, version: int) -> int:
        """Restore = new version that copies an old snapshot (history stays intact)."""
        m = self._meta()
        hit = next((v for v in m["versions"] if v["version"] == version), None)
        if hit is None:
            raise ProjectError(f"NO_SUCH_VERSION: {version}")
        with open(os.path.join(self.path, hit["file"]), encoding="utf-8") as f:
            snap = json.load(f)
        return self.commit(snap["stage"], snap["state"], note=f"restored from v{version}")

    # ---------------------------------------------------------------- approval + resume
    def set_approval(self, plan_approved: bool | None = None, repair_id: str | None = None):
        m = self._meta()
        if plan_approved is not None:
            m["approval"]["plan_approved"] = bool(plan_approved)
        if repair_id:
            m["approval"]["repairs_approved"].append({"repair": repair_id, "time": _now()})
        self._save_meta(m)

    def resume(self) -> dict:
        """Everything needed to reopen the project after closing the browser."""
        m = self._meta()
        cur = self.current()
        return {"name": m["name"], "approval": m["approval"], "settings": m["settings"], "head": m["pointer"] + 1,
                "stage": cur["stage"] if cur else None, "state": cur["state"] if cur else None,
                "reference": os.path.join(self.path, m["reference"]) if m.get("reference") else None,
                "versions": len(m["versions"])}
