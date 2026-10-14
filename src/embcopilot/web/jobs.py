"""Job / session layer. Framework-free (no Flask/FastAPI needed): `server.py` is a thin HTTP shell over this, and any
ASGI/WSGI framework could wrap the same JobManager without touching the core engine."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field

from .. import __version__
from ..inputs import ACCEPTED, MAX_BYTES_DEFAULT, sniff

STAGES = ["UPLOAD", "PREPROCESS", "SEGMENT", "SEMANTIC_ANALYSIS", "BLUEPRINT", "TECHNICAL_PLAN", "STITCH_GENERATION", "QC",
          "AUTO_REPAIR", "COMPARISON", "APPROVAL", "EXPORT", "COMPLETE"]
SUBDIRS = ["original", "vision", "objects", "blueprint", "technical", "stitches", "simulation", "qc", "comparison", "exports", "logs"]
_FILE_TO_DIR = {"01": "vision", "02": "vision", "03": "vision", "04": "objects", "05": "blueprint", "06": "technical", "07": "stitches", "08": "stitches",
                "09": "stitches", "10": "qc", "11": "simulation", "12": "qc", "playback": "simulation", "design": "exports", "reference": "original"}


class JobError(Exception):
    def __init__(self, code: str, msg: str, http: int = 400):
        super().__init__(f"{code}: {msg}")
        self.code, self.http, self.msg = code, http, msg


@dataclass
class Job:
    job_id: str
    root: str
    input_file: str = ""
    input_kind: str = ""
    status: str = "created"  # created | processing | review | approved | failed
    stage: str = "UPLOAD"
    progress: int = 0
    warnings: list = field(default_factory=list)
    error: str = ""
    approved: bool = False
    approval_note: str = ""
    created: float = field(default_factory=time.time)
    settings: dict = field(default_factory=dict)
    result: object = None
    qc_issues: list = field(default_factory=list)
    proposals: list = field(default_factory=list)
    ctx: dict = field(default_factory=dict)
    exports: dict = field(default_factory=dict)
    applied_repairs: list = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)


class JobManager:
    def __init__(self, base_dir: str, max_bytes: int = MAX_BYTES_DEFAULT, max_jobs: int = 200):
        self.base = os.path.abspath(base_dir)
        os.makedirs(self.base, exist_ok=True)
        self.max_bytes, self.max_jobs = max_bytes, max_jobs
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ util
    def log(self, job: Job, action: str, status: str = "OK", message: str = "", **kw):
        rec = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "job_id": job.job_id, "stage": job.stage, "object_id": kw.get("object_id"),
               "action": action, "status": status, "confidence": kw.get("confidence"), "risk": kw.get("risk"),
               "duration_ms": kw.get("duration_ms"), "message": message}
        with open(os.path.join(job.root, "logs", "job.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def get(self, job_id: str) -> Job:
        if not job_id.isalnum():
            raise JobError("BAD_JOB_ID", "invalid job id", 400)
        j = self.jobs.get(job_id)
        if not j:
            raise JobError("JOB_NOT_FOUND", job_id, 404)
        return j

    def _safe_name(self, name: str) -> str:
        base = os.path.basename(name.replace("\\", "/"))
        keep = "".join(c for c in base if c.isalnum() or c in "._- ")[:80].strip(". ")
        return keep or "upload"

    # ---------------------------------------------------------------- create
    def create(self, filename: str, data: bytes) -> Job:
        if len(data) > self.max_bytes:
            raise JobError("FILE_TOO_LARGE", f"limit is {self.max_bytes // (1024*1024)} MB", 413)
        if not data:
            raise JobError("EMPTY_FILE", "no data received", 400)
        with self.lock:
            if len(self.jobs) >= self.max_jobs:
                oldest = min(self.jobs.values(), key=lambda j: j.created)
                self.delete(oldest.job_id)
        jid = uuid.uuid4().hex[:16]
        root = os.path.join(self.base, jid)
        for d in SUBDIRS:
            os.makedirs(os.path.join(root, d), exist_ok=True)
        fname = self._safe_name(filename)
        path = os.path.join(root, "original", fname)
        with open(path, "wb") as fh:
            fh.write(data)
        job = Job(jid, root, path)
        sn = sniff(path, self.max_bytes)
        job.input_kind = sn.kind
        if not sn.ok:
            job.status, job.error = "failed", f"INPUT_REJECTED: {sn.problem}"
            self.log(job, "upload", "REJECTED", sn.problem)
        else:
            if not sn.extension_matches:
                job.warnings.append(f"file extension does not match its content; treated as {sn.kind.upper()}")
            self.log(job, "upload", "OK", f"{fname} detected as {sn.kind}")
        job.progress = 3
        self.jobs[jid] = job
        return job

    def delete(self, job_id: str):
        j = self.jobs.pop(job_id, None)
        if j:
            shutil.rmtree(j.root, ignore_errors=True)

    def cleanup_older_than(self, hours: float) -> int:
        cut = time.time() - hours * 3600
        old = [j.job_id for j in self.jobs.values() if j.created < cut]
        for i in old:
            self.delete(i)
        return len(old)

    # --------------------------------------------------------------- analyze
    def analyze(self, job_id: str, settings: dict, background: bool = True) -> Job:
        job = self.get(job_id)
        if job.status == "failed":
            raise JobError("JOB_FAILED", job.error, 409)
        if job.status == "processing":
            raise JobError("JOB_BUSY", "already processing", 409)
        if job.input_kind not in ("png", "jpg", "webp", "svg", "ai"):
            job.status, job.error = "failed", f"INPUT_KIND_NOT_DIGITIZABLE: {job.input_kind.upper()} is a machine/native file, not artwork"
            self.log(job, "analyze", "REJECTED", job.error)
            raise JobError("INPUT_KIND_NOT_DIGITIZABLE", job.error, 422)
        try:
            w = float(settings.get("width_mm", 100)); h = float(settings.get("height_mm", 100))
        except (TypeError, ValueError):
            raise JobError("BAD_SETTINGS", "width_mm/height_mm must be numbers", 400)
        if not (5 <= w <= 1000 and 5 <= h <= 1000):
            raise JobError("BAD_SETTINGS", "size must be between 5 and 1000 mm", 400)
        job.settings = {**settings, "width_mm": w, "height_mm": h}
        job.status, job.progress, job.error, job.cancel = "processing", 5, "", threading.Event()
        job.approved = False
        if background:
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        else:
            self._run(job)
        return job

    def _run(self, job: Job):
        from ..pipeline import run_pipeline
        from ..repair import QCTargets, propose_repairs, run_qc
        s = job.settings
        t0 = time.time()

        def prog(stage, pct):
            if job.cancel.is_set():
                raise RuntimeError("cancelled by user")
            job.stage, job.progress = stage, pct

        try:
            work = os.path.join(job.root, "_work")
            shutil.rmtree(work, ignore_errors=True)
            res = run_pipeline(job.input_file, (s["width_mm"], s["height_mm"]), s.get("profile", "balanced"), s.get("fabric", "woven"), work,
                               overrides=s.get("overrides") or None, brand=s.get("brand") or None,
                               colorway_mode=s.get("colorway_mode", "nearest"), hoop=s.get("hoop") or None,
                               segmentation=s.get("segmentation", "standard"), progress=prog, vector_fit=bool(s.get("vector_fit", False)))
            if res.stopped:
                job.status, job.stage, job.error = "failed", "COMPLETE", res.stopped
                self.log(job, "analyze", "STOPPED", res.stopped)
                return
            job.result = res
            job.stage, job.progress = "AUTO_REPAIR", 88
            tg = QCTargets(**(s.get("targets") or {}))
            job.ctx = {"objects": res.objects, "technical": res.technical, "canvas": res.plan.canvas_mm, "deps": res.graph.dependencies(), "profile": s.get("profile", "balanced")}
            job.qc_issues = run_qc(res.plan, res.objects, res.validation, tg, res.technical)
            job.proposals = propose_repairs(res.plan, job.qc_issues, job.ctx, res.objects, tg)
            self._organize(work, job)
            job.warnings = list(dict.fromkeys(job.warnings + res.warnings))
            job.stage, job.progress, job.status = "APPROVAL", 95, "review"
            self.log(job, "analyze", "OK", f"{len(res.objects)} objects, validation {res.validation.status}", duration_ms=int((time.time() - t0) * 1000))
        except Exception as e:  # noqa: BLE001 - a job failure must never take the server down
            job.status, job.error = "failed", f"{type(e).__name__}: {e}"
            self.log(job, "analyze", "FAILED", job.error)

    def _organize(self, work: str, job: Job):
        for fn in os.listdir(work):
            key = fn.split("_")[0].rstrip("b") if fn[0].isdigit() else fn.split(".")[0]
            key = fn[:2] if fn[0].isdigit() else key
            d = _FILE_TO_DIR.get(key, "objects")
            if fn.startswith("design"):
                continue  # exports are produced on demand, after approval
            shutil.move(os.path.join(work, fn), os.path.join(job.root, d, fn))
        shutil.rmtree(work, ignore_errors=True)

    # ---------------------------------------------------------------- repair
    def repair(self, job_id: str, names: list[str]) -> dict:
        from ..repair import apply_repairs, metrics, propose_repairs, run_qc, QCTargets
        from ..stitch import validate_stitch_plan
        job = self.get(job_id)
        if not job.result:
            raise JobError("NOT_ANALYZED", "analyze first", 409)
        res = job.result
        before = metrics(res.plan)
        new_plan, applied = apply_repairs(res.plan, job.proposals, names, job.ctx)
        if not applied:
            raise JobError("NO_REPAIR_APPLIED", "none of the requested repairs is available/approved (rejected or no effect)", 409)
        res.plan = new_plan
        res.validation = validate_stitch_plan(new_plan, res.objects, job.ctx["deps"])
        tg = QCTargets(**(job.settings.get("targets") or {}))
        job.qc_issues = run_qc(res.plan, res.objects, res.validation, tg, res.technical)
        job.proposals = propose_repairs(res.plan, job.qc_issues, job.ctx, res.objects, tg)
        job.applied_repairs += applied
        job.approved, job.exports = False, {}  # any change invalidates a previous approval and exports
        self._write_plan(job)
        after = metrics(res.plan)
        self.log(job, "repair", "APPLIED", ",".join(applied))
        job.status = "review"
        return {"applied": applied, "before": before, "after": after}

    def _write_plan(self, job: Job):
        import cv2
        from ..simulate import html_player, render
        res = job.result
        with open(os.path.join(job.root, "stitches", "07_stitch_geometry.json"), "w") as fh:
            json.dump(res.plan.to_dict(True), fh)
        cv2.imwrite(os.path.join(job.root, "simulation", "11_simulation.png"), render(res.plan, "path"))
        cv2.imwrite(os.path.join(job.root, "simulation", "11b_simulation_thread.png"), render(res.plan, "thread"))
        with open(os.path.join(job.root, "simulation", "playback.html"), "w", encoding="utf-8") as fh:
            fh.write(html_player(res.plan, res.objects))

    # -------------------------------------------------------------- approval
    def approve(self, job_id: str, approved: bool, note: str = "") -> Job:
        job = self.get(job_id)
        if not job.result or job.status not in ("review", "approved"):
            raise JobError("NOT_READY", "nothing to approve yet", 409)
        if approved and job.result.validation.status == "ERROR":
            raise JobError("CANNOT_APPROVE_ERRORS", "validation has ERROR findings; repair or fix before approving", 409)
        job.approved, job.approval_note = approved, note[:500]
        job.status = "approved" if approved else "review"
        self.log(job, "approval", "APPROVED" if approved else "REJECTED", note[:200])
        return job

    # ---------------------------------------------------------------- export
    def export(self, job_id: str, fmt: str) -> dict:
        from ..formats import ExportError, capabilities, export_design
        job = self.get(job_id)
        fmt = fmt.lower()
        cap = capabilities().get(fmt)
        if cap is None:
            raise JobError("UNKNOWN_FORMAT", fmt, 404)
        if not cap.can_export:
            raise JobError(f"{fmt.upper()}_UNAVAILABLE", cap.reason, 501)
        if not job.result:
            raise JobError("NOT_ANALYZED", "analyze first", 409)
        if not job.approved:
            raise JobError("APPROVAL_REQUIRED", "a human must approve the design before export", 403)
        path = os.path.join(job.root, "exports", f"design.{fmt}")
        try:
            info = export_design(job.result.plan, fmt, path, validation=job.result.validation)
        except ExportError as e:
            self.log(job, f"export_{fmt}", "FAILED", str(e))
            raise JobError("EXPORT_FAILED", str(e), 422)
        info["path"] = f"exports/design.{fmt}"
        job.exports[fmt] = info
        self.log(job, f"export_{fmt}", "VERIFIED", info["verification"])
        return info

    # ------------------------------------------------------------ artifacts
    def artifact_path(self, job_id: str, rel: str) -> str:
        job = self.get(job_id)
        rel = rel.replace("\\", "/")
        if rel.startswith("/") or ".." in rel.split("/") or "\x00" in rel:
            raise JobError("BAD_PATH", "path traversal blocked", 400)
        full = os.path.abspath(os.path.join(job.root, rel))
        if not full.startswith(os.path.abspath(job.root) + os.sep) or not os.path.isfile(full):
            raise JobError("ARTIFACT_NOT_FOUND", rel, 404)
        return full

    def zip_job(self, job_id: str) -> str:
        job = self.get(job_id)
        out = os.path.join(self.base, f"{job.job_id}.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for d, _s, files in os.walk(job.root):
                for f in files:
                    p = os.path.join(d, f)
                    z.write(p, os.path.relpath(p, job.root))
        return out

    def list_artifacts(self, job: Job) -> list[str]:
        out = []
        for d in SUBDIRS:
            p = os.path.join(job.root, d)
            out += [f"{d}/{f}" for f in sorted(os.listdir(p))]
        return out

    # --------------------------------------------------------------- summary
    def summary(self, job_id: str) -> dict:
        from ..formats import capabilities
        job = self.get(job_id)
        d = {"job_id": job.job_id, "version": __version__, "status": job.status, "stage": job.stage, "progress": job.progress,
             "input_file": os.path.basename(job.input_file), "input_kind": job.input_kind, "warnings": job.warnings, "error": job.error,
             "approved": job.approved, "approval_note": job.approval_note, "settings": job.settings, "stages": STAGES,
             "artifacts": self.list_artifacts(job), "applied_repairs": job.applied_repairs}
        res = job.result
        if res:
            byid = {a.object_id: a for a in res.assessments}
            cmp_ = {c.object_id: c for c in res.comparison}
            plans = {p.object_id: p for p in res.technical.plans}
            d["vision_mode"] = res.vision_mode
            d["objects"] = [{
                "id": o.object_id, "name": o.name, "semantic": o.category,
                "semantic_confidence": o.confidence["semantic"].value if "semantic" in o.confidence else None,
                "alternatives": o.semantic_alternatives, "reason": o.semantic_reason, "parent": o.parent_object_id, "z": o.z_order,
                "color": o.color_group, "stitch": res.plan.object_status.get(o.object_id, {}).get("strategy"),
                "density_mm": plans[o.object_id].density_mm, "underlay": plans[o.object_id].underlay,
                "pull_mm": plans[o.object_id].pull_compensation_mm, "angle": plans[o.object_id].direction_deg,
                "tech_warnings": plans[o.object_id].warnings, "status": res.plan.object_status.get(o.object_id, {}),
                "risk": byid[o.object_id].risk, "why_review": byid[o.object_id].reasons, "dims": byid[o.object_id].dimensions,
                "match": cmp_[o.object_id].to_dict() if o.object_id in cmp_ else None} for o in res.objects]
            d["stats"] = res.plan.stats()
            d["validation"] = res.validation.to_dict()
            d["comparison_global"] = res.comparison_global
            d["qc_issues"] = [i.to_dict() for i in job.qc_issues]
            d["repair_proposals"] = [p.to_dict() for p in job.proposals]
            d["colorway"] = res.colorway.to_dict() if res.colorway else None
            d["hoop"] = res.hoop_check
            d["timings_s"] = res.timings
            d["dst_qc"] = res.qc.get("dst_qc")
        caps = {k: v.to_dict() for k, v in capabilities().items()}
        for k, c in caps.items():
            c["enabled"] = bool(c["can_export"] and job.approved and res)
            c["disabled_reason"] = "" if c["enabled"] else (c["reason"] if not c["can_export"] else ("approve the design first" if res else "analyze first"))
        d["exports_available"] = caps
        d["exports_done"] = job.exports
        return d
