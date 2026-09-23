/** Typed client for the jobhunt API.
 *
 * Mirrors the pydantic models in src/jobhunt/review/api.py. Kept hand-written
 * rather than generated: the surface is nine endpoints, and a generator would
 * be more machinery than the thing it manages.
 */

export type Stage =
  | "discovered"
  | "scored"
  | "rejected"
  | "researched"
  | "drafted"
  | "awaiting_review"
  | "approved"
  | "submitted"
  | "abandoned";

export interface Reason {
  component: string;
  points: number;
  detail: string;
  evidence: string[];
}

export interface Match {
  score: number;
  reasons: Reason[];
  matched_skills: string[];
  missing_skills: string[];
  blockers: string[];
}

export interface Brief {
  name: string;
  summary: string;
  facts: Record<string, string>;
  sources: string[];
  confidence: string;
}

export interface Letter {
  body: string;
  evidence_used: string[];
  generator: string;
  edited_by_human: boolean;
  word_count: number;
}

export interface Job {
  id: string;
  source: string;
  title: string;
  company: string;
  description: string;
  url: string;
  location: string | null;
  remote: boolean | null;
  salary_min: number | null;
  salary_max: number | null;
  currency: string;
  contract_type: string | null;
  posted_at: string | null;
}

export interface Application {
  job: Job;
  stage: Stage;
  match: Match | null;
  brief: Brief | null;
  letter: Letter | null;
  notes: string;
  notified_at: string | null;
  submitted_at: string | null;
}

export interface Stats {
  counts: Record<string, number>;
  awaiting: number;
  approved: number;
  submitted: number;
  rejected: number;
  notified_today: number;
  average_score: number | null;
}

export interface RunState {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  summary: string | null;
  errors: string[];
}

export interface Meta {
  banned_phrases: string[];
  notify_threshold: number;
  weights: Record<string, number>;
  cv_path: string;
  letters_backend: string;
  sources: string[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!response.ok) {
    // FastAPI puts the useful message in `detail`. Surfacing it verbatim means
    // a 409 from the human gate reads as the sentence the backend wrote.
    let message = response.statusText;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* non-JSON error body: keep the status text */
    }
    throw new ApiError(message, response.status);
  }

  return response.status === 204 ? (undefined as T) : response.json();
}

export const api = {
  stats: () => request<Stats>("/stats"),
  meta: () => request<Meta>("/meta"),

  list: (stage?: Stage, limit = 100) =>
    request<Application[]>(
      `/applications?limit=${limit}${stage ? `&stage=${stage}` : ""}`,
    ),

  get: (id: string) => request<Application>(`/applications/${id}`),

  saveLetter: (id: string, body: string) =>
    request<Application>(`/applications/${id}/letter`, {
      method: "PUT",
      body: JSON.stringify({ body }),
    }),

  approve: (id: string) =>
    request<Application>(`/applications/${id}/approve`, { method: "POST" }),

  skip: (id: string, reason = "") =>
    request<Application>(`/applications/${id}/skip`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),

  markSubmitted: (id: string) =>
    request<Application>(`/applications/${id}/mark-submitted`, { method: "POST" }),

  assist: (id: string) =>
    request<Application>(`/applications/${id}/assist`, { method: "POST" }),

  runStatus: () => request<RunState>("/run"),
  startRun: (dryRun = false) =>
    request<RunState>(`/run?dry_run=${dryRun}`, { method: "POST" }),
};

/** Money, the way a job advert writes it. */
export function formatSalary(job: Job): string | null {
  const fmt = (n: number) =>
    n >= 1000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`;
  if (job.salary_min && job.salary_max)
    return `£${fmt(job.salary_min)} - £${fmt(job.salary_max)}`;
  if (job.salary_max) return `up to £${fmt(job.salary_max)}`;
  if (job.salary_min) return `from £${fmt(job.salary_min)}`;
  return null;
}

export function timeAgo(iso: string | null): string | null {
  if (!iso) return null;
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}
