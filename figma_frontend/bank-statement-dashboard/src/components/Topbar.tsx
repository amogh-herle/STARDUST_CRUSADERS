"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { getSession, logout, type AuthUser } from "@/lib/auth";
import { View } from "./Sidebar";

const TITLES: Record<View, string> = {
  upload: "Upload bank statements",
  reports: "Reports",
  graph: "Graph View",
  library: "Library",
};

type Case = {
  id: string;
  case_name: string;
  case_number: string;
  status: string;
  priority: string;
  created_at: string;
};

const PRIORITY_OPTIONS = ["low", "medium", "high", "critical"] as const;

const PRIORITY_COLORS: Record<string, string> = {
  critical: "text-red-600 bg-red-50 border-red-200",
  high: "text-orange-600 bg-orange-50 border-orange-200",
  medium: "text-yellow-600 bg-yellow-50 border-yellow-200",
  low: "text-green-600 bg-green-50 border-green-200",
};

const STATUS_COLORS: Record<string, string> = {
  open: "text-blue-600",
  in_progress: "text-purple-600",
  closed: "text-gray-600",
  archived: "text-gray-400",
};

// ─── New Case Modal ───────────────────────────────────────────────────────────
function NewCaseModal({
  userId,
  onClose,
  onCreated,
}: {
  userId: string;
  onClose: () => void;
  onCreated: (c: Case) => void;
}) {
  const [caseName, setCaseName] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<"low" | "medium" | "high" | "critical">("medium");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const supabase = createClient();

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!caseName.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const today = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      const suffix = Math.random().toString(36).slice(2, 6).toUpperCase();
      const caseNumber = `CASE-${today}-${suffix}`;

      const { data, error: insertError } = await supabase
        .from("cases")
        .insert({
          case_name: caseName.trim(),
          case_number: caseNumber,
          description: description.trim() || null,
          priority,
          status: "open",
          created_by: userId,
        })
        .select()
        .single();

      if (insertError) throw insertError;

      onCreated(data as Case);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create case");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white shadow-xl">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-sm font-semibold text-gray-900">New Case</h2>
          <p className="mt-0.5 text-xs text-gray-500">
            Create an investigation case to organise bank statement analysis
          </p>
        </div>

        <form onSubmit={handleCreate} className="px-6 py-4 space-y-4">
          <div>
            <label htmlFor="case-name" className="mb-1.5 block text-xs font-medium text-gray-700">
              Case name <span className="text-red-500">*</span>
            </label>
            <input
              id="case-name"
              type="text"
              value={caseName}
              onChange={(e) => setCaseName(e.target.value)}
              required
              autoFocus
              maxLength={200}
              placeholder="e.g. Operation Sandstorm — HDFC Account Cluster"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-700">Priority</label>
            <div className="flex gap-2">
              {PRIORITY_OPTIONS.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPriority(p)}
                  className={`flex-1 rounded-md border px-2 py-1.5 text-[11px] font-medium uppercase transition-colors ${
                    priority === p
                      ? PRIORITY_COLORS[p]
                      : "border-gray-200 text-gray-500 hover:bg-gray-50"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label htmlFor="case-desc" className="mb-1.5 block text-xs font-medium text-gray-700">
              Description <span className="font-normal text-gray-400">(optional)</span>
            </label>
            <textarea
              id="case-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              maxLength={1000}
              placeholder="Brief summary of the investigation..."
              className="w-full resize-none rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
            />
          </div>

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-gray-300 px-4 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !caseName.trim()}
              className="rounded-md bg-accent px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "Creating..." : "Create Case"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Topbar ───────────────────────────────────────────────────────────────────
export default function Topbar({ view }: { view: View }) {
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const [showNewCase, setShowNewCase] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [cases, setCases] = useState<Case[]>([]);
  const [loadingCases, setLoadingCases] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const supabase = createClient();

  useEffect(() => {
    setUser(getSession());

    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowProfileMenu(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    if (showProfileMenu && user) {
      loadCases();
    }
  }, [showProfileMenu, user]);

  const loadCases = async () => {
    if (!user) return;
    setLoadingCases(true);
    try {
      const { data, error } = await supabase
        .from("cases")
        .select("*")
        .eq("created_by", user.id)
        .order("created_at", { ascending: false })
        .limit(10);

      if (error) throw error;
      setCases(data || []);
    } catch (err) {
      console.error("Error loading cases:", err);
    } finally {
      setLoadingCases(false);
    }
  };

  const handleLogout = () => {
    logout();
    router.push("/login");
    router.refresh();
  };

  const handleCaseCreated = (newCase: Case) => {
    setCases((prev) => [newCase, ...prev]);
  };

  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
            CIDECODE 2026
          </p>
          <h1 className="text-sm font-semibold text-foreground">{TITLES[view]}</h1>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowNewCase(true)}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-600 transition-colors"
          >
            New case
          </button>

          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setShowProfileMenu(!showProfileMenu)}
              className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-white text-sm font-medium hover:bg-blue-600 transition-colors"
              title={user?.username || "Profile"}
              aria-label="Open profile menu"
            >
              {user?.username?.[0]?.toUpperCase() || "U"}
            </button>

            {showProfileMenu && (
              <div className="absolute right-0 mt-2 w-80 rounded-lg border border-gray-200 bg-white shadow-lg z-50">
                <div className="border-b border-gray-200 p-4">
                  <p className="text-sm font-semibold text-gray-900">
                    {user?.full_name || user?.username}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">@{user?.username}</p>
                </div>

                <div className="p-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                    My Cases ({cases.length})
                  </h3>

                  {loadingCases ? (
                    <p className="text-xs text-gray-400 text-center py-4">Loading...</p>
                  ) : cases.length === 0 ? (
                    <p className="text-xs text-gray-400 text-center py-4">
                      No cases yet.{" "}
                      <button
                        onClick={() => {
                          setShowProfileMenu(false);
                          setShowNewCase(true);
                        }}
                        className="text-accent hover:underline"
                      >
                        Create one
                      </button>{" "}
                      to get started.
                    </p>
                  ) : (
                    <div className="max-h-64 overflow-y-auto space-y-2">
                      {cases.map((c) => (
                        <div
                          key={c.id}
                          className="rounded-md border border-gray-200 p-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-medium text-gray-900 truncate">
                                {c.case_name}
                              </p>
                              <p className="text-xs text-gray-500 mt-0.5">
                                #{c.case_number}
                              </p>
                            </div>
                            <span
                              className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase ${
                                PRIORITY_COLORS[c.priority] ?? "text-gray-600 bg-gray-50 border-gray-200"
                              }`}
                            >
                              {c.priority}
                            </span>
                          </div>
                          <div className="mt-1.5 flex items-center gap-2">
                            <span
                              className={`text-[10px] font-medium uppercase ${
                                STATUS_COLORS[c.status] ?? "text-gray-600"
                              }`}
                            >
                              {c.status.replace("_", " ")}
                            </span>
                            <span className="text-[10px] text-gray-400">
                              {new Date(c.created_at).toLocaleDateString()}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="border-t border-gray-200 p-2">
                  <button
                    onClick={handleLogout}
                    className="w-full rounded-md px-3 py-2 text-left text-xs font-medium text-red-600 hover:bg-red-50 transition-colors"
                  >
                    Logout
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      {showNewCase && user && (
        <NewCaseModal
          userId={user.id}
          onClose={() => setShowNewCase(false)}
          onCreated={handleCaseCreated}
        />
      )}
    </>
  );
}
