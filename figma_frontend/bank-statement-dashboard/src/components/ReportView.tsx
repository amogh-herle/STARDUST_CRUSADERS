export default function ReportView({ files }: { files: File[] }) {
  return (
    <div className="w-full max-w-2xl mx-auto text-center">
      <h2 className="text-xl font-semibold text-foreground">Report</h2>
      <p className="mt-1 text-sm text-slate-500">
        {files.length} file{files.length !== 1 ? "s" : ""} submitted for analysis
      </p>

      <div className="mt-6 space-y-2 text-left">
        {files.map((f) => (
          <div
            key={f.name}
            className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700"
          >
            {f.name}
          </div>
        ))}
      </div>

      <p className="mt-8 text-xs text-slate-400">
        Analysis pipeline not yet connected — this is a placeholder report.
      </p>
    </div>
  );
}
