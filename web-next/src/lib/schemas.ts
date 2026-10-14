/** Types + runtime guards mirroring backend/ai/schemas.py. The backend is the authority; these guard the UI against malformed responses. */
export type Risk = "low" | "medium" | "high";
export type ApprovalMode = "SUGGEST" | "CONFIRM" | "AUTO" | "MANUAL";
export type AiMode = "LOCAL" | "CLOUD" | "HYBRID";
export type ProposalStatus = "pending" | "applied" | "rejected" | "blocked" | "nothing_to_do";

export interface Chip {
  index: number;
  label: string;
  op: string;
  target: string | null;
  params: Record<string, number | string | string[]>;
  risk: Risk;
  /** param name -> [min, max]; only these may be edited in the UI */
  editable: Record<string, [number, number]>;
}

export interface Metrics {
  stitch_count: number;
  jump_count: number;
  trim_count: number;
  center_crossing_travel: number;
  short_stitch_count: number;
  qc_errors: number;
  over_max_stitch: number;
}

export interface Attempt { provider: string; model: string; ok: boolean; code: string; latency_ms: number; detail: string }

export interface Proposal {
  proposal_id: string;
  prompt: string;
  status: ProposalStatus;
  chips: Chip[];
  router: { provider: string; model: string; mode: AiMode; attempts: Attempt[]; fallback_used: boolean; fallback_to_local: boolean; latency_ms: number };
  decision: { risk: Risk; requires_approval: boolean; auto_apply: boolean; blocked: boolean; reason: string };
  preview: { before: Metrics; after: Metrics; skipped: unknown[]; notes: string[] } | { error: string } | null;
  errors: string[];
  unrecognised: string[];
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

export function isProposal(v: unknown): v is Proposal {
  if (!isObj(v) || typeof v.proposal_id !== "string" || !Array.isArray(v.chips) || !isObj(v.router) || !isObj(v.decision)) return false;
  return v.chips.every((c) => isObj(c) && typeof c.index === "number" && typeof c.label === "string" && isObj(c.editable));
}

export const clampToRange = (value: number, range: [number, number]): number => Math.min(range[1], Math.max(range[0], value));
