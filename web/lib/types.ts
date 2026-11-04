/**
 * TypeScript mirrors of the Django API's response dicts (src/atelier/api/core/services.py).
 * Field names and optionality here must match that file exactly, not an idealized shape.
 */

export type MediaType = "image" | "video" | "audio" | "document" | "text";

export interface Asset {
  handle: string;
  media: MediaType;
  ext: string;
  size: number;
  filename: string;
  created_at: string;
}

export interface AssetDetail extends Asset {
  description: string;
  meta: Record<string, unknown>;
}

export interface AssetList {
  count: number;
  items: Asset[];
}

export type JobKind = "spec" | "prompt";

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "checks_failed"
  | "needs_input"
  | "failed"
  | "cancelled";

export const TERMINAL_STATUSES: JobStatus[] = [
  "succeeded",
  "checks_failed",
  "needs_input",
  "failed",
  "cancelled",
];

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export type CheckStatus = "pass" | "warn" | "fail";

export interface Check {
  name: string;
  status: CheckStatus;
  message: string;
  hint: string | null;
}

export interface StepReport {
  id: string;
  op: string;
  cached: boolean;
  seconds: number;
  fused: string[];
}

export interface JobOutput {
  handle: string;
  media: MediaType;
  ext: string;
  size: number;
  summary: string;
}

export interface JobResult {
  outputs?: Record<string, JobOutput>;
  checks?: Record<string, Check[]>;
  steps?: StepReport[];
  passed?: boolean;
  seconds?: number;
  /** Prompt (agent) jobs only. */
  summary?: string;
  warnings?: string[];
  /** Set when status is "needs_input": the agent's clarifying question. */
  question?: string;
}

export interface Job {
  id: string;
  kind: JobKind;
  status: JobStatus;
  prompt: string;
  assets: string[];
  recipe: string | null;
  /** The job this one follows up on ("refine this"), if any. */
  parent?: string | null;
  result: JobResult | null;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  units: number;
  compute_seconds: number;
  /** Present when kind === "spec", or once the job has finished (any kind). */
  spec?: Record<string, unknown>;
  /** Present on GET /jobs/{id}; absent from list rows. */
  events?: JobEvent[];
}

export interface JobList {
  count: number;
  items: Job[];
}

export type JobEventKind =
  | "started"
  | "plan"
  | "step_start"
  | "step_done"
  | "turn"
  | "tools"
  | "finished"
  | (string & {});

export interface JobEvent {
  seq: number;
  kind: JobEventKind;
  data: Record<string, unknown>;
  at: string;
}

export interface EventsPoll {
  status: JobStatus;
  finished: boolean;
  events: JobEvent[];
}

export interface Recipe {
  id: number;
  name: string;
  description: string;
  version: number;
  /** Every value is an asset handle; a recipe has no scalar parameters. */
  inputs: Record<string, string>;
  created_at: string;
}

export interface RecipeList {
  items: Recipe[];
}

export interface Me {
  org: { slug: string; name: string };
  key: { name: string; prefix: string };
}

export interface Usage {
  period_start: string;
  jobs: number;
  units: number;
  compute_seconds: number;
  units_limit: number | null;
  compute_seconds_limit: number | null;
}

export interface BrandKit {
  name?: string;
  tagline?: string;
  colors?: Record<string, string>;
  fonts?: Record<string, string>;
  rules?: string[];
  logo?: string;
  logo_dark?: string;
}

export interface ValidateOk {
  valid: true;
  steps: number;
  runs: number;
  cached: number;
  plan: string;
}

export interface ValidateErr {
  valid: false;
  errors: string[];
}

export type ValidateResult = ValidateOk | ValidateErr;

export interface OpsCatalogue {
  catalogue: string;
}

export interface OpDetail {
  name: string;
  description: string;
}
