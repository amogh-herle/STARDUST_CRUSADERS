"use client";

import { useState } from "react";

export type View = "upload" | "reports" | "graph" | "library";

const NAV: { id: View; label: string; icon: string }[] = [
  { id: "upload", label: "Upload", icon: "⬆" },
  { id: "reports", label: "Reports", icon: "▤" },
  { id: "graph", label: "Graph View", icon: "🕸" },
  { id: "library", label: "Library", icon: "▥" },
];

export default function Sidebar({
  view,
  onChange,
}: {
  view: View;
  onChange: (v: View) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className={`flex shrink-0 flex-col gap-1 bg-sidebar px-3 py-4 transition-all duration-200 ${
        collapsed ? "w-16" : "w-56"
      }`}
    >
      <button
        onClick={() => setCollapsed((c) => !c)}
        aria-label="Toggle sidebar"
        className="mb-4 flex h-9 w-9 items-center justify-center rounded-md text-white/90 hover:bg-white/15"
      >
        <span className="text-lg leading-none">☰</span>
      </button>

      {NAV.map((item) => (
        <button
          key={item.id}
          onClick={() => onChange(item.id)}
          title={collapsed ? item.label : undefined}
          className={`flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors ${
            view === item.id
              ? "bg-white text-sidebar font-medium"
              : "text-white/85 hover:bg-white/15"
          }`}
        >
          <span className="w-4 text-center">{item.icon}</span>
          {!collapsed && <span>{item.label}</span>}
        </button>
      ))}
    </aside>
  );
}