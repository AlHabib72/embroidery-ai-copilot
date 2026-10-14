"""AIController: prompt -> router -> validated steps -> policy -> preview -> (approval) -> session -> audit.
Proposals are immutable records; the user may drop chips or edit a numeric chip before approving; edited chips are re-validated."""
from __future__ import annotations

import copy
import os
import time
import uuid

from ..emb_studio import emb_capabilities, handoff_bundle
from . import policy
from .router import AIRouter
from .schemas import RANGES, Step, validate_step
from .session import DesignSession

EDITABLE = {"width_mm", "density_mm", "pull_comp_mm", "pull_comp_pct", "max_stitch_mm", "min_stitch_mm", "angle_deg", "underlay_spacing_mm", "underlay_inset_mm"}


def chip_for(i: int, s: Step, risk: str) -> dict:
    bits = [f"{k}: {v if not isinstance(v, list) else '+'.join(v)}" for k, v in s.params.items()]
    return {"index": i, "label": f"{s.op.replace('_', ' ')}" + (f" - {s.target}" if s.target else "") + (f" ({', '.join(bits)})" if bits else ""),
            "op": s.op, "target": s.target, "params": s.params, "risk": risk,
            "editable": {k: list(RANGES[k]) for k in s.params if k in EDITABLE}}


class AIController:
    def __init__(self, session: DesignSession, router: AIRouter, approval_mode: str = "CONFIRM", work_dir: str | None = None):
        self.session, self.router = session, router
        self.approval_mode = approval_mode.upper()
        policy.assess([], self.approval_mode)   # validates the mode name
        self.work_dir = work_dir
        self.proposals: dict[str, dict] = {}

    def set_mode(self, approval_mode: str | None = None, ai_mode: str | None = None):
        if approval_mode:
            policy.assess([], approval_mode); self.approval_mode = approval_mode.upper()
        if ai_mode:
            self.router = AIRouter(ai_mode.upper(), self.router.order, self.router.providers[self.router.order[0]].transport,
                                   self.router.providers[self.router.order[0]].sleep, self.router.timeout)

    def propose(self, text: str, dry_run_preview: bool = True) -> dict:
        res = self.router.parse(text, self.session.role_names())
        dec = policy.assess(res.steps, self.approval_mode)
        pid = uuid.uuid4().hex[:10]
        p = {"proposal_id": pid, "prompt": text, "created": time.time(), "router": res.to_dict(), "decision": dec.to_dict(),
             "chips": [chip_for(i, s, dec.step_risks[i]) for i, s in enumerate(res.steps)], "status": "pending", "preview": None,
             "unrecognised": res.unrecognised, "errors": res.errors}
        edits = [s for s in res.steps if s.op not in ("qc", "emb_info", "protect", "export")]
        if res.steps and not dec.blocked:
            if edits and dry_run_preview:
                try:
                    p["preview"] = self.session.preview(res.steps)
                except Exception as e:   # noqa: BLE001 - a preview failure must not crash the console
                    p["preview"] = {"error": f"{type(e).__name__}: {e}"}
        self.proposals[pid] = p
        self._steps = getattr(self, "_steps", {}); self._steps[pid] = res.steps
        if dec.blocked:
            p["status"] = "blocked"
        elif dec.auto_apply and res.steps:
            out = self._execute(pid, res.steps, approval="auto")
            p["status"] = "applied"; p["result"] = out
        elif not res.steps:
            p["status"] = "nothing_to_do"
        return p

    def approve(self, pid: str, exclude: list[int] | None = None, edits: dict | None = None) -> dict:
        p = self.proposals.get(pid)
        if p is None:
            raise KeyError("UNKNOWN_PROPOSAL")
        if p["status"] != "pending":
            raise ValueError(f"PROPOSAL_NOT_PENDING: status is {p['status']}")
        steps = list(self._steps[pid])
        exclude = set(exclude or [])
        final: list[Step] = []
        for i, s in enumerate(steps):
            if i in exclude:
                continue
            d = {"op": s.op, "target": s.target, **copy.deepcopy(s.params)}
            for k, v in ((edits or {}).get(str(i)) or (edits or {}).get(i) or {}).items():
                if k not in EDITABLE:
                    raise ValueError(f"PARAM_NOT_EDITABLE: {k}")
                d[k] = v
            final.append(validate_step(d))                       # edited values are re-validated
        out = self._execute(pid, final, approval="user")
        p["status"] = "applied"; p["result"] = out
        return p

    def reject(self, pid: str) -> dict:
        p = self.proposals[pid]
        if p["status"] == "pending":
            p["status"] = "rejected"
            self.session.audit.record(command_id=pid, event="REJECTED", user_prompt=p["prompt"], provider=p["router"]["provider"])
        return p

    def _execute(self, pid: str, steps: list[Step], approval: str) -> dict:
        p = self.proposals[pid]
        r = self.session.apply(steps, p["prompt"], p["router"]["provider"], p["router"]["model"], p["decision"]["risk"], approval)
        actions = []
        for s in steps:
            if s.op == "emb_info":
                actions.append({"op": "emb_info", "result": emb_capabilities()})
            elif s.op == "qc":
                actions.append({"op": "qc", "result": self.session.build.qc.to_dict()})
            elif s.op == "export":
                actions.append({"op": "export", "result": self._export(s.params["format"])})
        r["actions"] = actions
        return r

    def _export(self, fmt: str) -> dict:
        from ..exportpkg import export_package
        if fmt == "emb":
            caps = emb_capabilities()
            res = {"status": caps["EMB_EXPORT"]["state"], "download": False, "message": caps["message"],
                   "reason": caps["EMB_EXPORT"]["reason"], "alternative": "handoff bundle (DST + object SVG + manifest) via /api/jobs/{id}/emb/handoff"}
            if self.work_dir:
                res["handoff"] = handoff_bundle(self.session.build, os.path.join(self.work_dir, "emb_handoff"))
            return res
        if not self.work_dir:
            return {"status": "UNAVAILABLE", "reason": "no work directory configured"}
        meta = export_package(self.session.build.plan, self.session.build.objects, os.path.join(self.work_dir, "export"),
                              self.session.build.qc, (fmt,))
        return meta["files"].get(f"design.{fmt}", {"status": "UNAVAILABLE"})
