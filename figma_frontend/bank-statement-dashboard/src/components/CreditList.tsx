"use client";

import { CreditTrailInfo } from "@/lib/api";

interface CreditListProps {
  credits: CreditTrailInfo[];
  onSelectCredit: (credit: CreditTrailInfo) => void;
  formatCurrencyShort: (amount: number) => string;
}

export default function CreditList({
  credits,
  onSelectCredit,
  formatCurrencyShort,
}: CreditListProps) {
  // Sort credits by amount descending
  const sortedCredits = [...credits].sort((a, b) => b.amount - a.amount);

  const formatDate = (isoStr: string) => {
    try {
      const date = new Date(isoStr);
      return date.toLocaleDateString("en-US", { month: "short", day: "2-digit" });
    } catch {
      return "";
    }
  };

  const formatTime = (isoStr: string) => {
    try {
      const date = new Date(isoStr);
      return date.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    } catch {
      return "";
    }
  };

  return (
    <div className="space-y-1.5 max-h-[220px] overflow-y-auto pr-1">
      {sortedCredits.map((credit) => (
        <div
          key={credit.credit_txn_id}
          onClick={() => onSelectCredit(credit)}
          className="group flex flex-col p-2.5 rounded border bg-white border-slate-200 hover:border-accent hover:bg-slate-50 cursor-pointer transition-all duration-150"
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-800 text-[11px]">
              {formatCurrencyShort(credit.amount)}
            </span>
            <span className="text-[10px] text-slate-400 font-medium">
              {formatDate(credit.timestamp)} {formatTime(credit.timestamp)}
            </span>
          </div>
          <div className="flex items-center justify-between mt-1 text-[10px] text-slate-500">
            <span className="truncate max-w-[200px]">
              from{" "}
              <span className="font-mono text-slate-600">
                {credit.source_account_name && credit.source_account_name !== "Unknown"
                  ? credit.source_account_name
                  : credit.source_account}
              </span>
            </span>
            <span className="text-[9px] text-accent font-semibold opacity-0 group-hover:opacity-100 transition-opacity">
              Trace &rarr;
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
