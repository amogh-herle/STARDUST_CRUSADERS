"use client";

import { useState } from "react";
import Sidebar, { View } from "@/components/Sidebar";
import Topbar, { type Case } from "@/components/Topbar";
import UploadZone from "@/components/UploadZone";
import ReportView from "@/components/ReportView";
import MoneyTrailView from "@/components/MoneyTrailView";
import LibraryView from "@/components/LibraryView";
import { type UploadResult } from "@/lib/api";

export default function Home() {
  const [view, setView] = useState<View>("upload");
  const [submittedFiles, setSubmittedFiles] = useState<File[]>([]);
  const [uploadResult, setUploadResult] = useState<UploadResult | undefined>();
  const [moneyTrailAccountId, setMoneyTrailAccountId] = useState<string | null>(null);
  const [activeCase, setActiveCase] = useState<Case | null>(null);

  const handleSubmit = (files: File[], result: UploadResult) => {
    setSubmittedFiles(files);
    setUploadResult(result);
    setView("reports");
  };

  // Called from Graph View's "Open Money Trail for this account" button
  const openMoneyTrail = (accountId: string) => {
    setMoneyTrailAccountId(accountId);
    setView("moneytrail");
  };

  return (
    <div className="flex min-h-screen">
      <Sidebar view={view} onChange={setView} />

      <div className="flex flex-1 flex-col">
        <Topbar
          view={view}
          activeCase={activeCase}
          onClearActiveCase={() => setActiveCase(null)}
        />

        <main className="flex flex-1 flex-col items-center justify-center px-8 py-12">
          {view === "upload" && (
            <UploadZone
              activeCase={activeCase}
              onSubmit={handleSubmit}
            />
          )}
          {(view === "reports" || view === "graph") && (
            <ReportView
              files={submittedFiles}
              uploadResult={uploadResult}
              activeSubView={view}
              onOpenMoneyTrail={openMoneyTrail}
            />
          )}
          {view === "moneytrail" && (
            <MoneyTrailView
              initialAccountId={moneyTrailAccountId}
              onAccountChange={setMoneyTrailAccountId}
            />
          )}
          {view === "library" && (
            <LibraryView
              onOpenCase={(c, uploadedFiles, uploadId) => {
                setActiveCase(c);
                if (uploadId && uploadedFiles && uploadedFiles.length > 0) {
                  const mockFiles = uploadedFiles.map((fileName) => new File([], fileName));
                  setSubmittedFiles(mockFiles);
                  setUploadResult({
                    upload_id: uploadId,
                    files_received: mockFiles.length,
                    files_ingested: mockFiles.length,
                    rows_parsed: 0,
                    rows_after_clean: 0,
                    banks_detected: [],
                    warnings: [],
                    status: "success",
                  });
                  setView("reports");
                } else {
                  setView("upload");
                }
              }}
            />
          )}
        </main>
      </div>
    </div>
  );
}
