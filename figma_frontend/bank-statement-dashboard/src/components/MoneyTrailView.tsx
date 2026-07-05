"use client";

import { useEffect, useState } from "react";
import { getAnalyticsStatus, type AnalyticsStatus, type TopAccount } from "@/lib/api";
import MoneyTrailPanel from "./MoneyTrailPanel";

function TierBadge({ tier }: { tier: string }) {
  const map: Record<string, string> = {
    CRITICAL: "bg-red-100 text-red-700",
    HIGH: "bg-orange-100 text-orange-700",
    MEDIUM: "bg-yellow-100 text-yellow-700",
    LOW: "bg-green-100 text-green-700",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
        map[tier] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {tier}
    </span>
  );
}

export default function MoneyTrailView({
  initialAccountId,
  onAccountChange,
}: {
  initialAccountId?: string | null;
  onAccountChange?: (accountId: string) => void;
}) {
  const [analytics, setAnalytics] = useState<AnalyticsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(
    initialAccountId ?? null
  );
  const [searchValue, setSearchValue] = useState("");

  useEffect(() => {
    setLoading(true);
    getAnalyticsStatus()
      .then(setAnalytics)
      .catch(() => setAnalytics({ status: "no_data", message: "Could not load analytics." }))
      .finally(() => setLoading(false));
  }, []);

  // Keep in sync if the caller hands us a new account (e.g. from Graph View)
  useEffect(() => {
    if (initialAccountId) {
      setSelectedAccountId(initialAccountId);
    }
  }, [initialAccountId]);

  // Default to the top risk account once analytics loads, if nothing selected yet
  useEffect(() => {
    if (
      !selectedAccountId &&
      analytics?.status === "ready" &&
      analytics.top_accounts &&
      analytics.top_accounts.length > 0
    ) {
      selectAccount(analytics.top_accounts[0].account_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analytics]);

  const selectAccount = (accountId: string) => {
    setSelectedAccountId(accountId);
    onAccountChange?.(accountId);
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = searchValue.trim();
    if (trimmed) {
      selectAccount(trimmed);
    }
  };

  const selectedMeta: TopAccount | undefined = analytics?.top_accounts?.find(
    (a) => a.account_id === selectedAccountId
  );

  return (
    <div className="w-full max-w-5xl mx-auto flex flex-col gap-6">
      <div className="rounded-xl border border-slate-200 bg-white px-5 py-4">
        <h2 className="text-sm font-semibold text-foreground">Money Trail Investigator</h2>
        <p className="mt-1 text-xs text-slate-500">
          Pick an account below, then drill into each incoming credit to trace where the money went.
        </p>
      </div>

      {loading ? (
        <div className="text-sm text-slate-400 text-center py-6">Loading analytics…</div>
      ) : analytics?.status === "no_data" ? (
        <div className="rounded-xl border border-slate-200 bg-white px-5 py-8 text-center text-sm text-slate-400">
          {analytics.message ?? "No analytics data available yet — upload a statement first."}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-[280px_1fr] gap-4 items-start">
          {/* Left: account picker */}
          <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
            <form onSubmit={handleSearchSubmit} className="flex gap-1.5">
              <input
                value={searchValue}
                onChange={(e) => setSearchValue(e.target.value)}
                placeholder="Enter account ID…"
                className="flex-1 min-w-0 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button
                type="submit"
                className="rounded-md bg-accent px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-blue-600 transition-colors"
              >
                Load
              </button>
            </form>

            <div>
              <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
                Top risk accounts
              </p>
              <div className="space-y-1.5 max-h-[420px] overflow-y-auto pr-1">
                {analytics?.top_accounts && analytics.top_accounts.length > 0 ? (
                  analytics.top_accounts.map((acct) => (
                    <button
                      key={acct.account_id}
                      onClick={() => selectAccount(acct.account_id)}
                      className={`w-full text-left rounded-lg border px-3 py-2 transition-colors ${
                        selectedAccountId === acct.account_id
                          ? "border-accent bg-accent/5"
                          : "border-slate-200 bg-white hover:bg-slate-50"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-[11px] text-slate-700 truncate">
                          {acct.account_id}
                        </span>
                        <TierBadge tier={acct.risk_tier} />
                      </div>
                      <p className="mt-0.5 text-[10px] text-slate-500 truncate">
                        {acct.account_holder}
                      </p>
                    </button>
                  ))
                ) : (
                  <p className="text-[11px] text-slate-400 text-center py-4">
                    No flagged accounts yet.
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Right: drill-down trail panel */}
          <div className="rounded-xl border border-slate-200 bg-white p-4 min-h-[420px]">
            {selectedAccountId ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between border-b border-slate-100 pb-2">
                  <div>
                    <h3 className="font-mono text-sm font-bold text-slate-800 select-all">
                      {selectedAccountId}
                    </h3>
                    {selectedMeta && (
                      <p className="text-xs text-slate-500">{selectedMeta.account_holder}</p>
                    )}
                  </div>
                  {selectedMeta && <TierBadge tier={selectedMeta.risk_tier} />}
                </div>

                <MoneyTrailPanel
                  accountId={selectedAccountId}
                  onReseed={(newAccountId) => selectAccount(newAccountId)}
                />
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-center p-6">
                <div className="text-2xl text-slate-300">💰</div>
                <p className="text-xs text-slate-400 mt-2 font-medium">
                  Select an account to trace its money trail
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
