"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { View } from "./Sidebar";

const TITLES: Record<View, string> = {
  upload: "Upload bank statements",
  reports: "Reports",
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

export default function Topbar({ view }: { view: View }) {
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const [user, setUser] = useState<any>(null);
  const [cases, setCases] = useState<Case[]>([]);
  const [loadingCases, setLoadingCases] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const supabase = createClient();

  useEffect(() => {
    // Get current user
    supabase.auth.getUser().then(({ data }) => {
      setUser(data.user);
    });

    // Close menu when clicking outside
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowProfileMenu(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [supabase]);

  useEffect(() => {
    if (showProfileMenu && user && cases.length === 0) {
      loadCases();
    }
  }, [showProfileMenu, user]);

  const loadCases = async () => {
    setLoadingCases(true);
    try {
      const { data, error } = await supabase
        .from("cases")
        .select("*")
        .order("created_at", { ascending: false })
        .limit(10);

      if (error) throw error;
      setCases(data || []);
    } catch (error) {
      console.error("Error loading cases:", error);
    } finally {
      setLoadingCases(false);
    }
  };

  const handleLogout = async () => {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case "critical":
        return "text-red-600 bg-red-50 border-red-200";
      case "high":
        return "text-orange-600 bg-orange-50 border-orange-200";
      case "medium":
        return "text-yellow-600 bg-yellow-50 border-yellow-200";
      case "low":
        return "text-green-600 bg-green-50 border-green-200";
      default:
        return "text-gray-600 bg-gray-50 border-gray-200";
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "open":
        return "text-blue-600";
      case "in_progress":
        return "text-purple-600";
      case "closed":
        return "text-gray-600";
      case "archived":
        return "text-gray-400";
      default:
        return "text-gray-600";
    }
  };

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6">
      <div>
        <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
          CIDECODE 2026
        </p>
        <h1 className="text-sm font-semibold text-foreground">{TITLES[view]}</h1>
      </div>

      <div className="flex items-center gap-3">
        <button className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-600">
          New case
        </button>

        {/* Profile Menu */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setShowProfileMenu(!showProfileMenu)}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-white text-sm font-medium hover:bg-blue-600 transition-colors"
            title="Profile"
          >
            {user?.email?.[0]?.toUpperCase() || "U"}
          </button>

          {showProfileMenu && (
            <div className="absolute right-0 mt-2 w-80 rounded-lg border border-gray-200 bg-white shadow-lg z-50">
              {/* User Info */}
              <div className="border-b border-gray-200 p-4">
                <p className="text-sm font-semibold text-gray-900">
                  {user?.user_metadata?.full_name || user?.email}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">{user?.email}</p>
              </div>

              {/* Cases Section */}
              <div className="p-3">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                  My Cases ({cases.length})
                </h3>

                {loadingCases ? (
                  <p className="text-xs text-gray-400 text-center py-4">Loading...</p>
                ) : cases.length === 0 ? (
                  <p className="text-xs text-gray-400 text-center py-4">
                    No cases yet. Create one to get started.
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
                            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase ${getPriorityColor(c.priority)}`}
                          >
                            {c.priority}
                          </span>
                        </div>
                        <div className="mt-1.5 flex items-center gap-2">
                          <span className={`text-[10px] font-medium uppercase ${getStatusColor(c.status)}`}>
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

              {/* Actions */}
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
  );
}