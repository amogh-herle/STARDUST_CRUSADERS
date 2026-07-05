"use client";

import { useEffect, useState } from "react";
import { getMoneyTrail, type MoneyTrailResponse, type CreditTrailInfo } from "@/lib/api";
import CreditList from "./CreditList";
import CreditTrail from "./CreditTrail";

interface MoneyTrailPanelProps {
  accountId: string;
  onHighlightNode?: (nodeId: string) => void;
  onReseed?: (accountId: string) => void;
}

export default function MoneyTrailPanel({
  accountId,
  onHighlightNode,
  onReseed,
}: MoneyTrailPanelProps) {
  const [trail, setTrail] = useState<MoneyTrailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isEmpty, setIsEmpty] = useState(false);

  // Drill-down states
  const [view, setView] = useState<"credits" | "trail">("credits");
  const [selectedCredit, setSelectedCredit] = useState<CreditTrailInfo | null>(null);

  useEffect(() => {
    if (!accountId) return;
    setLoading(true);
    setError(null);
    setTrail(null);
    setIsEmpty(false);
    setView("credits");
    setSelectedCredit(null);

    getMoneyTrail(accountId)
      .then((data) => {
        setTrail(data);
        setIsEmpty(!data.credits || data.credits.length === 0);
      })
      .catch((err) => {
        console.error("Failed to load money trail in panel:", err);
        if (err.status === 404 || /no transaction|no credit/i.test(err.message || "")) {
          setTrail(null);
          setError(null);
          setIsEmpty(true);
        } else {
          setError("Couldn't load trail data");
          setIsEmpty(false);
        }
      })
      .finally(() => {
        setLoading(false);
      });
  }, [accountId]);

  const formatCurrencyShort = (amount: number) => {
    if (amount >= 10000000) return `₹${(amount / 10000000).toFixed(1)}Cr`;
    if (amount >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
    if (amount >= 1000) return `₹${(amount / 1000).toFixed(1)}K`;
    return `₹${amount}`;
  };

  const handleSelectCredit = (credit: CreditTrailInfo) => {
    setSelectedCredit(credit);
    setView("trail");
  };

  const handleBackToCredits = () => {
    setSelectedCredit(null);
    setView("credits");
  };

  const handleReseed = (newAccountId: string) => {
    if (onReseed) {
      onReseed(newAccountId);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
          Money Trail flow
        </span>
      </div>

      {loading && (
        <div className="text-center py-4 flex items-center justify-center gap-1.5 text-xs text-slate-400">
          <div className="animate-spin rounded-full h-3 w-3 border border-blue-500 border-t-transparent" />
          <span>Tracing propagation...</span>
        </div>
      )}

      {error && <p className="text-[10px] text-red-500 font-medium py-1">{error}</p>}

      {!loading && !error && isEmpty && (
        <p className="text-[11px] text-slate-400 text-center py-4 bg-white border border-dashed border-slate-200 rounded-lg">
          No propagation flows — this account has no transactions to trace.
        </p>
      )}

      {!loading && !error && !isEmpty && trail && (
        <>
          {view === "credits" && (
            <CreditList
              credits={trail.credits}
              onSelectCredit={handleSelectCredit}
              formatCurrencyShort={formatCurrencyShort}
            />
          )}
          {view === "trail" && selectedCredit && (
            <CreditTrail
              credit={selectedCredit}
              onBack={handleBackToCredits}
              onReseed={handleReseed}
              onHighlightNode={onHighlightNode}
              formatCurrencyShort={formatCurrencyShort}
            />
          )}
        </>
      )}
    </div>
  );
}
