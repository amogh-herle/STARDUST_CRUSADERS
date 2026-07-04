/**
 * Typed API client for the CIDECODE FastAPI backend.
 * Base URL is read from NEXT_PUBLIC_API_URL (default: http://localhost:8000)
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Types ──────────────────────────────────────────────────────────────────

export interface UploadResult {
  upload_id: string;
  files_received: number;
  files_ingested: number;
  rows_parsed: number;
  rows_after_clean: number;
  banks_detected: string[];
  warnings: string[];
  status: "success" | "partial" | "failed";
}

export interface TopAccount {
  account_id: string;
  account_holder: string;
  risk_score: string;
  risk_tier: string;
  active_patterns: string;
}

export interface AnalyticsStatus {
  status: "ready" | "no_data";
  message?: string;
  run_timestamp?: string;
  accounts?: number;
  critical_accounts?: number;
  high_accounts?: number;
  medium_accounts?: number;
  round_trips?: number;
  layering_chains?: number;
  fan_in?: number;
  fan_out?: number;
  smurfing?: number;
  odd_hours?: number;
  communities?: number;
  top_accounts?: TopAccount[];
}

export interface ChatResponse {
  answer: string;
  sources: string[];
}

// ── Endpoints ──────────────────────────────────────────────────────────────

/**
 * Upload one or more statement files and run the full pipeline (phase6 → 7 → 8).
 */
export async function uploadStatements(files: File[]): Promise<UploadResult> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));

  const res = await fetch(`${BASE}/api/v1/upload/`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Upload failed");
  }
  return res.json();
}

/**
 * Fetch a summary of the latest Phase 8 analytics run.
 */
export async function getAnalyticsStatus(): Promise<AnalyticsStatus> {
  const res = await fetch(`${BASE}/api/v1/upload/analytics-status`);
  if (!res.ok) throw new Error("Failed to fetch analytics status");
  return res.json();
}

/**
 * Send a question to the Gemini-powered AML assistant.
 */
export async function chat(
  question: string,
  accountId?: string,
  communityId?: string
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/api/v1/assistant/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      account_id: accountId ?? null,
      community_id: communityId ?? null,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `API error: ${res.status}`);
  }
  return res.json();
}
