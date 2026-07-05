/**
 * Typed API client for the CIDECODE FastAPI backend.
 * Base URL is read from NEXT_PUBLIC_API_URL (default: http://localhost:8000)
 */

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

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

export interface CytoscapeNode {
  data: {
    id: string;
    label: string;
    bank: string;
    risk_score: number;
    risk_tier: string;
    role: string;
    is_seed: boolean;
    is_internal: boolean;
  };
}

export interface CytoscapeEdge {
  data: {
    id: string;
    source: string;
    target: string;
    amount: number;
    dates: string[];
    risk_flag: string;
  };
}

export interface CytoscapeGraph {
  nodes: CytoscapeNode[];
  edges: CytoscapeEdge[];
}

/**
 * Fetch the ledger money trail Cytoscape graph payload for an account.
 */
export async function getLedgerTrace(accountId: string): Promise<CytoscapeGraph> {
  const res = await fetch(`${BASE}/api/v1/graph/ledger-trace/${encodeURIComponent(accountId)}`);
  if (!res.ok) throw new Error(`Failed to fetch ledger trace for account ${accountId}`);
  return res.json();
}

/**
 * Fetch the 1-hop ego-graph expansion for a given account.
 */
export async function getFundTrace(accountId: string, hops: number = 1): Promise<any> {
  const res = await fetch(`${BASE}/api/v1/graph/fund-trace/${encodeURIComponent(accountId)}?hops=${hops}`);
  if (!res.ok) throw new Error(`Failed to fetch fund trace for account ${accountId}`);
  return res.json();
}

/**
 * Fetch the full overview transaction graph (paginated by accounts count).
 */
export async function getFullGraph(limitAccounts: number = 10): Promise<CytoscapeGraph> {
  const res = await fetch(`${BASE}/api/v1/graph/cytoscape-overview?limit_accounts=${limitAccounts}`);
  if (!res.ok) throw new Error("Failed to fetch full overview graph");
  return res.json();
}


export interface Transaction {
  id: string;
  transaction_id?: string;
  account_id: string;
  date: string;
  time: string;
  narration: string;
  channel: string;
  debit: number;
  credit: number;
  balance: number;
  utr_ref?: string;
  counterparty_account_id?: string;
  counterparty_name?: string;
  is_duplicate: boolean;
  is_balance_breach: boolean;
  is_high_value_flag: boolean;
  is_ocr_row: boolean;
  final_risk_score?: number;
}

export interface PaginatedTransactions {
  total: number;
  page: number;
  page_size: number;
  items: Transaction[];
}

/**
 * Fetch transactions for one account.
 */
export async function getAccountTransactions(
  accountId: string,
  page: number = 1,
  pageSize: number = 100
): Promise<PaginatedTransactions> {
  const res = await fetch(
    `${BASE}/api/v1/accounts/${encodeURIComponent(accountId)}/transactions?page=${page}&page_size=${pageSize}`
  );
  if (!res.ok) throw new Error(`Failed to fetch transactions for account ${accountId}`);
  return res.json();
}

export interface SourceCreditAllocation {
  credit_txn_id: string;
  amount: number;
}

export interface SeedCredit {
  txn_id: string;
  account_id: string;
  amount: number;
  timestamp: string;
}

export interface MoneyTrailHop {
  hop_number: number;
  from_account: string;
  from_account_name?: string;
  to_account: string;
  to_account_name?: string;
  debit_txn_id: string;
  amount: number;
  timestamp: string;
  source_credit_txn_ids: string[];
  source_credits: SourceCreditAllocation[];
  is_commingled: boolean;
  is_untracked_remainder: boolean;
  is_cycle: boolean;
  to_account_risk_tier?: string;
  to_account_role?: string;
}

export interface CreditTrailInfo {
  credit_txn_id: string;
  amount: number;
  timestamp: string;
  source_account: string;
  source_account_name?: string;
  hops: MoneyTrailHop[];
}

export interface MoneyTrailResponse {
  credits: CreditTrailInfo[];
}

/**
 * Fetch the FIFO Money Trail flow tracing results for an account and an optional seed credit transaction ID.
 */
export async function getMoneyTrail(
  accountId: string,
  creditTxnId?: string
): Promise<MoneyTrailResponse> {
  const url = new URL(`${BASE}/api/v1/graph/money-trail/${accountId}`);
  if (creditTxnId) {
    url.searchParams.append("credit_txn_id", creditTxnId);
  }
  const res = await fetch(url.toString());
  if (!res.ok) {
    const err = new Error(`Failed to fetch money trail for account ${accountId}`) as any;
    err.status = res.status;
    throw err;
  }
  return res.json();
}


