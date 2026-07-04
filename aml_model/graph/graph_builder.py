"""
graph_builder.py
──────────────────
Core money-flow graph engine for a Next.js frontend.
Outputs pure JSON (nodes + edges) — NO Pyvis, NO HTML rendering.
The frontend is responsible for rendering (react-force-graph, cytoscape.js-react,
vis-network-react, etc.) — this module only produces the data.

Key design points requested:

1. INCREMENTAL GRAPH BUILDING
   Expansion stops at a node once it's been added, UNLESS that account has
   more than `incremental_threshold` (default 30) total transactions —
   only "significant" accounts get expanded further. This keeps the graph
   small by default and lets the frontend "click to expand" a node that
   crosses the threshold, rather than eagerly pulling in everything.

2. SLIDER-DRIVEN FILTERS
   min_amount: drop every transaction below this rupee value before
               building the graph (frontend slider -> this param)
   date_from / date_to: restrict to a date window (frontend range slider)
   Filters are applied ONCE at load time to the in-memory transaction
   table, then the graph is rebuilt fast from the filtered table — no
   need to reload the CSV per filter change if you cache the GraphBuilder
   instance across requests (see api/graph_api.py).

3. WORKS FOR ALL TRANSACTIONS, NOT JUST FLAGGED
   Unlike money_flow_graph.py (investigation-focused, flagged-only),
   this builds from the FULL transaction set — appropriate for a general
   "explore all money flow" view the user asked for.
"""

import pandas as pd
import networkx as nx
from typing import Optional
from datetime import datetime


