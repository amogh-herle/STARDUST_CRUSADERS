"use client";

import { CreditTrailInfo, MoneyTrailHop } from "@/lib/api";

interface CreditTrailProps {
  credit: CreditTrailInfo;
  onBack: () => void;
  onReseed: (accountId: string) => void;
  onHighlightNode?: (nodeId: string) => void;
  formatCurrencyShort: (amount: number) => string;
}

export default function CreditTrail({
  credit,
  onBack,
  onReseed,
  onHighlightNode,
  formatCurrencyShort,
}: CreditTrailProps) {
  const hops = credit.hops;

  // Depletion math
  const totalTraced = hops.reduce((acc, h) => acc + h.amount, 0);
  const rawPct = credit.amount > 0 ? (totalTraced / credit.amount) * 100 : 0;
  const pct = Math.min(100, Math.max(0, rawPct));

  // Risk tier color helper
  const getRiskColor = (tier?: string) => {
    switch (tier?.toUpperCase()) {
      case "CRITICAL":
        return "text-red-600 bg-red-50 border-red-200";
      case "HIGH":
        return "text-orange-600 bg-orange-50 border-orange-200";
      case "MEDIUM":
        return "text-yellow-600 bg-yellow-50 border-yellow-200";
      case "LOW":
        return "text-green-600 bg-green-50 border-green-200";
      default:
        return "text-slate-500 bg-slate-50 border-slate-200";
    }
  };

  return (
    <div className="space-y-3">
      {/* Navigation Header */}
      <div className="flex items-center justify-between pb-1.5 border-b border-slate-100">
        <button
          onClick={onBack}
          className="text-[10px] text-accent font-semibold hover:underline flex items-center gap-1"
        >
          &larr; Back to credits
        </button>
        <span className="text-[10px] text-slate-500 truncate max-w-[180px] font-mono">
          {formatCurrencyShort(credit.amount)} from{" "}
          {credit.source_account_name && credit.source_account_name !== "Unknown"
            ? credit.source_account_name
            : credit.source_account}
        </span>
      </div>

      {/* Depletion Progress Bar */}
      <div className="space-y-1 bg-slate-50 p-2 rounded border border-slate-150">
        <div className="flex justify-between text-[10px] text-slate-500">
          <span>Traced: <strong className="text-slate-700 font-semibold">{formatCurrencyShort(totalTraced)}</strong> ({rawPct.toFixed(1)}%)</span>
        </div>
        <div className="w-full bg-slate-200 rounded-full h-1.5 overflow-hidden">
          <div
            className="bg-accent h-1.5 rounded-full transition-all duration-350"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Hop List */}
      <div className="max-h-[170px] overflow-y-auto space-y-1.5 pr-1">
        {hops.length === 0 ? (
          <p className="text-[10px] text-slate-400 text-center py-4">
            No propagation hops traced for this credit.
          </p>
        ) : (
          hops.map((hop, index) => {
            const hasBadge = hop.is_commingled || hop.is_cycle || hop.is_untracked_remainder;
            const hasRiskInfo = hop.to_account_risk_tier && hop.to_account_risk_tier !== "UNKNOWN";

            return (
              <div
                key={index}
                onClick={() => onHighlightNode && onHighlightNode(hop.to_account)}
                className="group flex flex-col p-2 rounded text-[11px] border bg-white border-slate-200 hover:border-slate-300 hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="font-semibold text-slate-400 shrink-0">
                      Hop {hop.hop_number}
                    </span>
                    <span className="text-slate-800 font-medium truncate font-mono">
                      &rarr;{" "}
                      {hop.to_account_name && hop.to_account_name !== "Unknown"
                        ? hop.to_account_name
                        : hop.to_account}
                    </span>
                  </div>
                  <span className="font-mono font-bold text-slate-700 shrink-0">
                    {formatCurrencyShort(hop.amount)}
                  </span>
                </div>

                {/* Risk and Role Badge */}
                {hasRiskInfo && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    <span className={`text-[8px] px-1 rounded font-bold uppercase border ${getRiskColor(hop.to_account_risk_tier)}`}>
                      {hop.to_account_risk_tier} &middot; {hop.to_account_role}
                    </span>
                  </div>
                )}

                {/* Flags and Actions */}
                <div className="mt-1.5 flex items-center justify-between">
                  <div className="flex gap-1">
                    {hop.is_commingled && (
                      <span className="bg-yellow-50 text-yellow-700 border border-yellow-250 text-[8px] px-1 rounded font-bold uppercase" title="Commingled funds">
                        🔀 commingled
                      </span>
                    )}
                    {hop.is_cycle && (
                      <span className="bg-red-50 text-red-750 border border-red-250 text-[8px] px-1 rounded font-bold uppercase" title="Cyclic path detected">
                        🔄 cycle
                      </span>
                    )}
                    {hop.is_untracked_remainder && (
                      <span className="bg-orange-50 text-orange-750 border border-orange-250 text-[8px] px-1 rounded font-bold uppercase" title="Untracked remainder">
                        ⚠️ untracked
                      </span>
                    )}
                  </div>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onReseed(hop.to_account);
                    }}
                    className="text-[9px] text-accent font-semibold hover:underline opacity-0 group-hover:opacity-100 transition-opacity self-end"
                  >
                    [→ trace this account]
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
