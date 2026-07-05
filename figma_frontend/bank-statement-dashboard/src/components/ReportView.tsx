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
  onOpenMoneyTrail,
}: {
  files: File[];
  uploadResult?: UploadResult;
  activeSubView?: "reports" | "graph";
  onOpenMoneyTrail?: (accountId: string) => void;
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
  const [dayOffset, setDayOffset] = useState<number>(30);
  const [targetDateStr, setTargetDateStr] = useState<string>("");
  const [totalTxnCount, setTotalTxnCount] = useState<number>(0);
  const [minAmount, setMinAmount] = useState<number>(0);
  const [minDateIndex, setMinDateIndex] = useState<number>(0);

  // New AML Filter states for the dashboard Graph View
  const [searchQuery, setSearchQuery] = useState("");
  const [minAnomalyScore, setMinAnomalyScore] = useState(0);
  const [selectedRiskTiers, setSelectedRiskTiers] = useState<string[]>([]);
  const [selectedModes, setSelectedModes] = useState<string[]>([]);
  const [flaggedOnly, setFlaggedOnly] = useState(false);

  const handleHighlightNode = (nodeId: string) => {
    if (cyInstance.current) {
      const node = cyInstance.current.$id(nodeId);
      if (node.length > 0) {
        cyInstance.current.elements().unselect();
        node.select();
        cyInstance.current.animate({
          center: { eles: node },
          zoom: Math.max(cyInstance.current.zoom(), 1.2),
          duration: 400
        });
        setSelectedNodeData(node.data());
      }
    }
  };

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

  // Auto-set target date when entering isolated mode
  useEffect(() => {
    if (!isOverviewMode && selectedAccountId && graphData && !targetDateStr) {
      const edges = (graphData as any).edges || [];
      const nodeEdges = edges.filter((e: any) => String(e.source) === String(selectedAccountId) || String(e.target) === String(selectedAccountId));
      const dates = Array.from(new Set(nodeEdges.map((e: any) => e.datetime ? e.datetime.split(" ")[0] : (e.dates && e.dates[0]) || "").filter(Boolean))).sort();
      if (dates.length > 0) {
        setTargetDateStr(dates[0] as string);
      }
    }
  }, [isOverviewMode, selectedAccountId, graphData, targetDateStr]);

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

  // Load static graph.json when graph tab is active
  useEffect(() => {
    if (activeSubView !== "graph") return;

    setGraphLoading(true);
    fetch("/graph.json")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load graph.json from public assets");
        return res.json();
      })
      .then((data: any) => {
        if (!data || !data.nodes || !data.edges) {
          throw new Error("Invalid graph data structure in graph.json");
        }
        
        // Normalize nodes
        const normalizedNodes = data.nodes.map((n: any) => {
          const base = n.data ? { ...n.data, ...n } : n;
          return {
            id: String(base.id || ""),
            label: base.label || base.id || "",
            account_holder: base.account_holder,
            is_known_account: base.is_known_account === true || base.is_known_account === "true",
            role: base.role,
            total_transactions: base.total_transactions !== undefined ? Number(base.total_transactions) : undefined,
            is_expanded: base.is_expanded,
            is_expandable: base.is_expandable,
            total_received: base.total_received !== undefined ? Number(base.total_received) : undefined,
            total_forwarded: base.total_forwarded !== undefined ? Number(base.total_forwarded) : undefined,
            max_anomaly_score: base.max_anomaly_score !== undefined ? Number(base.max_anomaly_score) : undefined,
            in_degree: base.in_degree !== undefined ? Number(base.in_degree) : undefined,
            out_degree: base.out_degree !== undefined ? Number(base.out_degree) : undefined,
            risk_score: base.risk_score !== undefined ? Number(base.risk_score) : undefined,
            risk_tier: base.risk_tier,
            bank_name: base.bank_name || base.bank || "",
          };
        });

        // Normalize edges
        const normalizedEdges = data.edges.map((e: any) => {
          const base = e.data ? { ...e.data, ...e } : e;
          return {
            id: String(base.id || ""),
            source: String(base.source || ""),
            target: String(base.target || ""),
            amount: Number(base.amount || 0),
            mode: base.mode || base.channel || "",
            transaction_id: base.transaction_id,
            narration: base.narration,
            datetime: base.datetime || (base.dates && base.dates[0]) || "",
            direction: base.direction,
            is_flagged: base.is_flagged !== undefined ? Number(base.is_flagged) : (base.risk_flag === "SUSPICIOUS" ? 1 : 0),
            anomaly_score: base.anomaly_score !== undefined ? Number(base.anomaly_score) : undefined,
          };
        });

        setGraphData({ nodes: normalizedNodes, edges: normalizedEdges } as any);
      })
      .catch((err) => {
        console.error("Failed to load overview graph:", err);
      })
      .finally(() => {
        setGraphLoading(false);
      });
  }, [activeSubView]);

  // Compute filtered elements
  const filteredElements = useMemo(() => {
    if (!graphData) return [];

    const nodes = (graphData as any).nodes || [];
    let edges = (graphData as any).edges || [];

    if (isOverviewMode) {
      // Overview Mode: Only show seed nodes, no edges
      const seedNodes = nodes.filter((n: any) => n.is_known_account);
      const elements: cytoscape.ElementDefinition[] = [];
      seedNodes.forEach((n: any) => {
        elements.push({
          group: "nodes",
          data: {
            id: String(n.id),
            label: n.label || n.id,
            account_holder: n.account_holder,
            is_known_account: n.is_known_account,
            role: n.role,
            total_transactions: n.total_transactions,
            total_received: n.total_received,
            total_forwarded: n.total_forwarded,
            max_anomaly_score: n.max_anomaly_score,
            in_degree: n.in_degree,
            out_degree: n.out_degree,
            risk_score: n.risk_score || 0,
            risk_tier: n.risk_tier || "LOW",
            bank_name: n.bank_name || "",
            bank: n.bank_name || "",
            is_seed: n.is_known_account,
            is_internal: n.role !== "source" && n.role !== "destination",
          }
        });
      });
      return elements;
    }

    // Isolated Mode: Focus on selectedAccountId
    if (selectedAccountId) {
      edges = edges.filter((e: any) => String(e.source) === String(selectedAccountId) || String(e.target) === String(selectedAccountId));
      
      // Temporal Filtering
      if (targetDateStr) {
        const targetTime = new Date(targetDateStr).getTime();
        const offsetMs = dayOffset * 24 * 60 * 60 * 1000;
        const minTime = targetTime - offsetMs;
        const maxTime = targetTime + offsetMs;
        
        edges = edges.filter((e: any) => {
          const edgeDateStr = e.datetime ? e.datetime.split(" ")[0] : (e.dates && e.dates[0]) || "";
          if (!edgeDateStr) return true; // Keep if no date available
          const t = new Date(edgeDateStr).getTime();
          return t >= minTime && t <= maxTime;
        });
      }
    }

    // Filter edges by amount, anomaly, mode, flags
    const filteredEdges = edges.filter((e: any) => {
      if (e.amount < minAmount) return false;
      if (flaggedOnly && e.is_flagged !== 1) return false;
      if (e.anomaly_score !== undefined && e.anomaly_score < minAnomalyScore) return false;
      if (selectedModes.length > 0 && e.mode && !selectedModes.includes(e.mode)) return false;
      return true;
    });

    // Collect active node IDs
    const connectedNodeIds = new Set<string>();
    filteredEdges.forEach((e: any) => {
      connectedNodeIds.add(String(e.source));
      connectedNodeIds.add(String(e.target));
    });

    // Filter nodes
    const filteredNodes = nodes.filter((n: any) => {
      // Risk Tier filter
      if (
        selectedRiskTiers.length > 0 &&
        n.risk_tier &&
        !selectedRiskTiers.includes(n.risk_tier.toUpperCase())
      ) {
        return false;
      }

      // Search Query filter
      if (searchQuery.trim() !== "") {
        const query = searchQuery.toLowerCase();
        const idMatch = String(n.id).toLowerCase().includes(query);
        const holderMatch = n.account_holder?.toLowerCase().includes(query);
        const bankMatch = n.bank_name?.toLowerCase().includes(query);
        return idMatch || holderMatch || bankMatch;
      }

      // Always include the selected account node in isolated mode, even if no edges match
      if (String(n.id) === String(selectedAccountId)) return true;

      return connectedNodeIds.has(String(n.id));
    });

    const finalNodeIds = new Set(filteredNodes.map((n: any) => String(n.id)));
    const finalEdges = filteredEdges.filter(
      (e: any) => e.source && e.target && finalNodeIds.has(String(e.source)) && finalNodeIds.has(String(e.target))
    );

    // Format elements with nested "data" property for Cytoscape compatibility
    const elements: cytoscape.ElementDefinition[] = [];
    
    filteredNodes.forEach((n: any) => {
      elements.push({
        group: "nodes",
        data: {
          id: String(n.id),
          label: n.label || n.id,
          account_holder: n.account_holder,
          is_known_account: n.is_known_account,
          role: n.role,
          total_transactions: n.total_transactions,
          total_received: n.total_received,
          total_forwarded: n.total_forwarded,
          max_anomaly_score: n.max_anomaly_score,
          in_degree: n.in_degree,
          out_degree: n.out_degree,
          risk_score: n.risk_score || 0,
          risk_tier: n.risk_tier || "LOW",
          bank_name: n.bank_name || "",
          // Compatibility keys for previous styling/inspector
          bank: n.bank_name || "",
          is_seed: n.is_known_account,
          is_internal: n.role !== "source" && n.role !== "destination",
        }
      });
    });

    finalEdges.forEach((e: any) => {
      elements.push({
        group: "edges",
        data: {
          id: String(e.id),
          source: String(e.source),
          target: String(e.target),
          amount: Number(e.amount || 0),
          mode: e.mode || "",
          transaction_id: e.transaction_id || "",
          narration: e.narration || "",
          datetime: e.datetime || "",
          direction: e.direction || "",
          is_flagged: e.is_flagged,
          anomaly_score: e.anomaly_score,
          // Compatibility keys for previous styling/inspector
          dates: [e.datetime ? e.datetime.split(" ")[0] : ""],
          risk_flag: e.is_flagged === 1 ? "SUSPICIOUS" : "NORMAL",
        }
      });
    });

    return elements;
  }, [graphData, minAmount, minAnomalyScore, searchQuery, selectedRiskTiers, selectedModes, flaggedOnly, isOverviewMode, selectedAccountId, targetDateStr, dayOffset]);

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
              return lbl.length > 15 ? lbl.substring(0, 13) + "..." : lbl;
            },
            "width": (node: any) => (node.data("is_seed") ? 42 : 30),
            "height": (node: any) => (node.data("is_seed") ? 42 : 30),
            "background-color": (node: any) => {
              const tier = String(node.data("risk_tier")).toUpperCase();
              if (tier === "CRITICAL") return "#f43f5e"; // Rose red
              if (tier === "HIGH") return "#f97316"; // Orange
              if (tier === "MEDIUM") return "#eab308"; // Yellow
              return "#10b981"; // Emerald green
            },
            "color": "#f8fafc",
            "font-size": "9px",
            "font-family": "Inter, system-ui, sans-serif",
            "text-valign": "bottom",
            "text-margin-y": 5,
            "border-width": (node: any) => (node.data("is_seed") ? 3 : 1.5),
            "border-color": "#ffffff",
            "border-opacity": 0.8,
            "overlay-padding": "4px",
            "overlay-opacity": 0,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-width": 4,
            "border-color": "#6366f1",
            "border-opacity": 1,
          },
        },
        {
          selector: "edge",
          style: {
            "width": (edge: any) => Math.min(Math.max(Math.log10(edge.data("amount") || 1) * 1.5, 1.5), 6),
            "line-color": (edge: any) => (edge.data("is_flagged") === 1 || edge.data("risk_flag") === "SUSPICIOUS" ? "#f43f5e" : "#475569"),
            "target-arrow-color": (edge: any) => (edge.data("is_flagged") === 1 || edge.data("risk_flag") === "SUSPICIOUS" ? "#f43f5e" : "#475569"),
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
            "color": "#94a3b8",
            "text-background-opacity": 0.9,
            "text-background-color": "#0f172a",
            "text-background-padding": "2px",
            "text-background-shape": "roundrectangle",
            "text-rotation": "autorotate",
            "arrow-scale": 0.85,
          },
        },
      ],
      layout: {
        name: layoutName,
        padding: 40,
        animate: true,
        animationDuration: 500,
      } as any,
    });

    cyInstance.current = cy;

    cy.on("tap", "node", (evt) => {
      const data = evt.target.data();
      handleHighlightNode(data.id);
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
            {activeSubView === "graph" && (
              <div className="mt-6 rounded-xl border border-slate-800 bg-slate-950 p-5 w-full text-slate-100 flex flex-col gap-4 shadow-2xl">
                {/* Header Section */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-3">
                      <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                        <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                        Dynamic money flow graph
                        <span className="text-xs font-normal text-slate-400 ml-1">
                          — File-based AML visualization sandbox
                        </span>
                      </h3>
                      {!isOverviewMode && (
                        <button
                          onClick={() => {
                            setIsOverviewMode(true);
                            setSelectedAccountId(null);
                            setTargetDateStr("");
                          }}
                          className="text-[10px] bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 border border-indigo-500/30 px-2.5 py-0.5 rounded-full font-semibold transition-colors flex items-center gap-1"
                        >
                          <span>←</span> Back to Overview
                        </button>
                      )}
                    </div>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Trace flow paths and isolate suspects across all uploaded transaction statements.
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex gap-1 bg-slate-900 p-1 rounded-lg border border-slate-800">
                      <button
                        onClick={() => cyInstance.current?.zoom(cyInstance.current.zoom() * 1.2)}
                        className="rounded hover:bg-slate-800 p-1 px-2 text-xs font-semibold text-slate-300 transition-colors"
                        title="Zoom In"
                      >
                        ＋
                      </button>
                      <button
                        onClick={() => cyInstance.current?.zoom(cyInstance.current.zoom() / 1.2)}
                        className="rounded hover:bg-slate-800 p-1 px-2 text-xs font-semibold text-slate-300 transition-colors"
                        title="Zoom Out"
                      >
                        －
                      </button>
                      <button
                        onClick={() => {
                          cyInstance.current?.fit();
                          cyInstance.current?.center();
                        }}
                        className="rounded hover:bg-slate-800 p-1 px-2 text-xs font-semibold text-slate-300 transition-colors"
                        title="Fit Window"
                      >
                        ⛶
                      </button>
                    </div>
                  </div>
                </div>

                {/* Main Three-Column Layout */}
                <div className="flex flex-col lg:flex-row gap-5 min-h-[580px] relative">
                  
                  {/* Left Column: Visual Filters */}
                  <aside className="w-full lg:w-60 shrink-0 bg-slate-900/60 p-4 border border-slate-800 rounded-xl flex flex-col gap-5 overflow-y-auto">
                    
                    {/* Temporal Filter (Only in Isolated Mode) */}
                    {!isOverviewMode && (
                      <div className="bg-slate-950/50 p-3 rounded-lg border border-slate-800/80 shadow-inner">
                        <label className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider block mb-2 flex items-center gap-1.5">
                          <span className="text-xs">⏱</span> Time Filter
                        </label>
                        <div className="mb-3">
                          <label className="text-[9px] font-semibold text-slate-400 block mb-1">Target Date</label>
                          <input
                            type="date"
                            value={targetDateStr}
                            onChange={(e) => setTargetDateStr(e.target.value)}
                            className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-[10px] text-slate-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                          />
                        </div>
                        <div>
                          <label className="text-[9px] font-semibold text-slate-400 flex justify-between mb-1">
                            <span>Window (+/- Days)</span>
                            <span className="text-indigo-300 font-bold">{dayOffset}d</span>
                          </label>
                          <input
                            type="range"
                            min="0"
                            max="180"
                            step="1"
                            value={dayOffset}
                            onChange={(e) => setDayOffset(Number(e.target.value))}
                            className="w-full accent-indigo-500 h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer"
                          />
                          <div className="flex justify-between text-[8px] text-slate-500 mt-1">
                            <span>0</span>
                            <span>180</span>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Search */}
                    <div>
                      <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">Search Account</label>
                      <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Search ID, Name, Bank..."
                        className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
                      />
                    </div>

                    {/* Min Amount */}
                    <div>
                      <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">Min Amount (₹)</label>
                      <input
                        type="number"
                        value={minAmount}
                        onChange={(e) => setMinAmount(Number(e.target.value))}
                        className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-1.5 text-xs focus:outline-none text-slate-200 mb-1.5"
                      />
                      <input
                        type="range"
                        min="0"
                        max="25000"
                        step="500"
                        value={minAmount}
                        onChange={(e) => setMinAmount(Number(e.target.value))}
                        className="w-full accent-indigo-500"
                      />
                      <div className="flex justify-between text-[9px] text-slate-500 mt-1">
                        <span>₹0</span>
                        <span>₹25,000</span>
                      </div>
                    </div>

                    {/* Date Filter Slider */}
                    {(() => {
                      const allDates = Array.from(new Set(graphData?.edges?.flatMap(e => {
                        const base = e?.data || e;
                        return base?.dates || ((base as any)?.datetime ? [(base as any).datetime.split(" ")[0]] : []);
                      }) || [])).sort();
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


                    {/* Risk Tier */}
                    <div>
                      <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">Risk Level</label>
                      <div className="flex flex-wrap gap-1.5">
                        {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((tier) => {
                          const isSelected = selectedRiskTiers.includes(tier);
                          return (
                            <button
                              key={tier}
                              onClick={() =>
                                setSelectedRiskTiers((prev) =>
                                  prev.includes(tier) ? prev.filter((t) => t !== tier) : [...prev, tier]
                                )
                              }
                              className={`rounded px-2 py-0.5 text-[9px] font-medium transition-all ${
                                isSelected
                                  ? "bg-indigo-600 text-white"
                                  : "bg-slate-950 border border-slate-800 text-slate-400 hover:bg-slate-900"
                              }`}
                            >
                              {tier}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Modes */}
                    <div>
                      <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">Channels</label>
                      <div className="flex flex-wrap gap-1.5">
                        {["UPI", "NEFT", "IMPS", "RTGS"].map((mode) => {
                          const isSelected = selectedModes.includes(mode);
                          return (
                            <button
                              key={mode}
                              onClick={() =>
                                setSelectedModes((prev) =>
                                  prev.includes(mode) ? prev.filter((m) => m !== mode) : [...prev, mode]
                                )
                              }
                              className={`rounded px-2 py-0.5 text-[9px] font-medium transition-all ${
                                isSelected
                                  ? "bg-blue-600 text-white"
                                  : "bg-slate-950 border border-slate-800 text-slate-400 hover:bg-slate-900"
                              }`}
                            >
                              {mode}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Flagged Status */}
                    <div className="flex items-center justify-between border-t border-slate-800 pt-3">
                      <span className="text-xs text-slate-300">Flagged Only</span>
                      <button
                        onClick={() => setFlaggedOnly(!flaggedOnly)}
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                          flaggedOnly ? "bg-red-500" : "bg-slate-800"
                        }`}
                      >
                        <span
                          className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                            flaggedOnly ? "translate-x-4" : "translate-x-0"
                          }`}
                        />
                      </button>
                    </div>

                    {/* Layout switch */}
                    <div className="mt-auto border-t border-slate-800 pt-3">
                      <label className="text-[10px] font-semibold text-slate-400 uppercase block mb-1">Graph Layout</label>
                      <select
                        value={layoutName}
                        onChange={(e) => setLayoutName(e.target.value)}
                        className="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-xs text-slate-200 focus:outline-none"
                      >
                        <option value="cose">CoSE (Organic Force-Directed)</option>
                        <option value="concentric">Concentric (Radial Tiers)</option>
                        <option value="grid">Grid (Clean Layout)</option>
                        <option value="circle">Circle (Circular)</option>
                      </select>
                    </div>
                  </aside>

                  {/* Middle Column: Visual Canvas */}
                  <main className="flex-1 min-h-[450px] relative bg-slate-950 border border-slate-800 rounded-xl overflow-hidden flex flex-col justify-end">
                    {/* Grid lines background style */}
                    <div
                      className="absolute inset-0 pointer-events-none opacity-[0.03]"
                      style={{
                        backgroundImage:
                          "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
                        backgroundSize: "20px 20px",
                      }}
                    />

                    {/* Cytoscape element container */}
                    <div ref={cyRef} className="absolute inset-0 w-full h-full" />

                    {graphLoading && (
                      <div className="absolute inset-0 bg-slate-950/80 z-20 flex flex-col items-center justify-center gap-3">
                        <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
                        <p className="text-xs text-slate-400 font-medium">Reconstructing money flow network...</p>
                      </div>
                    )}

                    {/* Legend Overlay */}
                    <div className="absolute bottom-3 left-3 bg-slate-950/80 backdrop-blur-md border border-slate-800 rounded-lg p-2 flex flex-wrap gap-x-3 gap-y-1 text-[9px] text-slate-400 z-10 pointer-events-none">
                      <div className="flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full bg-blue-500 border border-white inline-block" />
                        <span className="font-semibold text-slate-300">Seed Node</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full bg-rose-500 inline-block" />
                        <span>Critical</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full bg-orange-500 inline-block" />
                        <span>High</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full bg-yellow-500 inline-block" />
                        <span>Medium</span>
                      </div>
                      <div className="flex items-center gap-1.5 border-l border-slate-800 pl-3">
                        <span className="h-[2px] w-4 bg-slate-500 inline-block" />
                        <span>Normal Flow</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="h-[2px] w-4 bg-rose-500 inline-block" />
                        <span className="text-rose-400 font-semibold">Flagged Flow</span>
                      </div>
                    </div>
                  </main>

                  {/* Right Column: Selection Inspector */}
                  <aside className="w-full lg:w-76 shrink-0 bg-slate-900/60 p-4 border border-slate-800 rounded-xl flex flex-col gap-4 overflow-y-auto justify-between">
                    {selectedNodeData ? (
                      <div className="flex flex-col gap-4 h-full justify-between">
                        <div className="flex flex-col gap-3">
                          <div>
                            <span className="text-[9px] uppercase font-bold text-indigo-400 tracking-wider">Account Node</span>
                            <h3 className="text-xs font-semibold text-slate-100 mt-0.5 select-all font-mono">{selectedNodeData.id}</h3>
                          </div>

                          {selectedNodeData.account_holder && (
                            <div className="bg-slate-950/50 p-2 rounded-lg border border-slate-800">
                              <span className="text-[9px] text-slate-500 block">Account Holder</span>
                              <span className="text-xs font-semibold text-slate-200">{selectedNodeData.account_holder}</span>
                            </div>
                          )}

                          {selectedNodeData.bank && (
                            <div className="bg-slate-950/50 p-2 rounded-lg border border-slate-800">
                              <span className="text-[9px] text-slate-500 block">Bank Name</span>
                              <span className="text-xs font-semibold text-slate-200">{selectedNodeData.bank}</span>
                            </div>
                          )}

                          <div className="grid grid-cols-2 gap-2">
                            <div className="bg-slate-950/50 p-2 rounded-lg border border-slate-800 text-center">
                              <span className="text-[9px] text-slate-500 block">Risk Score</span>
                              <span className="text-xs font-bold text-red-400">{selectedNodeData.risk_score || 0}%</span>
                            </div>
                            <div className="bg-slate-950/50 p-2 rounded-lg border border-slate-800 text-center">
                              <span className="text-[9px] text-slate-500 block">Risk Tier</span>
                              <span className={`text-[9px] font-extrabold uppercase mt-0.5 px-1 rounded inline-block ${
                                selectedNodeData.risk_tier === "CRITICAL"
                                  ? "bg-red-500/10 text-red-400 border border-red-500/20"
                                  : selectedNodeData.risk_tier === "HIGH"
                                  ? "bg-orange-500/10 text-orange-400 border border-orange-500/20"
                                  : "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                              }`}>
                                {selectedNodeData.risk_tier || "LOW"}
                              </span>
                            </div>
                          </div>

                          <div className="bg-slate-950/30 rounded-lg border border-slate-800/80 p-2.5 flex flex-col gap-1.5">
                            <div className="flex justify-between text-[11px]">
                              <span className="text-slate-400">Node Role</span>
                              <span className="capitalize font-medium text-slate-200">{selectedNodeData.role || "Unknown"}</span>
                            </div>
                            <div className="flex justify-between text-[11px]">
                              <span className="text-slate-400">Total Txns</span>
                              <span className="font-semibold text-slate-200">{selectedNodeData.total_transactions || 0}</span>
                            </div>
                            <div className="flex justify-between text-[11px]">
                              <span className="text-slate-400">In / Out Degree</span>
                              <span className="font-semibold text-slate-200">{selectedNodeData.in_degree || 0} / {selectedNodeData.out_degree || 0}</span>
                            </div>
                            <div className="flex justify-between text-[11px]">
                              <span className="text-slate-400">Total Inflow</span>
                              <span className="font-semibold text-emerald-400">₹{selectedNodeData.total_received?.toLocaleString() || 0}</span>
                            </div>
                            <div className="flex justify-between text-[11px]">
                              <span className="text-slate-400">Total Outflow</span>
                              <span className="font-semibold text-red-400">₹{selectedNodeData.total_forwarded?.toLocaleString() || 0}</span>
                            </div>
                          </div>
                        <div className="border-t border-slate-200 pt-3">
                          <button
                            onClick={() => onOpenMoneyTrail && onOpenMoneyTrail(selectedNodeData.id)}
                            className="w-full flex items-center justify-between rounded-lg border border-accent/30 bg-accent/5 px-3 py-2.5 text-xs font-semibold text-accent transition-colors hover:bg-accent/10"
                          >
                            <span>💰 Open Money Trail for this account</span>
                            <span>&rarr;</span>
                          </button>
                        </div>

                        {/* Database Transactions sub-table */}
                        <div className="flex flex-col min-h-0">
                          <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Account Transaction Ledger</p>
                          {nodeTransactionsLoading ? (
                            <div className="text-center text-[11px] text-slate-500 py-3 flex flex-col items-center gap-1.5">
                              <div className="animate-spin rounded-full h-3 w-3 border border-indigo-500 border-t-transparent" />
                              Loading database transactions...
                            </div>
                          ) : nodeTransactions.length > 0 ? (
                            <div className="overflow-y-auto border border-slate-800 rounded-lg max-h-[160px] bg-slate-950">
                              <table className="w-full text-left text-[10px]">
                                <thead className="bg-slate-900 text-slate-400 font-semibold sticky top-0 uppercase text-[9px]">
                                  <tr>
                                    <th className="px-2 py-1">Date</th>
                                    <th className="px-2 py-1">Narration</th>
                                    <th className="px-2 py-1 text-right">Amount</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-900 bg-slate-950">
                                  {nodeTransactions.map((t, idx) => {
                                    const isDebit = t.debit > 0;
                                    const amount = isDebit ? t.debit : t.credit;
                                    const isSuspicious = t.is_high_value_flag || t.is_balance_breach || (t.final_risk_score && t.final_risk_score >= 0.7);
                                    return (
                                      <tr key={t.id || idx} className={`hover:bg-slate-900/60 ${isSuspicious ? "bg-red-500/5" : ""}`}>
                                        <td className="px-2 py-1 whitespace-nowrap text-slate-500 font-mono text-[9px]">{t.date}</td>
                                        <td className="px-2 py-1 text-slate-300 truncate max-w-[100px]" title={t.narration}>{t.narration}</td>
                                        <td className={`px-2 py-1 text-right font-mono font-semibold ${isDebit ? "text-red-400" : "text-emerald-400"}`}>
                                          {isDebit ? "-" : "+"}₹{amount.toLocaleString("en-IN")}
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          ) : (
                            <p className="text-[10px] text-slate-500 text-center py-3 bg-slate-950 border border-dashed border-slate-800 rounded-lg">No database transactions found for this account.</p>
                          )}
                        </div>
                      </div>

                      {/* Trace buttons */}
                      {!selectedNodeData.is_seed && (
                        <div className="pt-2">
                          <button
                            onClick={() => {
                              setSelectedAccountId(selectedNodeData.id);
                            }}
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
                </aside>
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
