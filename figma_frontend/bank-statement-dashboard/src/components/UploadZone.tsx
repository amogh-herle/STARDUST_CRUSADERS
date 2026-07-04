"use client";

import { useCallback, useRef, useState } from "react";

const ACCEPTED_EXTENSIONS = [".pdf", ".csv", ".xlsx", ".xls", ".docx", ".png", ".jpg", ".jpeg"];

type QueuedFile = {
  file: File;
  error?: string;
};

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isAccepted(file: File) {
  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  return ACCEPTED_EXTENSIONS.includes(ext);
}

export default function UploadZone({ onSubmit }: { onSubmit: (files: File[]) => void }) {
  const [isDragging, setIsDragging] = useState(false);
  const [files, setFiles] = useState<QueuedFile[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    const next: QueuedFile[] = Array.from(incoming).map((file) => ({
      file,
      error: isAccepted(file) ? undefined : "Unsupported file type",
    }));
    setFiles((prev) => [...prev, ...next]);
  }, []);

  const removeFile = (name: string) => {
    setFiles((prev) => prev.filter((f) => f.file.name !== name));
  };

  const validFiles = files.filter((f) => !f.error);

  return (
    <div className="w-full max-w-2xl mx-auto">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          addFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-xl border-2 border-dashed p-12 text-center transition-colors ${
          isDragging ? "border-accent bg-accent/5" : "border-slate-300 bg-white hover:border-slate-400"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          onChange={(e) => addFiles(e.target.files)}
        />
        <p className="text-sm font-medium text-slate-700">
          Drop bank statements here or click to browse
        </p>
        <p className="mt-1 text-xs text-slate-400">PDF, CSV, XLSX, DOCX, PNG, JPG</p>
      </div>

      {files.length > 0 && (
        <div className="mt-6 space-y-2">
          {files.map((qf) => (
            <div
              key={qf.file.name}
              className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-slate-700">{qf.file.name}</p>
                <p className="text-xs text-slate-400">
                  {formatSize(qf.file.size)}
                  {qf.error ? ` · ${qf.error}` : ""}
                </p>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeFile(qf.file.name);
                }}
                className="ml-4 text-slate-400 hover:text-slate-600"
                aria-label={`Remove ${qf.file.name}`}
              >
                ✕
              </button>
            </div>
          ))}

          <button
            onClick={() => onSubmit(validFiles.map((f) => f.file))}
            disabled={validFiles.length === 0}
            className="mt-2 w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            Analyze {validFiles.length > 0 ? `${validFiles.length} file${validFiles.length > 1 ? "s" : ""}` : ""}
          </button>
        </div>
      )}
    </div>
  );
}
