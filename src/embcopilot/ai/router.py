"""AIRouter: LOCAL / CLOUD / HYBRID.  Never switches silently: every attempt, fallback and the final provider/model are recorded.
CLOUD never falls back to the local parser on its own; HYBRID does, and says so (`fallback_to_local=True`)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .local_parser import parse_local
from .providers import PROVIDERS, ProviderError, redact
from .schemas import from_spec_style, system_prompt, validate_steps

MODES = ("LOCAL", "CLOUD", "HYBRID")


@dataclass
class Attempt:
    provider: str
    model: str
    ok: bool
    code: str
    latency_ms: float
    detail: str = ""

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class RouterResult:
    steps: list
    errors: list
    unrecognised: list
    provider: str
    model: str
    mode: str
    attempts: list = field(default_factory=list)
    fallback_used: bool = False
    fallback_to_local: bool = False
    latency_ms: float = 0.0
    cloud_payload: dict | None = None

    def to_dict(self):
        return {"steps": [s.to_dict() for s in self.steps], "errors": self.errors, "unrecognised": self.unrecognised, "provider": self.provider,
                "model": self.model, "mode": self.mode, "attempts": [a.to_dict() for a in self.attempts], "fallback_used": self.fallback_used,
                "fallback_to_local": self.fallback_to_local, "latency_ms": round(self.latency_ms, 1), "cloud_payload": self.cloud_payload}


class AIRouter:
    def __init__(self, mode: str = "LOCAL", order=("groq", "gemini", "openrouter"), transport=None, sleep=None, timeout: float = 20.0):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode, self.order, self.timeout = mode, tuple(order), timeout
        kw = {k: v for k, v in (("transport", transport), ("sleep", sleep)) if v is not None}
        self.providers = {n: PROVIDERS[n](**kw) for n in self.order}

    def status(self) -> dict:
        return {"mode": self.mode, "cloud_processing": self.mode != "LOCAL",
                "providers": {n: {"configured": p.configured(), "model": p.model} for n, p in self.providers.items()},   # booleans only: never key material
                "privacy": self.privacy_manifest()}

    @staticmethod
    def privacy_manifest() -> dict:
        return {"sent_to_cloud": ["your prompt text", "list of object role names", "allowed operation schema"],
                "never_sent": ["artwork images", "stitch data", "file paths", "API keys of other providers"],
                "images": "only to Gemini visual QC, and only after explicit per-call consent"}

    def parse(self, text: str, roles: list[str]) -> RouterResult:
        text = (text or "").strip()[:800]
        t0 = time.time()
        if not text:
            return RouterResult([], ["empty prompt"], [], "none", "", self.mode)
        if self.mode == "LOCAL":
            return self._local(text, roles, t0, [])
        attempts: list[Attempt] = []
        payload = {"prompt": text, "roles": roles}
        sysmsg = system_prompt(roles)
        for i, name in enumerate(self.order):
            p = self.providers[name]
            ta = time.time()
            if not p.configured():
                attempts.append(Attempt(name, p.model, False, "NOT_CONFIGURED", 0.0, "key not set"))
                continue
            try:
                raw = p.chat_json(sysmsg, text, self.timeout)
                raw_steps = raw.get("steps") if "steps" in raw else from_spec_style(raw)
                steps, errs = validate_steps(raw_steps)
                attempts.append(Attempt(name, p.model, True, "OK", (time.time() - ta) * 1000, f"{len(steps)} valid step(s), {len(errs)} rejected"))
                return RouterResult(steps, errs, [], name, p.model, self.mode, attempts, fallback_used=any(not a.ok for a in attempts),
                                    latency_ms=(time.time() - t0) * 1000, cloud_payload=payload)
            except ProviderError as e:
                attempts.append(Attempt(name, p.model, False, e.code, (time.time() - ta) * 1000, redact(str(e))))
        if self.mode == "HYBRID":
            r = self._local(text, roles, t0, attempts)
            r.fallback_to_local = True; r.fallback_used = True
            return r
        return RouterResult([], ["CLOUD_UNAVAILABLE: all configured providers failed or none configured; switch to LOCAL or HYBRID, or set a key"], [],
                            "none", "", self.mode, attempts, latency_ms=(time.time() - t0) * 1000)

    def _local(self, text, roles, t0, attempts) -> RouterResult:
        out = parse_local(text)
        steps, errs = validate_steps(out["steps"])
        return RouterResult(steps, errs, out["unrecognised"], "local-rules", "rule-parser-v1", self.mode, attempts,
                            latency_ms=(time.time() - t0) * 1000)
