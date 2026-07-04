import { View } from "./Sidebar";

const TITLES: Record<View, string> = {
  upload: "Upload bank statements",
  reports: "Reports",
  library: "Library",
};

export default function Topbar({ view }: { view: View }) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6">
      <div>
        <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
          CIDECODE 2026
        </p>
        <h1 className="text-sm font-semibold text-foreground">{TITLES[view]}</h1>
      </div>
      <button className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-600">
        New case
      </button>
    </header>
  );
}