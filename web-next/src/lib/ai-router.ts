/**
 * Browser client for the Embroidery AI Copilot AI layer.
 *
 * SECURITY DESIGN: provider keys (GROQ/GEMINI/OPENROUTER) live ONLY in the backend environment. This module never sees, stores or sends a
 * key. Provider selection, retry, fallback and schema validation happen server-side (backend/ai/router.py) and every attempt is returned
 * so the UI can show exactly which provider/model answered. Nothing here executes a command: it only proposes, and applies what the
 * user approved.
 */
import { isProposal, type AiMode, type ApprovalMode, type Proposal } from "./schemas";

export class AiRouterError extends Error {
  constructor(public code: string, message: string, public status = 0) {
    super(message);
    this.name = "AiRouterError";
  }
}

export interface ClientOptions { baseUrl?: string; token?: string; timeoutMs?: number; retries?: number }

export class AiRouterClient {
  private base: string;
  private token?: string;
  private timeoutMs: number;
  private retries: number;

  constructor(opts: ClientOptions = {}) {
    this.base = (opts.baseUrl ?? "").replace(/\/$/, "");
    this.token = opts.token;
    this.timeoutMs = opts.timeoutMs ?? 30_000;
    this.retries = opts.retries ?? 1;
  }

  private async request<T>(path: string, body?: unknown, method?: "GET" | "POST"): Promise<T> {
    const verb = method ?? (body === undefined ? "GET" : "POST");
    let last: AiRouterError | undefined;
    for (let attempt = 0; attempt <= this.retries; attempt++) {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), this.timeoutMs);
      try {
        const res = await fetch(`${this.base}/api${path}`, {
          method: verb,
          headers: { "Content-Type": "application/json", ...(this.token ? { "X-Token": this.token } : {}) },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: ctl.signal,
        });
        const data: unknown = await res.json().catch(() => ({}));
        if (!res.ok) {
          const d = data as { error?: string; message?: string };
          const err = new AiRouterError(d.error ?? "HTTP", d.message ?? res.statusText, res.status);
          if (res.status >= 500 || res.status === 429) { last = err; continue; } // retry transient errors only
          throw err;
        }
        return data as T;
      } catch (e) {
        if (e instanceof AiRouterError) throw e;
        last = new AiRouterError((e as Error).name === "AbortError" ? "TIMEOUT" : "NETWORK", (e as Error).message);
      } finally {
        clearTimeout(timer);
      }
    }
    throw last ?? new AiRouterError("UNKNOWN", "request failed");
  }

  status() { return this.request<{ mode: AiMode; approval_mode: ApprovalMode; cloud_processing: boolean; providers: Record<string, { configured: boolean; model: string }> }>("/ai/status"); }
  configure(cfg: { ai_mode?: AiMode; approval_mode?: ApprovalMode }) { return this.request("/ai/config", cfg); }

  async propose(jobId: string, text: string): Promise<Proposal> {
    const p = await this.request<unknown>(`/jobs/${encodeURIComponent(jobId)}/ai/command`, { text });
    if (!isProposal(p)) throw new AiRouterError("BAD_RESPONSE", "server returned a malformed proposal");
    return p;
  }

  /** `exclude` = chip indexes the user dropped; `edits` = {chipIndex: {param: value}} (re-validated by the backend). */
  approve(jobId: string, proposalId: string, exclude: number[] = [], edits: Record<number, Record<string, number>> = {}) {
    return this.request<{ proposal: Proposal; summary: unknown }>(`/jobs/${encodeURIComponent(jobId)}/ai/approve`, { proposal_id: proposalId, exclude, edits });
  }
  reject(jobId: string, proposalId: string) { return this.request<Proposal>(`/jobs/${encodeURIComponent(jobId)}/ai/reject`, { proposal_id: proposalId }); }
  undo(jobId: string) { return this.request<{ changed: boolean; summary: unknown }>(`/jobs/${encodeURIComponent(jobId)}/ai/undo`, {}); }
  redo(jobId: string) { return this.request<{ changed: boolean; summary: unknown }>(`/jobs/${encodeURIComponent(jobId)}/ai/redo`, {}); }
  embCapabilities() { return this.request<Record<string, unknown>>("/emb/capabilities"); }
  embHandoff(jobId: string) { return this.request<{ files: string[]; not_an_emb: true }>(`/jobs/${encodeURIComponent(jobId)}/emb/handoff`, {}); }
}
