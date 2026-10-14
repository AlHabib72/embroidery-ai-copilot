"""Cloud LLM providers (Groq, Gemini, OpenRouter).  Keys come ONLY from environment variables (or a local .env that is git-ignored).
Transport is injectable so tests never touch the network.  Keys are redacted from every error, log and audit record."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
MODEL_ENV = {"groq": ("GROQ_MODEL", "llama-3.3-70b-versatile"), "gemini": ("GEMINI_MODEL", "gemini-2.0-flash"),
             "openrouter": ("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")}   # defaults NOT verified against the live APIs: override via env
_PATTERNS = [re.compile(p) for p in (r"gsk_[A-Za-z0-9]{10,}", r"sk-or-[A-Za-z0-9_\-]{10,}", r"AIza[0-9A-Za-z_\-]{20,}", r"AQ\.[A-Za-z0-9_\-]{20,}",
                                      r"sk-[A-Za-z0-9]{20,}", r"Bearer\s+[A-Za-z0-9._\-]{10,}")]


def redact(text) -> str:
    s = str(text)
    for env in KEY_ENV.values():
        v = os.environ.get(env)
        if v and len(v) >= 8:
            s = s.replace(v, "***")
    for p in _PATTERNS:
        s = p.sub("***", s)
    return s


def redact_obj(o):
    if isinstance(o, str):
        return redact(o)
    if isinstance(o, dict):
        return {k: redact_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [redact_obj(v) for v in o]
    return o


def load_env(path: str = ".env") -> list[str]:
    """Minimal .env reader; sets only variables that are not already set.  Returns NAMES loaded (never values)."""
    loaded = []
    if not os.path.isfile(path):
        return loaded
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v
                loaded.append(k)
    return loaded


class ProviderError(Exception):
    def __init__(self, code: str, msg: str = "", retry_after: float | None = None, retryable: bool = False):
        super().__init__(redact(msg))
        self.code, self.retry_after, self.retryable = code, retry_after, retryable


def default_transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError("NETWORK", f"{type(e).__name__}", retryable=True)


def extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        v = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise ProviderError("BAD_JSON", "model did not return JSON")
        try:
            v = json.loads(m.group(0))
        except json.JSONDecodeError:
            raise ProviderError("BAD_JSON", "model returned malformed JSON")
    if not isinstance(v, dict):
        raise ProviderError("BAD_JSON", "JSON root is not an object")
    return v


class Provider:
    name = "base"

    def __init__(self, transport=None, sleep=time.sleep):
        self.transport = transport or default_transport
        self.sleep = sleep

    @property
    def model(self) -> str:
        env, dflt = MODEL_ENV[self.name]
        return os.environ.get(env, dflt)

    def key(self) -> str | None:
        return os.environ.get(KEY_ENV[self.name]) or None

    def configured(self) -> bool:
        return bool(self.key())

    def _request(self, system: str, user: str) -> tuple[str, dict, bytes]: ...
    def _text(self, payload: dict) -> str: ...

    def chat_json(self, system: str, user: str, timeout: float = 20.0, retries: int = 2) -> dict:
        if not self.configured():
            raise ProviderError("NOT_CONFIGURED", f"{KEY_ENV[self.name]} is not set")
        url, headers, body = self._request(system, user)
        last: ProviderError | None = None
        for attempt in range(retries + 1):
            try:
                status, raw = self.transport(url, headers, body, timeout)
            except ProviderError as e:
                last = e
            else:
                if status == 200:
                    try:
                        return extract_json(self._text(json.loads(raw)))
                    except ProviderError:
                        raise
                    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                        raise ProviderError("BAD_RESPONSE", "unexpected provider response shape")
                if status in (401, 403):
                    raise ProviderError("AUTH", f"HTTP {status}: key rejected (rotate/replace the key)")
                if status == 429:
                    last = ProviderError("RATE_LIMITED", "HTTP 429", retry_after=2.0 * (attempt + 1), retryable=True)
                elif status >= 500:
                    last = ProviderError("SERVER", f"HTTP {status}", retryable=True)
                else:
                    raise ProviderError("HTTP", f"HTTP {status}")
            if last and last.retryable and attempt < retries:
                self.sleep(last.retry_after or 0.5 * (2 ** attempt))
                continue
            break
        raise last or ProviderError("UNKNOWN")


class _OpenAICompat(Provider):
    url = ""

    def _request(self, system, user):
        body = json.dumps({"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        return self.url, {"Content-Type": "application/json", "Authorization": f"Bearer {self.key()}"}, body

    def _text(self, p):
        return p["choices"][0]["message"]["content"]


class GroqProvider(_OpenAICompat):
    name = "groq"
    url = "https://api.groq.com/openai/v1/chat/completions"


class OpenRouterProvider(_OpenAICompat):
    name = "openrouter"
    url = "https://openrouter.ai/api/v1/chat/completions"


class GeminiProvider(Provider):
    name = "gemini"

    def _url(self):
        return f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    def _request(self, system, user):
        body = json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                           "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}).encode()
        return self._url(), {"Content-Type": "application/json", "x-goog-api-key": self.key() or ""}, body   # key in header, never in the URL

    def _text(self, p):
        return p["candidates"][0]["content"]["parts"][0]["text"]

    VISUAL_SYSTEM = ("You review a rendered embroidery stitch/density image. Output ONLY JSON {\"findings\":[{\"kind\":str,\"severity\":\"info|warning|error\","
                     "\"region_hint\":str,\"note\":str}],\"summary\":str}. Do not output stitch coordinates, code or commands.")

    def analyze_visual(self, png_bytes: bytes, context: str, allow_image_upload: bool, timeout: float = 40.0) -> dict:
        """Advisory visual QC.  Uploads the image ONLY when the caller passes allow_image_upload=True (explicit user consent)."""
        if not allow_image_upload:
            raise ProviderError("CONSENT_REQUIRED", "image upload to a cloud model needs explicit consent")
        import base64
        if not self.configured():
            raise ProviderError("NOT_CONFIGURED", f"{KEY_ENV[self.name]} is not set")
        body = json.dumps({"systemInstruction": {"parts": [{"text": self.VISUAL_SYSTEM}]},
                           "contents": [{"role": "user", "parts": [{"text": context[:500]}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png_bytes).decode()}}]}],
                           "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}).encode()
        status, raw = self.transport(self._url(), {"Content-Type": "application/json", "x-goog-api-key": self.key() or ""}, body, timeout)
        if status != 200:
            raise ProviderError("HTTP", f"HTTP {status}")
        out = extract_json(self._text(json.loads(raw)))
        fs = out.get("findings", [])
        if not isinstance(fs, list):
            raise ProviderError("BAD_JSON", "findings must be a list")
        clean = [{"kind": str(f.get("kind", ""))[:40], "severity": f.get("severity") if f.get("severity") in ("info", "warning", "error") else "info",
                  "region_hint": str(f.get("region_hint", ""))[:80], "note": str(f.get("note", ""))[:300]} for f in fs[:30] if isinstance(f, dict)]
        return {"findings": clean, "summary": str(out.get("summary", ""))[:500], "provider": "gemini", "model": self.model,
                "advisory": True, "note": "advisory only; never converted into stitch coordinates"}


PROVIDERS = {"groq": GroqProvider, "gemini": GeminiProvider, "openrouter": OpenRouterProvider}
