"use client";

import { useState } from "react";
import Sidebar, { View } from "@/components/Sidebar";
import Topbar from "@/components/Topbar";
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
        <Topbar view={view} />

        <main className="flex flex-1 flex-col items-center justify-center px-8 py-12">
          {view === "upload" && <UploadZone onSubmit={handleSubmit} />}
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
          {view === "library" && <LibraryView />}
        </main>
      </div>
    </div>
  );
}
