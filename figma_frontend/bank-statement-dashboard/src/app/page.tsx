"use client";

import { useState } from "react";
import Sidebar, { View } from "@/components/Sidebar";
import Topbar from "@/components/Topbar";
import UploadZone from "@/components/UploadZone";
import ReportView from "@/components/ReportView";
import LibraryView from "@/components/LibraryView";
import { type UploadResult } from "@/lib/api";

export default function Home() {
  const [view, setView] = useState<View>("upload");
  const [submittedFiles, setSubmittedFiles] = useState<File[]>([]);
  const [uploadResult, setUploadResult] = useState<UploadResult | undefined>();

  const handleSubmit = (files: File[], result: UploadResult) => {
    setSubmittedFiles(files);
    setUploadResult(result);
    setView("reports");
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
            />
          )}
          {view === "library" && <LibraryView />}
        </main>
      </div>
    </div>
  );
}
