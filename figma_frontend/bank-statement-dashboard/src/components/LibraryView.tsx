"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { getSession } from "@/lib/auth";
import { type Case } from "./Topbar";

const PRIORITY_COLORS: Record<string, string> = {
  critical: "text-red-600 bg-red-50 border-red-200",
  high: "text-orange-600 bg-orange-50 border-orange-200",
  medium: "text-yellow-600 bg-yellow-50 border-yellow-200",
  low: "text-green-600 bg-green-50 border-green-200",
};

const STATUS_COLORS: Record<string, string> = {
  open: "text-blue-600 bg-blue-50 border-blue-200",
  in_progress: "text-purple-600 bg-purple-50 border-purple-200",
  closed: "text-gray-600 bg-gray-50 border-gray-200",
  archived: "text-gray-400 bg-gray-50 border-gray-100",
};

export default function LibraryView({
  onOpenCase,
}: {
  onOpenCase: (c: Case, uploadedFiles: string[], uploadId?: string | null) => void;
}) {
  const [cases, setCases] = useState<Case[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const supabase = createClient();
  const user = getSession();

  const loadLibrary = async () => {
    if (!user) return;
    setLoading(true);
    setError(null);
    try {
      const { data: casesData, error: casesError } = await supabase
        .from("cases")
        .select("*")
        .eq("created_by", user.id)
        .order("created_at", { ascending: false });

      if (casesError) throw casesError;

      setCases(casesData || []);
    } catch (err) {
      console.error("Error loading library:", err);
      setError(err instanceof Error ? err.message : "Failed to load library");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLibrary();
  }, []);

  const handleDeleteCase = async (id: string) => {
    if (
      !confirm(
        "Are you sure you want to delete this case? All associated files and data will be unlinked from it."
      )
    ) {
      return;
    }
    try {
      const { error } = await supabase.from("cases").delete().eq("id", id);
      if (error) throw error;
      loadLibrary();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete case");
    }
  };

  if (loading) {
    return (
      <div className="w-full max-w-4xl mx-auto text-center py-12">
        <svg
          className="h-8 w-8 animate-spin text-accent mx-auto shrink-0 mb-4"
          viewBox="0 0 24 24"
          fill="none"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
          />
        </svg>
        <p className="text-sm text-slate-400">Loading cases from your library...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full max-w-4xl mx-auto text-center py-12">
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 max-w-md mx-auto">
          <p className="text-sm font-semibold text-red-800">Error loading library</p>
          <p className="text-xs text-red-600 mt-1">{error}</p>
          <button
            onClick={loadLibrary}
            className="mt-4 rounded-lg bg-red-600 px-4 py-2 text-xs font-semibold text-white hover:bg-red-700 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between border-b border-slate-200 pb-4">
        <div>
          <h2 className="text-xl font-bold text-slate-800">Case Library</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Manage your past forensic investigations and linked bank statements.
          </p>
        </div>
        <button
          onClick={loadLibrary}
          className="rounded-lg border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
        >
          Refresh
        </button>
      </div>

      {cases.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white p-12 text-center">
          <span className="text-3xl">▥</span>
          <h3 className="mt-3 text-sm font-semibold text-slate-700">No cases found</h3>
          <p className="mt-1 text-xs text-slate-500 max-w-sm mx-auto">
            You haven't created any cases yet. Go to the Upload tab to start a new forensic investigation.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {cases.map((c) => {
            const caseUploads = c.uploaded_files || [];
            return (
              <div
                key={c.id}
                className="flex flex-col justify-between rounded-xl border border-slate-200 bg-white p-5 shadow-sm hover:shadow-md transition-shadow"
              >
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[10px] text-slate-400">
                      #{c.case_number}
                    </span>
                    <div className="flex gap-1.5">
                      <span
                        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                          PRIORITY_COLORS[c.priority] ?? ""
                        }`}
                      >
                        {c.priority}
                      </span>
                      <span
                        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                          STATUS_COLORS[c.status] ?? ""
                        }`}
                      >
                        {c.status.replace("_", " ")}
                      </span>
                    </div>
                  </div>

                  <h3 className="mt-2 text-sm font-bold text-slate-800 line-clamp-1">
                    {c.case_name}
                  </h3>

                  {c.description && (
                    <p className="mt-1 text-xs text-slate-500 line-clamp-2">
                      {c.description}
                    </p>
                  )}

                  <div className="mt-4 border-t border-slate-100 pt-3">
                    <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
                      Statements ({caseUploads.length})
                    </p>
                    {caseUploads.length === 0 ? (
                      <p className="text-xs italic text-slate-400">No statements uploaded yet.</p>
                    ) : (
                      <div className="max-h-24 overflow-y-auto space-y-1.5 pr-1">
                        {caseUploads.map((fileName, idx) => (
                          <div
                            key={idx}
                            className="flex items-center gap-2 rounded bg-slate-50 px-2 py-1 text-xs text-slate-600 border border-slate-100"
                          >
                            <span className="text-[11px]">📄</span>
                            <span className="truncate font-medium flex-1">{fileName}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                <div className="mt-5 border-t border-slate-100 pt-3 flex items-center justify-between gap-4">
                  <span className="text-[10px] text-slate-400">
                    Created {new Date(c.created_at).toLocaleDateString()}
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleDeleteCase(c.id)}
                      className="rounded-lg border border-red-200 bg-white p-2 text-xs font-medium text-red-600 hover:bg-red-50 transition-colors"
                      title="Delete case"
                    >
                      🗑
                    </button>
                    <button
                      onClick={() => onOpenCase(c, caseUploads, c.upload_id)}
                      className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white hover:bg-blue-600 transition-colors"
                    >
                      Open Case
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