class GraphBuilder:

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.raw_df = self._load(csv_path)
        # Per-account total transaction count — used for incremental expansion decisions
        self.account_txn_counts = self.raw_df["account_id"].value_counts().to_dict()

    # ── loading ──────────────────────────────────────────────────────────────

    def _load(self, path: str) -> pd.DataFrame:
        df = pd.read_csv(path, dtype=str, low_memory=False)
        df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")

        # Flexible column detection — supports multiple naming conventions
        # seen across different uploaded files throughout this project
        account_col = next((c for c in ["account_id", "acc_id", "account_no", "account_number"]
                             if c in df.columns), None)
        if account_col is None:
            raise ValueError(f"No account ID column found. Available columns: {df.columns.tolist()}")
        if account_col != "account_id":
            df["account_id"] = df[account_col]

        debit_col  = next((c for c in ["debit_clean", "debit", "debit_amount", "withdrawal"]
                            if c in df.columns), None)
        credit_col = next((c for c in ["credit_clean", "credit", "credit_amount", "deposit"]
                            if c in df.columns), None)
        cp_col     = next((c for c in ["counterparty_account_id", "counterparty_account",
                                        "counterparty_acc", "cp_account_id", "beneficiary_account"]
                            if c in df.columns), None)

        df["debit"]  = pd.to_numeric(df.get(debit_col, 0), errors="coerce").fillna(0) if debit_col else 0.0
        df["credit"] = pd.to_numeric(df.get(credit_col, 0), errors="coerce").fillna(0) if credit_col else 0.0
        df["counterparty_account"] = df.get(cp_col) if cp_col else None
        df["amount"] = df[["debit", "credit"]].max(axis=1)

        if "is_flagged" in df.columns:
            df["is_flagged"] = pd.to_numeric(df["is_flagged"], errors="coerce").fillna(0).astype(int)
        else:
            df["is_flagged"] = 0  # unscored CSV — every account treated as unflagged by default

        if "anomaly_score" in df.columns:
            df["anomaly_score"] = pd.to_numeric(df["anomaly_score"], errors="coerce").fillna(0)
        else:
            df["anomaly_score"] = 0.0

        df["narration"] = df.get("narration", "").fillna("")

        # ── FALLBACK: when counterparty_account is missing/empty, derive a
        # pseudo-counterparty node from the narration text itself. Real bank
        # statements very often DON'T carry a clean beneficiary account number
        # (only a narration string like "IMPS-XXXX-JOHN DOE-SBIN0001234" or
        # "UPI/CR/.../MERCHANT NAME/..."). Without this, transactions with no
        # account-number counterparty would simply vanish from the graph.
        df["counterparty_account"] = df["counterparty_account"].where(
            df["counterparty_account"].notna() & (df["counterparty_account"].astype(str).str.strip() != ""),
            None
        )
        missing_cp = df["counterparty_account"].isna()
        df.loc[missing_cp, "counterparty_account"] = (
            "NARR::" + df.loc[missing_cp, "narration"].apply(self._extract_pseudo_counterparty)
        )

        date_col = next((c for c in ["datetime", "date", "transaction_date", "txn_date"]
                          if c in df.columns), None)
        df["datetime"] = pd.to_datetime(df.get(date_col), errors="coerce") if date_col else pd.NaT

        return df

    @staticmethod
    def _extract_pseudo_counterparty(narration: str) -> str:
        """
        Best-effort extraction of a stable "who" identifier from free-text
        narration when no formal account number is present. Falls back to a
        cleaned/truncated version of the narration itself as the node id if
        nothing structured can be pulled out — every transaction is still
        represented as an edge to SOMETHING, never silently dropped.
        """
        import re
        if not narration or not str(narration).strip():
            return "UNKNOWN"

        text = str(narration).strip().upper()

        # Common bank narration patterns: IMPS-<ref>-<NAME>-<IFSC>...
        # or UPI/<ref>/CR|DR/<NAME>/<bank>/...
        parts = re.split(r"[-/]", text)
        parts = [p.strip() for p in parts if p.strip()]

        # Prefer a part that looks like a name (letters/spaces, not a pure
        # number/reference code, length 3-40)
        for p in parts:
            if re.fullmatch(r"[A-Z .]{3,40}", p) and not p.isdigit():
                if p not in {"CR", "DR", "IMPS", "UPI", "NEFT", "RTGS", "TRANSFER", "PAYMENT"}:
                    return p

        # Nothing structured found — use a short hash-stable slice of the
        # narration so the SAME narration always maps to the SAME node
        return text[:30] if text else "UNKNOWN"

    # ── apply slider filters (amount + date range) ───────────────────────────

    def filtered_df(self, min_amount: float = 0, date_from: Optional[str] = None,
                     date_to: Optional[str] = None) -> pd.DataFrame:
        df = self.raw_df
        df = df[df["amount"] >= min_amount]

        if date_from:
            df = df[df["datetime"] >= pd.to_datetime(date_from)]
        if date_to:
            df = df[df["datetime"] <= pd.to_datetime(date_to)]

        return df

    # ── build a directed edge graph from a (possibly filtered) dataframe ────

    def _build_graph(self, df: pd.DataFrame) -> nx.MultiDiGraph:
        """
        Every transaction becomes exactly one directed edge:
          - a debit row  -> account_id --sends--> counterparty  (money OUT)
          - a credit row -> counterparty --sends--> account_id  (money IN)
        This ensures accounts that ONLY receive (or only send) still produce
        edges, instead of silently vanishing from the graph.
        """
        G = nx.MultiDiGraph()

        outgoing = df[df["debit"] > 0]
        for _, row in outgoing.iterrows():
            G.add_edge(
                row["account_id"], row["counterparty_account"],
                amount=float(row["debit"]),
                narration=str(row.get("narration", ""))[:80],
                datetime=str(row["datetime"]),
                is_flagged=int(row["is_flagged"]),
                anomaly_score=float(row["anomaly_score"]),
            )

        incoming = df[(df["credit"] > 0) & (df["debit"] == 0)]
        for _, row in incoming.iterrows():
            G.add_edge(
                row["counterparty_account"], row["account_id"],
                amount=float(row["credit"]),
                narration=str(row.get("narration", ""))[:80],
                datetime=str(row["datetime"]),
                is_flagged=int(row["is_flagged"]),
                anomaly_score=float(row["anomaly_score"]),
            )

        return G

    # ── INCREMENTAL expansion: only "significant" nodes get expanded further ─

    def build_incremental_subgraph(
        self,
        seed: str,
        min_amount: float = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        incremental_threshold: int = 30,
        max_hops: int = 3,
        max_nodes: int = 200,
    ) -> dict:
        """
        Starts from `seed` and expands outward. A node is only expanded
        (its own neighbors pulled in) if account_txn_counts[node] > threshold.
        Nodes at or below the threshold are still SHOWN (as leaves) but not
        expanded further — this is the "click to build incremental graph"
        behaviour: low-activity accounts terminate the branch, high-activity
        ("significant") accounts keep growing the graph.

        Returns {"nodes": [...], "edges": [...], "meta": {...}} — ready to
        JSON-serialize straight to the Next.js frontend.
        """
        df = self.filtered_df(min_amount, date_from, date_to)
        full_graph = self._build_graph(df)

        if seed not in full_graph:
            return {"nodes": [], "edges": [], "meta": {"error": f"'{seed}' not found in filtered data"}}

        visited = {seed}
        frontier = [seed]
        expanded_nodes = set()

        for _ in range(max_hops):
            next_frontier = []
            for node in frontier:
                txn_count = self.account_txn_counts.get(node, 0)

                # Only expand this node's neighbors if it crosses the significance threshold
                if txn_count <= incremental_threshold and node != seed:
                    continue  # leaf — shown, but not expanded further

                expanded_nodes.add(node)
                for neighbor in set(full_graph.successors(node)) | set(full_graph.predecessors(node)):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)

                if len(visited) >= max_nodes:
                    break
            frontier = next_frontier
            if len(visited) >= max_nodes or not frontier:
                break

        subgraph = full_graph.subgraph(visited).copy()
        return self._to_json(subgraph, expanded_nodes, incremental_threshold)

    # ── expand a single node on demand (the "click node to expand" action) ──

    def expand_node(
        self,
        node: str,
        min_amount: float = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> dict:
        """
        Returns just the immediate neighbors + connecting edges for one node —
        call this when the frontend user clicks a node that has crossed the
        incremental_threshold, to grow the graph one step further without
        rebuilding everything from scratch.
        """
        df = self.filtered_df(min_amount, date_from, date_to)
        full_graph = self._build_graph(df)

        if node not in full_graph:
            return {"nodes": [], "edges": [], "meta": {"error": f"'{node}' not found"}}

        neighbors = set(full_graph.successors(node)) | set(full_graph.predecessors(node))
        induced = full_graph.subgraph({node} | neighbors).copy()
        return self._to_json(induced, expanded_nodes={node}, threshold=None)

    # ── convert a networkx graph into clean JSON for the frontend ───────────

    def _to_json(self, G: nx.MultiDiGraph, expanded_nodes: set, threshold: Optional[int]) -> dict:
        nodes = []
        for n in G.nodes():
            txn_count = self.account_txn_counts.get(n, 0)
            in_amt  = sum(d["amount"] for _, _, d in G.in_edges(n, data=True))
            out_amt = sum(d["amount"] for _, _, d in G.out_edges(n, data=True))
            max_risk = max([d["anomaly_score"] for _, _, d in G.in_edges(n, data=True)] +
                            [d["anomaly_score"] for _, _, d in G.out_edges(n, data=True)] + [0])

            role = "destination" if (G.in_degree(n) > 0 and G.out_degree(n) == 0) else \
                   "source" if (G.out_degree(n) > 0 and G.in_degree(n) == 0) else \
                   "intermediary" if (G.in_degree(n) > 0 and G.out_degree(n) > 0) else "isolated"

            nodes.append({
                "id": n,
                "label": n,
                "role": role,
                "total_transactions": txn_count,
                "is_expanded": n in expanded_nodes,
                "is_expandable": (threshold is not None) and (txn_count > threshold) and (n not in expanded_nodes),
                "total_received": round(in_amt, 2),
                "total_forwarded": round(out_amt, 2),
                "max_anomaly_score": round(max_risk, 4),
                "in_degree": G.in_degree(n),
                "out_degree": G.out_degree(n),
            })

        edges = []
        for u, v, k, d in G.edges(keys=True, data=True):
            edges.append({
                "id": f"{u}->{v}->{k}",
                "source": u,
                "target": v,
                "amount": round(d["amount"], 2),
                "narration": d.get("narration", ""),
                "datetime": d.get("datetime", ""),
                "is_flagged": d.get("is_flagged", 0),
                "anomaly_score": round(d.get("anomaly_score", 0), 4),
            })

        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "incremental_threshold": threshold,
            },
        }

    # ── full-dataset stats, useful for setting slider bounds in the UI ──────

    def get_filter_bounds(self) -> dict:
        return {
            "min_amount_possible": float(self.raw_df["amount"].min()),
            "max_amount_possible": float(self.raw_df["amount"].max()),
            "date_from_possible": str(self.raw_df["datetime"].min()),
            "date_to_possible": str(self.raw_df["datetime"].max()),
            "total_transactions": len(self.raw_df),
            "total_accounts": self.raw_df["account_id"].nunique(),
        }