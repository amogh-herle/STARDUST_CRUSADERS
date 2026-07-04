"use client";

import { useEffect, useRef, useState } from "react";
import {
  getAnalyticsStatus,
  chat,
  type AnalyticsStatus,
  type TopAccount,
  type UploadResult,
} from "@/lib/api";

// ── Stat card ────────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: "red" | "orange" | "yellow" | "green" | "blue";
}) {
  const colors: Record<string, string> = {
    red: "text-red-600",
    orange: "text-orange-500",
    yellow: "text-yellow-500",
    green: "text-green-600",
    blue: "text-blue-500",
  };
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-5 py-4">
      <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">{label}</p>
      <p
        className={`mt-1 text-2xl font-bold ${
          accent ? colors[accent] : "text-foreground"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

// ── Risk tier badge ───────────────────────────────────────────────────────────

function TierBadge({ tier }: { tier: string }) {
  const map: Record<string, string> = {
    CRITICAL: "bg-red-100 text-red-700",
    HIGH: "bg-orange-100 text-orange-700",
    MEDIUM: "bg-yellow-100 text-yellow-700",
    LOW: "bg-green-100 text-green-700",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${
        map[tier] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {tier}
    </span>
  );
}

// ── Chat message ──────────────────────────────────────────────────────────────

type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: string[];
};

function ChatBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? "bg-accent text-white rounded-br-sm"
            : "bg-white border border-slate-200 text-slate-800 rounded-bl-sm"
        }`}
      >
        <p style={{ whiteSpace: "pre-wrap" }}>{msg.content}</p>
        {!isUser && msg.sources && msg.sources.length > 0 && (
          <p className="mt-1 text-xs text-slate-400">
            Sources: {msg.sources.join(", ")}
          </p>
        )}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function ReportView({
  files,
  uploadResult,
}: {
  files: File[];
  uploadResult?: UploadResult;
}) {
  const [analytics, setAnalytics] = useState<AnalyticsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Load analytics on mount
  useEffect(() => {
    setLoading(true);
    getAnalyticsStatus()
      .then(setAnalytics)
      .catch(() =>
        setAnalytics({ status: "no_data", message: "Could not load analytics." })
      )
      .finally(() => setLoading(false));
  }, [uploadResult]);

  // Auto-scroll chat
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Seed welcome message when analytics loads
  useEffect(() => {
    if (analytics?.status === "ready" && messages.length === 0) {
      setMessages([
        {
          role: "assistant",
          content:
            `CIDECODE Intelligence Engine online.\n\nPhase 8 analytics loaded: ` +
            `${analytics.accounts ?? 0} accounts, ` +
            `${analytics.critical_accounts ?? 0} CRITICAL, ` +
            `${analytics.high_accounts ?? 0} HIGH risk.\n\n` +
            `Ask me anything about the analysis — risk drivers, fraud patterns, money trails, SAR drafts.`,
        },
      ]);
    }
  }, [analytics]);

  const sendMessage = async () => {
    const q = input.trim();
    if (!q || sending) return;
    setInput("");
    setSending(true);
    setChatError(null);

    const userMsg: Message = { role: "user", content: q };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const res = await chat(q);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.answer, sources: res.sources },
      ]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setChatError(msg);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `⚠ Unable to reach CIDECODE intelligence engine.\n\nError: ${msg}`,
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="w-full max-w-5xl mx-auto flex flex-col gap-6">
      {/* Upload result banner */}
      {uploadResult && (
        <div className="rounded-xl border border-green-200 bg-green-50 px-5 py-3 text-sm text-green-800 flex items-center gap-3">
          <span className="text-green-500 text-lg">✓</span>
          <span>
            Pipeline complete — {uploadResult.rows_parsed.toLocaleString()} rows parsed,{" "}
            {uploadResult.rows_after_clean.toLocaleString()} rows loaded.{" "}
            {uploadResult.banks_detected.length > 0 &&
              `Banks: ${uploadResult.banks_detected.join(", ")}.`}
            {uploadResult.warnings.length > 0 && (
              <span className="ml-2 text-yellow-700">
                {uploadResult.warnings.length} warning(s)
              </span>
            )}
          </span>
        </div>
      )}

      {/* Analytics summary */}
      {loading ? (
        <div className="text-sm text-slate-400 text-center py-6">
          Loading analytics…
        </div>
      ) : analytics?.status === "no_data" ? (
        <div className="rounded-xl border border-slate-200 bg-white px-5 py-8 text-center text-sm text-slate-400">
          {analytics.message ?? "No analytics data available yet."}
        </div>
      ) : (
        analytics && (
          <div>
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
              Risk Overview
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard label="CRITICAL" value={analytics.critical_accounts ?? 0} accent="red" />
              <StatCard label="HIGH" value={analytics.high_accounts ?? 0} accent="orange" />
              <StatCard label="MEDIUM" value={analytics.medium_accounts ?? 0} accent="yellow" />
              <StatCard label="Accounts" value={analytics.accounts ?? 0} accent="blue" />
            </div>

            <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-3">
              <StatCard label="Round trips" value={analytics.round_trips ?? 0} />
              <StatCard label="Layering" value={analytics.layering_chains ?? 0} />
              <StatCard label="Fan-in" value={analytics.fan_in ?? 0} />
              <StatCard label="Fan-out" value={analytics.fan_out ?? 0} />
              <StatCard label="Smurfing" value={analytics.smurfing ?? 0} />
              <StatCard label="Odd hours" value={analytics.odd_hours ?? 0} />
            </div>

            {/* Top accounts table */}
            {analytics.top_accounts && analytics.top_accounts.length > 0 && (
              <div className="mt-5">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                  Top Risk Accounts
                </p>
                <div className="rounded-xl border border-slate-200 overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-slate-50 text-left text-xs font-semibold text-slate-500 uppercase">
                        <th className="px-4 py-3">Account</th>
                        <th className="px-4 py-3">Holder</th>
                        <th className="px-4 py-3">Score</th>
                        <th className="px-4 py-3">Tier</th>
                        <th className="px-4 py-3">Patterns</th>
                      </tr>
                    </thead>
                    <tbody>
                      {analytics.top_accounts.map((acc: TopAccount, i) => (
                        <tr
                          key={acc.account_id}
                          className={`border-t border-slate-100 ${
                            i % 2 === 0 ? "bg-white" : "bg-slate-50/40"
                          }`}
                        >
                          <td className="px-4 py-2.5 font-mono text-xs text-slate-600">
                            {acc.account_id}
                          </td>
                          <td className="px-4 py-2.5 text-slate-700">{acc.account_holder}</td>
                          <td className="px-4 py-2.5 font-semibold text-slate-800">
                            {Number(acc.risk_score).toFixed(1)}
                          </td>
                          <td className="px-4 py-2.5">
                            <TierBadge tier={acc.risk_tier} />
                          </td>
                          <td className="px-4 py-2.5 text-slate-500 text-xs">
                            {acc.active_patterns}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )
      )}

      {/* Chatbot */}
      <div className="rounded-xl border border-slate-200 bg-slate-50 flex flex-col overflow-hidden"
        style={{ minHeight: "420px", maxHeight: "560px" }}>
        <div className="px-5 py-3 border-b border-slate-200 bg-white flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-green-400 inline-block" />
          <span className="text-sm font-semibold text-slate-700">
            CIDECODE AI Investigator
          </span>
          <span className="ml-auto text-xs text-slate-400">Powered by Gemini 2.5</span>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {messages.length === 0 && !loading && (
            <p className="text-xs text-slate-400 text-center mt-8">
              {analytics?.status === "ready"
                ? "Ask anything about the uploaded statements…"
                : "Upload a statement to start the investigation."}
            </p>
          )}
          {messages.map((m, i) => (
            <ChatBubble key={i} msg={m} />
          ))}
          {sending && (
            <div className="flex justify-start">
              <div className="bg-white border border-slate-200 rounded-2xl rounded-bl-sm px-4 py-3 text-sm text-slate-400">
                Thinking…
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Quick prompts */}
        <div className="px-5 py-2 flex gap-2 flex-wrap border-t border-slate-100 bg-white">
          {[
            "Draft SAR",
            "Explain Risk",
            "Community Map",
            "Trace Funds",
          ].map((label) => (
            <button
              key={label}
              onClick={() => {
                setInput(label);
                setTimeout(() => sendMessage(), 50);
              }}
              className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50 transition-colors"
            >
              {label}
            </button>
          ))}
        </div>

        {/* Input */}
        <div className="px-4 py-3 border-t border-slate-200 bg-white flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Ask about money trails, risk scores, SAR drafts…"
            className="flex-1 rounded-lg border border-slate-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
            disabled={sending}
          />
          <button
            onClick={sendMessage}
            disabled={sending || !input.trim()}
            className="rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed"
            aria-label="Send"
          >
            ➤
          </button>
        </div>
      </div>
    </div>
  );
}
