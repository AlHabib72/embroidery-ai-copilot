"""Learning / feedback store + fabric calibration (v0.6).

Nothing is collected unless the user ENABLES learning.  Nothing trains silently: this module only stores records
the user explicitly submits, can export them, delete them and reset preferences.  Calibration recommends a profile
from the user's OWN sew-out results; it never claims a universal value ("0.35 mm is always right for denim").
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

RESULTS = ("PASS", "MINOR_ISSUE", "REWORK", "FAIL")
_RANK = {"PASS": 0, "MINOR_ISSUE": 1, "REWORK": 2, "FAIL": 3}


@dataclass
class SewOutRecord:
    design: str
    fabric: str
    machine: str = ""
    needle: str = ""
    thread: str = ""
    density_mm: float | None = None
    underlay: str = ""
    pull_comp_mm: float | None = None
    result: str = "PASS"
    operator_notes: str = ""
    photo: str = ""            # path/reference only; the image is never uploaded anywhere
    ai_output: dict = field(default_factory=dict)
    human_correction: dict = field(default_factory=dict)
    final_output: dict = field(default_factory=dict)
    time: str = ""

    def __post_init__(self):
        if self.result not in RESULTS:
            raise ValueError(f"result must be one of {RESULTS}")
        self.time = self.time or time.strftime("%Y-%m-%dT%H:%M:%S")


class LearningDisabled(Exception):
    pass


class LearningStore:
    def __init__(self, path: str):
        self.dir = path
        os.makedirs(path, exist_ok=True)
        self.cfg_path = os.path.join(path, "settings.json")
        self.rec_path = os.path.join(path, "records.jsonl")

    def _cfg(self) -> dict:
        if os.path.exists(self.cfg_path):
            with open(self.cfg_path, encoding="utf-8") as f:
                return json.load(f)
        return {"enabled": False, "preferences": {}}

    def _save_cfg(self, c):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=1)

    @property
    def enabled(self) -> bool:
        return bool(self._cfg()["enabled"])

    def enable(self):
        c = self._cfg(); c["enabled"] = True; self._save_cfg(c)

    def disable(self):
        c = self._cfg(); c["enabled"] = False; self._save_cfg(c)

    def set_preference(self, key: str, value):
        if not self.enabled:
            raise LearningDisabled("enable learning first")
        c = self._cfg(); c["preferences"][key] = value; self._save_cfg(c)

    def reset_preferences(self):
        c = self._cfg(); c["preferences"] = {}; self._save_cfg(c)

    def add_record(self, rec: SewOutRecord):
        if not self.enabled:
            raise LearningDisabled("learning is disabled; record not stored")
        with open(self.rec_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec)) + "\n")

    def records(self) -> list[dict]:
        if not os.path.exists(self.rec_path):
            return []
        with open(self.rec_path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def export_data(self, out_path: str) -> str:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"settings": self._cfg(), "records": self.records()}, f, indent=1)
        return out_path

    def delete_data(self):
        if os.path.exists(self.rec_path):
            os.remove(self.rec_path)


def recommend_calibrated_profile(records: list[dict], fabric: str, min_samples: int = 2) -> dict:
    """Pick the parameter set with the best real sew-out outcomes for this fabric (+ machine/thread if provided).
    Needs >= min_samples records; returns status INSUFFICIENT_DATA otherwise (no invented numbers)."""
    rs = [r for r in records if r.get("fabric", "").lower() == fabric.lower() and r.get("pull_comp_mm") is not None]
    if len(rs) < min_samples:
        return {"status": "INSUFFICIENT_DATA", "samples": len(rs), "needed": min_samples,
                "next_step": "sew test swatches with 2+ different pull-compensation values and record the results"}
    groups: dict = {}
    for r in rs:
        key = (r["pull_comp_mm"], r.get("density_mm"), r.get("underlay", ""))
        groups.setdefault(key, []).append(_RANK[r["result"]])
    best = min(groups.items(), key=lambda kv: (sum(kv[1]) / len(kv[1]), -len(kv[1])))
    (pc, dens, ul), ranks = best
    return {"status": "CALIBRATED_FROM_USER_SEWOUTS", "fabric": fabric, "pull_comp_mm": pc, "density_mm": dens, "underlay": ul,
            "samples": len(ranks), "mean_rank": round(sum(ranks) / len(ranks), 2),
            "caveat": "valid for the tested machine/needle/thread/stabiliser only; re-test when any of these change"}
