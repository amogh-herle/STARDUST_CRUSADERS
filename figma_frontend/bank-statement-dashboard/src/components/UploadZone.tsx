"use client";

import { useCallback, useRef, useState } from "react";
import { uploadStatements, type UploadResult, updateCase } from "@/lib/api";
import { getSession } from "@/lib/auth";
import { NewCaseModal, type Case } from "./Topbar";

const ACCEPTED_EXTENSIONS = [".pdf", ".csv", ".xlsx", ".xls", ".docx", ".png", ".jpg", ".jpeg"];

type QueuedFile = {
  file: File;
  error?: string;
};

type UploadState = "idle" | "uploading" | "done" | "error";

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isAccepted(file: File) {
  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  return ACCEPTED_EXTENSIONS.includes(ext);
}

export default function UploadZone({
  activeCase,
  onSubmit,
}: {
  activeCase?: Case | null;
  onSubmit: (files: File[], result: UploadResult) => void;
}) {
  const [isDragging, setIsDragging] = useState(false);
  const [files, setFiles] = useState<QueuedFile[]>([]);
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string>("");
  const [showConfirmCase, setShowConfirmCase] = useState(false);
  const [uploadedResult, setUploadedResult] = useState<UploadResult | null>(null);
  const [caseConfirmed, setCaseConfirmed] = useState(false);
  const [currentCase, setCurrentCase] = useState<Case | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const user = getSession();
  const isCaseConfirmed = caseConfirmed || !!activeCase;

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

  const handleAnalyze = async () => {
    if (validFiles.length === 0) return;
    setUploadState("uploading");
    setUploadError(null);

    // Simulate pipeline stage labels while the request is in flight
    const stages = [
      "Uploading files…",
      "Running Phase 6 — parsing statements…",
      "Running Phase 7 — cleaning data…",
      "Running Phase 8 — analytics & risk scoring…",
      "Finalising report…",
    ];
    let stageIdx = 0;
    setProgress(stages[0]);
    const interval = setInterval(() => {
      stageIdx = Math.min(stageIdx + 1, stages.length - 1);
      setProgress(stages[stageIdx]);
    }, 4000);

    try {
      const result = await uploadStatements(validFiles.map((f) => f.file));

      // Link upload to case if a case context is active
      const caseContext = activeCase || currentCase;
      if (caseContext && user) {
        try {
          const fileNames = validFiles.map((f) => f.file.name);
          await updateCase(caseContext.id, {
            upload_id: result.upload_id,
            uploaded_files: fileNames,
          });
        } catch (dbErr) {
          console.warn("Database error linking files to case:", dbErr);
        }
      }

      clearInterval(interval);
      setUploadState("done");
      setUploadedResult(result);
      onSubmit(validFiles.map((f) => f.file), result);
    } catch (err) {
      clearInterval(interval);
      setUploadState("error");
      setUploadError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  const handleCaseCreated = (newCase: Case) => {
    setCurrentCase(newCase);
    setCaseConfirmed(true);
    setShowConfirmCase(false);
  };

  return (
    <div className="w-full max-w-2xl mx-auto">
      {activeCase && (
        <div className="mb-6 rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 text-xs text-indigo-900 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-base">💼</span>
            <span>
              Uploading statements to active case: <strong>{activeCase.case_name}</strong> ({activeCase.case_number})
            </span>
          </div>
        </div>
      )}

      {/* Drop zone */}
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
        onClick={() => uploadState === "idle" && inputRef.current?.click()}
        className={`cursor-pointer rounded-xl border-2 border-dashed p-12 text-center transition-colors ${
          isDragging
            ? "border-accent bg-accent/5"
            : "border-slate-300 bg-white hover:border-slate-400"
        } ${uploadState === "uploading" ? "pointer-events-none opacity-60" : ""}`}
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

      {/* File list */}
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
              {uploadState === "idle" && (
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
              )}
            </div>
          ))}

          {/* Confirm Case button (after files uploaded) / Analyze button (after case confirmed) / loading / error */}
          {uploadState === "idle" && !isCaseConfirmed && (
            <button
              onClick={() => setShowConfirmCase(true)}
              disabled={validFiles.length === 0}
              className="mt-2 w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              Confirm Case Details
            </button>
          )}

          {uploadState === "idle" && isCaseConfirmed && (
            <button
              onClick={handleAnalyze}
              disabled={validFiles.length === 0}
              className="mt-2 w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              Analyze{" "}
              {validFiles.length > 0
                ? `${validFiles.length} file${validFiles.length > 1 ? "s" : ""}`
                : ""}
            </button>
          )}

          {uploadState === "uploading" && (
            <div className="mt-2 rounded-lg bg-slate-50 border border-slate-200 px-4 py-3 text-sm text-slate-600 flex items-center gap-3">
              {/* Spinner */}
              <svg
                className="h-4 w-4 animate-spin text-accent shrink-0"
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
              <span>{progress}</span>
            </div>
          )}

          {uploadState === "done" && (
            <div className="mt-2 rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
              <span className="font-medium">✓ Analysis complete!</span> Files processed successfully.
            </div>
          )}

          {uploadState === "error" && (
            <div className="mt-2 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
              <span className="font-medium">Pipeline error: </span>
              {uploadError}
              <button
                onClick={() => {
                  setUploadState("idle");
                  setUploadError(null);
                }}
                className="ml-3 underline text-red-600 hover:text-red-800"
              >
                Retry
              </button>
            </div>
          )}
        </div>
      )}

      {/* Case confirmation modal */}
      {showConfirmCase && user && (
        <NewCaseModal
          userId={user.id}
          onClose={() => setShowConfirmCase(false)}
          onCreated={handleCaseCreated}
        />
      )}
    </div>
  );
}
