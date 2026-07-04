"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import {
  getAnalyticsStatus,
  chat,
  getLedgerTrace,
  getAccountTransactions,
  getFullGraph,
  type AnalyticsStatus,
  type TopAccount,
  type UploadResult,
  type CytoscapeGraph,
  type Transaction,
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
  activeSubView = "reports",
}: {
  files: File[];
  uploadResult?: UploadResult;
  activeSubView?: "reports" | "graph";
}) {
  const [analytics, setAnalytics] = useState<AnalyticsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);
  const cyRef = useRef<HTMLDivElement>(null);
  const [graphData, setGraphData] = useState<CytoscapeGraph | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [selectedNodeData, setSelectedNodeData] = useState<any>(null);
  const [nodeTransactions, setNodeTransactions] = useState<Transaction[]>([]);
  const [nodeTransactionsLoading, setNodeTransactionsLoading] = useState(false);
  const [layoutName, setLayoutName] = useState<string>("concentric");
  const cyInstance = useRef<cytoscape.Core | null>(null);

  // Incremental / Overview graph states
  const [isOverviewMode, setIsOverviewMode] = useState(true);
  const [totalTxnCount, setTotalTxnCount] = useState<number>(0);
  const [minAmount, setMinAmount] = useState<number>(0);
  const [minDateIndex, setMinDateIndex] = useState<number>(0);

  // Load transactions for the selected node
  useEffect(() => {
    if (!selectedNodeData?.id) {
      setNodeTransactions([]);
      setTotalTxnCount(0);
      return;
    }
    setNodeTransactionsLoading(true);
    getAccountTransactions(selectedNodeData.id, 1, 100)
      .then((res) => {
        setNodeTransactions(res.items || []);
        setTotalTxnCount(res.total || 0);
      })
      .catch((err) => {
        console.error("Failed to load node transactions:", err);
      })
      .finally(() => {
        setNodeTransactionsLoading(false);
      });
  }, [selectedNodeData]);

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

  // Automatically select the highest risk account on load
  useEffect(() => {
    if (analytics?.status === "ready" && analytics.top_accounts && analytics.top_accounts.length > 0 && !selectedAccountId) {
      setSelectedAccountId(analytics.top_accounts[0].account_id);
    }
  }, [analytics, selectedAccountId]);

  // Load overview graph or ledger trace depending on mode
  useEffect(() => {
    if (activeSubView !== "graph") return;

    if (isOverviewMode) {
      setGraphLoading(true);
      getFullGraph(10)
        .then((data) => {
          setGraphData(data);
        })
        .catch((err) => {
          console.error("Failed to load overview graph:", err);
        })
        .finally(() => {
          setGraphLoading(false);
        });
    } else if (selectedAccountId) {
      setGraphLoading(true);
      setSelectedNodeData(null);
      getLedgerTrace(selectedAccountId)
        .then((data) => {
          setGraphData(data);
          setMinAmount(0);
          setMinDateIndex(0);
        })
        .catch((err) => {
          console.error("Failed to load ledger trace:", err);
        })
        .finally(() => {
          setGraphLoading(false);
        });
    }
  }, [activeSubView, isOverviewMode, selectedAccountId]);

  // Compute filtered elements if in EGO mode and totalTxnCount < 30
  const filteredElements = useMemo(() => {
    if (!graphData) return [];

    // If in overview mode or txn count >= 30, show all elements
    if (isOverviewMode || totalTxnCount >= 30) {
      const elements: cytoscape.ElementDefinition[] = [];
      graphData.nodes.forEach((n) => {
        elements.push({
          group: "nodes",
          data: {
            id: n.data.id,
            label: n.data.label,
            bank: n.data.bank,
            risk_score: n.data.risk_score,
            risk_tier: n.data.risk_tier,
            role: n.data.role,
            is_seed: n.data.is_seed || n.data.id === selectedAccountId,
            is_internal: n.data.is_internal,
          },
        });
      });
      graphData.edges.forEach((e) => {
        elements.push({
          group: "edges",
          data: {
            id: e.data.id,
            source: e.data.source,
            target: e.data.target,
            amount: e.data.amount,
            dates: e.data.dates,
            risk_flag: e.data.risk_flag,
          },
        });
      });
      return elements;
    }

    // Otherwise (ego mode with < 30 txns), apply amount and date filters!
    const allDates = Array.from(
      new Set(graphData.edges.flatMap((e) => e.data.dates || []))
    ).sort();
    const minDateStr = allDates[minDateIndex] || "";

    // Filter edges
    const keptEdges = graphData.edges.filter((e) => {
      if (e.data.amount < minAmount) return false;
      if (minDateStr) {
        const edgeDates = e.data.dates || [];
        const hasMatchingDate = edgeDates.some((d) => d >= minDateStr);
        if (!hasMatchingDate) return false;
      }
      return true;
    });

    // Collect active node IDs
    const activeNodeIds = new Set<string>();
    if (selectedAccountId) {
      activeNodeIds.add(selectedAccountId);
    }
    keptEdges.forEach((e) => {
      activeNodeIds.add(e.data.source);
      activeNodeIds.add(e.data.target);
    });

    // Construct elements
    const elements: cytoscape.ElementDefinition[] = [];
    graphData.nodes.forEach((n) => {
      if (activeNodeIds.has(n.data.id)) {
        elements.push({
          group: "nodes",
          data: {
            id: n.data.id,
            label: n.data.label,
            bank: n.data.bank,
            risk_score: n.data.risk_score,
            risk_tier: n.data.risk_tier,
            role: n.data.role,
            is_seed: n.data.is_seed || n.data.id === selectedAccountId,
            is_internal: n.data.is_internal,
          },
        });
      }
    });

    keptEdges.forEach((e) => {
      elements.push({
        group: "edges",
        data: {
          id: e.data.id,
          source: e.data.source,
          target: e.data.target,
          amount: e.data.amount,
          dates: e.data.dates,
          risk_flag: e.data.risk_flag,
        },
      });
    });

    return elements;
  }, [graphData, isOverviewMode, totalTxnCount, minAmount, minDateIndex, selectedAccountId]);

  // Re-run layout if layout name changes
  useEffect(() => {
    if (cyInstance.current && filteredElements.length > 0) {
      const layout = cyInstance.current.layout({
        name: layoutName,
        animate: true,
        fit: true,
        padding: 30,
        concentric: (node: any) => {
          if (node.data("is_seed")) return 3;
          return node.data("risk_tier") === "CRITICAL" || node.data("risk_tier") === "HIGH" ? 2 : 1;
        },
        levelWidth: () => 1,
      } as any);
      layout.run();
    }
  }, [layoutName, filteredElements]);

  // Initialize Cytoscape
  useEffect(() => {
    if (!cyRef.current || filteredElements.length === 0) return;

    if (cyInstance.current) {
      cyInstance.current.destroy();
    }

    const cy = cytoscape({
      container: cyRef.current,
      elements: filteredElements,
      style: [
        {
          selector: "node",
          style: {
            "label": (node: any) => {
              const lbl = node.data("label") || node.data("id");
              return lbl.length > 12 ? lbl.substring(0, 10) + "..." : lbl;
            },
            "width": (node: any) => (isOverviewMode ? 46 : (node.data("is_seed") ? 42 : 28)),
            "height": (node: any) => (isOverviewMode ? 46 : (node.data("is_seed") ? 42 : 28)),
            "background-color": (node: any) => {
              if (node.data("is_seed")) return "#3b82f6"; // Vibrant blue seed
              const tier = node.data("risk_tier");
              if (tier === "CRITICAL") return "#ef4444"; // Red
              if (tier === "HIGH") return "#f97316"; // Orange
              if (tier === "MEDIUM") return "#eab308"; // Yellow
              return "#10b981"; // Green
            },
            "color": "#334155",
            "font-size": "9px",
            "font-weight": "bold",
            "text-valign": "bottom",
            "text-margin-y": 4,
            "border-width": (node: any) => (isOverviewMode ? 2 : (node.data("is_seed") ? 3 : 1.5)),
            "border-color": "#ffffff",
            "overlay-padding": "4px",
            "overlay-opacity": 0,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-width": 3,
            "border-color": "#4f46e5",
          },
        },
        {
          selector: "edge",
          style: {
            "width": (edge: any) => Math.min(Math.max(Math.log10(edge.data("amount") || 1) * 1.2, 1.5), 5),
            "line-color": (edge: any) => (edge.data("risk_flag") === "SUSPICIOUS" ? "#f43f5e" : "#64748b"),
            "target-arrow-color": (edge: any) => (edge.data("risk_flag") === "SUSPICIOUS" ? "#f43f5e" : "#64748b"),
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "label": (edge: any) => {
              const amt = edge.data("amount");
              if (amt >= 10000000) return `₹${(amt / 10000000).toFixed(1)}Cr`;
              if (amt >= 100000) return `₹${(amt / 100000).toFixed(1)}L`;
              if (amt >= 1000) return `₹${(amt / 1000).toFixed(0)}k`;
              return `₹${amt}`;
            },
            "font-size": "8px",
            "font-weight": "bold",
            "color": "#475569",
            "text-background-opacity": 0.85,
            "text-background-color": "#ffffff",
            "text-background-padding": "1.5px",
            "text-background-shape": "roundrectangle",
            "text-rotation": "autorotate",
            "arrow-scale": 0.8,
          },
        },
      ],
      layout: {
        name: layoutName,
        concentric: (node: any) => {
          if (node.data("is_seed")) return 3;
          return node.data("risk_tier") === "CRITICAL" || node.data("risk_tier") === "HIGH" ? 2 : 1;
        },
        levelWidth: () => 1,
        padding: 30,
        animate: true,
      } as any,
    });

    cyInstance.current = cy;

    cy.on("tap", "node", (evt) => {
      const data = evt.target.data();
      setSelectedNodeData(data);
      if (data && data.id) {
        if (isOverviewMode) {
          setIsOverviewMode(false);
          setSelectedAccountId(data.id);
        } else if (data.id !== selectedAccountId) {
          setSelectedAccountId(data.id);
        }
      }
    });

    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        setSelectedNodeData(null);
      }
    });

    const seedNode = cy.nodes("[?is_seed]");
    if (seedNode.length > 0) {
      seedNode.select();
      setSelectedNodeData(seedNode.data());
    }

    return () => {
      if (cyInstance.current) {
        cyInstance.current.destroy();
        cyInstance.current = null;
      }
    };
  }, [filteredElements, activeSubView]);

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
          <div className="w-full space-y-6">
            {activeSubView === "reports" && (
              <>
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
                          {analytics.top_accounts.map((acc: TopAccount, i) => {
                            const isSelected = selectedAccountId === acc.account_id;
                            return (
                              <tr
                                key={acc.account_id}
                                onClick={() => setSelectedAccountId(acc.account_id)}
                                className={`border-t border-slate-100 cursor-pointer transition-colors ${
                                  isSelected 
                                    ? "bg-blue-50/60 hover:bg-blue-50/80 border-l-2 border-l-blue-500" 
                                    : i % 2 === 0 
                                      ? "bg-white hover:bg-slate-50" 
                                      : "bg-slate-50/40 hover:bg-slate-50"
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
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* Interactive Money Trail Command Center */}
            {activeSubView === "graph" && (
              <div className="mt-6 rounded-xl border border-slate-200 bg-white p-5 w-full">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                      <span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
                      Interactive Money Trail Command Center
                      <span className="text-xs font-normal text-slate-500 ml-1">
                        {isOverviewMode 
                          ? "— Top 10 Accounts Overview" 
                          : `— Account ${selectedAccountId} (${totalTxnCount} Txns)`}
                      </span>
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Drill down and trace flow paths by clicking any node in the transaction graph.
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    {!isOverviewMode && (
                      <button
                        onClick={() => {
                          setIsOverviewMode(true);
                          setSelectedAccountId(null);
                          setSelectedNodeData(null);
                        }}
                        className="rounded border border-blue-200 bg-blue-50 hover:bg-blue-100 p-1 px-2.5 text-xs font-semibold text-blue-600 transition-colors mr-1"
                      >
                        ← Back to Overview
                      </button>
                    )}
                    <div className="flex items-center gap-1.5 text-xs text-slate-500">
                      <span>Layout:</span>
                      <select
                        value={layoutName}
                        onChange={(e) => setLayoutName(e.target.value)}
                        className="rounded border border-slate-200 px-2 py-1 bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-accent"
                      >
                        <option value="concentric">Concentric (Default)</option>
                        <option value="cose">CoSE (Organic)</option>
                        <option value="grid">Grid (Clean)</option>
                        <option value="circle">Circle (Radial)</option>
                      </select>
                    </div>
                    <div className="flex gap-1">
                      <button
                        onClick={() => cyInstance.current?.zoom(cyInstance.current.zoom() * 1.2)}
                        className="rounded border border-slate-200 bg-white hover:bg-slate-50 p-1 px-2 text-xs font-semibold text-slate-600 transition-colors"
                        title="Zoom In"
                      >
                        ＋
                      </button>
                      <button
                        onClick={() => cyInstance.current?.zoom(cyInstance.current.zoom() / 1.2)}
                        className="rounded border border-slate-200 bg-white hover:bg-slate-50 p-1 px-2 text-xs font-semibold text-slate-600 transition-colors"
                        title="Zoom Out"
                      >
                        －
                      </button>
                      <button
                        onClick={() => {
                          cyInstance.current?.fit();
                          cyInstance.current?.center();
                        }}
                        className="rounded border border-slate-200 bg-white hover:bg-slate-50 p-1 px-2 text-xs font-semibold text-slate-600 transition-colors"
                        title="Fit Window"
                      >
                        ⛶
                      </button>
                    </div>
                  </div>
                </div>

                {/* Incremental Filter Sliders for < 30 txn accounts */}
                {!isOverviewMode && totalTxnCount < 30 && graphData && (
                  <div className="mt-3 mb-2 p-3 bg-slate-50 border border-slate-200 rounded-lg flex flex-col md:flex-row gap-6">
                    {/* Amount Filter Slider */}
                    <div className="flex-1">
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-xs font-medium text-slate-700">Minimum Transaction Amount</span>
                        <span className="text-xs font-semibold text-blue-600 bg-blue-50 px-2 py-0.5 rounded">
                          ₹{minAmount.toLocaleString()}
                        </span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max={(() => {
                          const maxAmt = Math.max(...graphData.edges.map(e => e.data.amount || 0), 1000);
                          return maxAmt;
                        })()}
                        step={(() => {
                          const maxAmt = Math.max(...graphData.edges.map(e => e.data.amount || 0), 1000);
                          return Math.round(maxAmt / 50) || 10;
                        })()}
                        value={minAmount}
                        onChange={(e) => setMinAmount(Number(e.target.value))}
                        className="w-full h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
                      />
                    </div>

                    {/* Date Filter Slider */}
                    {(() => {
                      const allDates = Array.from(new Set(graphData.edges.flatMap(e => e.data.dates || []))).sort();
                      if (allDates.length <= 1) return null;
                      return (
                        <div className="flex-1">
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="text-xs font-medium text-slate-700">Filter Transactions From Date</span>
                            <span className="text-xs font-semibold text-blue-600 bg-blue-50 px-2 py-0.5 rounded">
                              {allDates[minDateIndex] || "All Dates"}
                            </span>
                          </div>
                          <input
                            type="range"
                            min="0"
                            max={allDates.length - 1}
                            step="1"
                            value={minDateIndex}
                            onChange={(e) => setMinDateIndex(Number(e.target.value))}
                            className="w-full h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
                          />
                        </div>
                      );
                    })()}
                  </div>
                )}

              <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 border border-slate-200 rounded-xl overflow-hidden min-h-[460px]">
                {/* Graph View (Left columns) */}
                <div className="lg:col-span-2 relative bg-slate-900 overflow-hidden flex flex-col justify-end min-h-[350px] lg:min-h-auto">
                  {/* Grid lines background style */}
                  <div className="absolute inset-0 pointer-events-none opacity-[0.03]" style={{
                    backgroundImage: "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
                    backgroundSize: "20px 20px"
                  }} />

                  {/* Cytoscape element container */}
                  <div ref={cyRef} className="absolute inset-0 w-full h-full" />

                  {graphLoading && (
                    <div className="absolute inset-0 bg-slate-950/70 backdrop-blur-sm flex items-center justify-center text-sm text-blue-400 font-semibold z-10">
                      <div className="flex flex-col items-center gap-2">
                        <div className="animate-spin rounded-full h-6 w-6 border-2 border-blue-500 border-t-transparent" />
                        Traced fund propagation…
                      </div>
                    </div>
                  )}

                  {/* Legend Overlay */}
                  <div className="absolute bottom-3 left-3 bg-slate-950/80 backdrop-blur-md border border-slate-800 rounded-lg p-2.5 flex flex-wrap gap-x-4 gap-y-1.5 text-[10px] text-slate-400 z-10 pointer-events-none">
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-blue-500 border border-white inline-block" />
                      <span className="font-semibold text-slate-300">Seed Subject</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-red-500 inline-block" />
                      <span>Critical Risk</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-orange-500 inline-block" />
                      <span>High Risk</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-yellow-500 inline-block" />
                      <span>Medium Risk</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-green-500 inline-block" />
                      <span>Clean / Source</span>
                    </div>
                    <div className="flex items-center gap-1.5 border-l border-slate-800 pl-4">
                      <span className="h-[2px] w-5 bg-slate-500 inline-block" />
                      <span>Normal Transaction</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="h-[2px] w-5 bg-red-500 inline-block" />
                      <span className="text-red-400 font-semibold">Suspicious Transaction</span>
                    </div>
                  </div>
                </div>

                {/* Account details and transaction history (Right column) */}
                <div className="bg-slate-50 border-t lg:border-t-0 lg:border-l border-slate-200 p-4 flex flex-col justify-between overflow-y-auto">
                  {selectedNodeData ? (
                    <div className="space-y-4 flex-1 flex flex-col justify-between h-full">
                      <div className="space-y-4">
                        <div>
                          <div className="flex items-center justify-between">
                            <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-bold tracking-wide uppercase ${
                              selectedNodeData.is_seed 
                                ? "bg-blue-100 text-blue-700 border border-blue-200" 
                                : "bg-slate-200 text-slate-700"
                            }`}>
                              {selectedNodeData.is_seed ? "SEED ACCOUNT" : "COUNTERPARTY"}
                            </span>
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md ${
                              selectedNodeData.risk_tier === "CRITICAL" ? "bg-red-100 text-red-700" :
                              selectedNodeData.risk_tier === "HIGH" ? "bg-orange-100 text-orange-700" :
                              selectedNodeData.risk_tier === "MEDIUM" ? "bg-yellow-100 text-yellow-700" :
                              "bg-green-100 text-green-700"
                            }`}>
                              {selectedNodeData.risk_tier} RISK ({selectedNodeData.risk_score.toFixed(1)})
                            </span>
                          </div>
                          <h4 className="mt-2 font-mono text-sm font-bold text-slate-800 select-all">{selectedNodeData.id}</h4>
                          <p className="text-xs text-slate-500 font-medium">{selectedNodeData.bank}</p>
                        </div>

                        <div className="border-t border-slate-200 pt-3">
                          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Network Role Profile</p>
                          <div className="grid grid-cols-2 gap-2 text-xs">
                            <div className="bg-white border border-slate-200 rounded p-2">
                              <span className="text-[9px] text-slate-400 block">Identified Role</span>
                              <span className="font-semibold text-slate-700 uppercase">{selectedNodeData.role || "UNKNOWN"}</span>
                            </div>
                            <div className="bg-white border border-slate-200 rounded p-2">
                              <span className="text-[9px] text-slate-400 block">Class</span>
                              <span className="font-semibold text-slate-700">{selectedNodeData.is_internal ? "Internal Mule" : "External Account"}</span>
                            </div>
                          </div>
                        </div>

                        <div className="border-t border-slate-200 pt-3">
                          <div className="flex justify-between items-center mb-1">
                            <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Transaction Flow Details</p>
                          </div>
                          
                          <div className="max-h-[120px] overflow-y-auto space-y-1.5 pr-1">
                            {graphData?.edges
                              .filter(e => e.data.source === selectedNodeData.id || e.data.target === selectedNodeData.id)
                              .map((e, index) => {
                                const isIncoming = e.data.target === selectedAccountId;
                                return (
                                  <div key={index} className={`flex items-center justify-between p-2 rounded text-[11px] border ${
                                    e.data.risk_flag === "SUSPICIOUS" 
                                      ? "bg-red-50/55 border-red-100" 
                                      : "bg-white border-slate-200"
                                  }`}>
                                    <div>
                                      <div className="flex items-center gap-1.5">
                                        <span className={`font-semibold ${isIncoming ? "text-green-600" : "text-blue-600"}`}>
                                          {isIncoming ? "← Credit" : "→ Debit"}
                                        </span>
                                        {e.data.risk_flag === "SUSPICIOUS" && (
                                          <span className="text-[9px] font-bold text-red-600 uppercase">⚠ Suspicious</span>
                                        )}
                                      </div>
                                      <span className="text-[9px] text-slate-400 block">{e.data.dates.join(", ")}</span>
                                    </div>
                                    <span className="font-mono font-bold text-slate-800">
                                      ₹{e.data.amount.toLocaleString("en-IN")}
                                    </span>
                                  </div>
                                );
                              })
                            }
                            {(!graphData?.edges.some(e => e.data.source === selectedNodeData.id || e.data.target === selectedNodeData.id)) && (
                              <p className="text-[11px] text-slate-400 text-center py-2">No active flows linked to this seed in the current trace.</p>
                            )}
                          </div>
                        </div>

                        <div className="border-t border-slate-200 pt-3 flex flex-col min-h-0">
                          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Account Transaction Ledger (Database)</p>
                          {nodeTransactionsLoading ? (
                            <div className="text-center text-xs text-slate-400 py-4 flex flex-col items-center gap-1.5">
                              <div className="animate-spin rounded-full h-4 w-4 border border-blue-500 border-t-transparent" />
                              Loading database transactions...
                            </div>
                          ) : nodeTransactions.length > 0 ? (
                            <div className="overflow-y-auto border border-slate-200 rounded-lg max-h-[220px]">
                              <table className="w-full text-left text-[11px]">
                                <thead className="bg-slate-100 text-slate-500 font-semibold sticky top-0 uppercase">
                                  <tr>
                                    <th className="px-2.5 py-1.5">Date</th>
                                    <th className="px-2.5 py-1.5">Narration</th>
                                    <th className="px-2.5 py-1.5 text-right">Amount</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 bg-white">
                                  {nodeTransactions.map((t, idx) => {
                                    const isDebit = t.debit > 0;
                                    const amount = isDebit ? t.debit : t.credit;
                                    const isSuspicious = t.is_high_value_flag || t.is_balance_breach || (t.final_risk_score && t.final_risk_score >= 0.7);
                                    return (
                                      <tr key={t.id || idx} className={`hover:bg-slate-50 ${isSuspicious ? "bg-red-50/30" : ""}`}>
                                        <td className="px-2.5 py-1.5 whitespace-nowrap text-slate-500 font-mono text-[10px]">{t.date}</td>
                                        <td className="px-2.5 py-1.5 text-slate-600 truncate max-w-[120px]" title={t.narration}>{t.narration}</td>
                                        <td className={`px-2.5 py-1.5 text-right font-mono font-semibold ${isDebit ? "text-red-600" : "text-green-600"}`}>
                                          {isDebit ? "-" : "+"}₹{amount.toLocaleString("en-IN")}
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          ) : (
                            <p className="text-[11px] text-slate-400 text-center py-4 bg-white border border-dashed border-slate-200 rounded-lg">No database transactions found for this account.</p>
                          )}
                        </div>
                      </div>

                      {/* Trace buttons */}
                      {!selectedNodeData.is_seed && (
                        <div className="pt-2">
                          <button
                            onClick={() => setSelectedAccountId(selectedNodeData.id)}
                            className="w-full rounded-lg bg-accent text-white hover:bg-blue-600 py-2 text-xs font-semibold shadow-sm transition-colors"
                          >
                            Trace this node's money trail →
                          </button>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-center p-4">
                      <div className="text-2xl text-slate-300">⚙</div>
                      <p className="text-xs text-slate-400 mt-2 font-medium">Select any node in the graph to display deep forensic intelligence</p>
                    </div>
                  )}

                  {/* Back to top seed if currently navigated away */}
                  {selectedAccountId && analytics?.top_accounts && analytics.top_accounts.length > 0 && selectedAccountId !== analytics.top_accounts[0].account_id && (
                    <button
                      onClick={() => {
                        if (analytics?.top_accounts && analytics.top_accounts[0]) {
                          setSelectedAccountId(analytics.top_accounts[0].account_id);
                        }
                      }}
                      className="mt-3 w-full rounded border border-slate-200 bg-white hover:bg-slate-50 py-1.5 text-xs text-slate-600 font-medium transition-colors"
                    >
                      ← Back to Primary Suspect ({analytics?.top_accounts?.[0]?.account_id})
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
          </div>
        )
      )}

      {/* Chatbot */}
      {activeSubView === "reports" && (
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
      )}
    </div>
  );
}
