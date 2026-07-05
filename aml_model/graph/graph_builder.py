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
        # account_id -> holder name (for known/uploaded accounts; counterparty
        # pseudo-nodes won't have an entry here, which is fine — they display
        # their node id as the label instead)
        self._account_holder_map = self._build_holder_map(self.raw_df)
        # Full per-account dashboard stats, computed once and cached
        self._account_summary_cache: dict = {}
        
        # Load risk scores map from the same directory if risk_scores.csv exists
        import os
        dir_name = os.path.dirname(csv_path)
        risk_csv = os.path.join(dir_name, "risk_scores.csv")
        self._risk_scores_map = {}
        if os.path.exists(risk_csv):
            try:
                risk_df = pd.read_csv(risk_csv, dtype=str)
                for _, row in risk_df.iterrows():
                    self._risk_scores_map[str(row["account_id"]).strip()] = {
                        "risk_score": float(row.get("risk_score", 0.0)),
                        "risk_tier": str(row.get("risk_tier", "LOW")).upper()
                    }
            except Exception as e:
                print(f"[GraphBuilder] Failed to load risk_scores.csv: {e}")

    def _build_holder_map(self, df: pd.DataFrame) -> dict:
        holder_col = df[["account_id", "account_holder"]].dropna(subset=["account_holder"])
        holder_col = holder_col[holder_col["account_holder"].astype(str).str.strip() != ""]
        return holder_col.drop_duplicates("account_id").set_index("account_id")["account_holder"].to_dict()

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

        # Check Phase 8 flag columns to set is_flagged
        flag_cols = [
            "is_round_trip", "is_round_trip_cycle", "is_layering", 
            "is_fan_in", "is_fan_out", "is_smurfing", "is_odd_hour"
        ]
        combined_flag = pd.Series(0, index=df.index)
        for col in flag_cols:
            if col in df.columns:
                col_flag = df[col].astype(str).str.lower().isin(["true", "1", "yes"])
                combined_flag = combined_flag | col_flag.astype(int)

        if "is_flagged" in df.columns:
            df["is_flagged"] = pd.to_numeric(df["is_flagged"], errors="coerce").fillna(0).astype(int) | combined_flag
        else:
            df["is_flagged"] = combined_flag

        if "anomaly_score" in df.columns:
            df["anomaly_score"] = pd.to_numeric(df["anomaly_score"], errors="coerce").fillna(0)
        else:
            df["anomaly_score"] = 0.0

        df["narration"] = df.get("narration", "").fillna("")
        df["channel"] = df.get("channel", "").fillna("") if "channel" in df.columns else ""
        df["transaction_id"] = df.get("transaction_id", "") if "transaction_id" in df.columns else ""
        df["account_holder"] = df.get("account_holder", "") if "account_holder" in df.columns else ""
        df["bank_name"] = df.get("bank_name", "") if "bank_name" in df.columns else ""
        df["balance"] = pd.to_numeric(df.get("balance_clean", df.get("balance", None)), errors="coerce") \
                         if ("balance_clean" in df.columns or "balance" in df.columns) else None

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

        # Common bank narration patterns:
        #   IMPS-<ref>-<NAME>-<IFSC>...
        #   UPI/<ref>/CR|DR/<NAME>/<bank>/...
        #   UPIAR/<ref>/DR/<NAME>/<bank>/<vpa>   (UPIAR = UPI outward channel code)
        #   UPIAB/<ref>/CR/<NAME>/<bank>/<vpa>   (UPIAB = UPI inward channel code)
        parts = re.split(r"[-/]", text)
        parts = [p.strip() for p in parts if p.strip()]

        # Channel/transaction-type tokens to ALWAYS skip — these are bank
        # codes, never the counterparty's identity, regardless of position.
        SKIP_TOKENS = {
            "CR", "DR", "IMPS", "UPI", "UPIAR", "UPIAB", "UPIAC", "UPICR", "UPIDR",
            "NEFT", "RTGS", "TRANSFER", "PAYMENT", "ANN", "FEE", "INT", "PD",
            "CHARGES", "SC", "GST", "FT", "OTHER", "CHEQUE", "SMS", "QTR",
        }

        # Prefer a part that looks like a name (letters/spaces, not a pure
        # number/reference code, length 3-40), skipping known channel codes
        # and skipping the FIRST token entirely (it's almost always the
        # channel code even if it happens to pass the regex, e.g. "UPIAR").
        for i, p in enumerate(parts):
            if i == 0:
                continue  # first token is the channel/transaction-type code
            if p in SKIP_TOKENS:
                continue
            if re.fullmatch(r"[A-Z .]{3,40}", p) and not p.isdigit():
                return p.strip()

        # Nothing structured found after skipping channel codes — fall back
        # to a short hash-stable slice of the FULL narration (excluding the
        # leading channel code + numeric reference) so at least similar
        # transactions to the same unknown party still group together.
        remainder = "-".join(parts[2:]) if len(parts) > 2 else text
        return remainder[:30] if remainder else "UNKNOWN"

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
                channel=str(row.get("channel", "")),
                transaction_id=str(row.get("transaction_id", "")),
                datetime=str(row["datetime"]),
                is_flagged=int(row["is_flagged"]),
                anomaly_score=float(row["anomaly_score"]),
                direction="debit",
            )

        incoming = df[(df["credit"] > 0) & (df["debit"] == 0)]
        for _, row in incoming.iterrows():
            G.add_edge(
                row["counterparty_account"], row["account_id"],
                amount=float(row["credit"]),
                narration=str(row.get("narration", ""))[:80],
                channel=str(row.get("channel", "")),
                transaction_id=str(row.get("transaction_id", "")),
                datetime=str(row["datetime"]),
                is_flagged=int(row["is_flagged"]),
                anomaly_score=float(row["anomaly_score"]),
                direction="credit",
            )

        return G

    # ── INCREMENTAL expansion: only "significant" nodes get expanded further ─

    def build_incremental_subgraph(
        self,
        seed,   # str OR list[str] — pass all uploaded account_ids to seed the initial view
        min_amount: float = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        incremental_threshold: int = 30,
        max_hops: int = 3,
        max_nodes: int = 200,
    ) -> dict:
        """
        Starts from one or more `seed` accounts (e.g. every account_id from
        the uploaded statements) and expands outward. A node is only
        expanded (its own neighbors pulled in) if account_txn_counts[node]
        > threshold. Nodes at or below the threshold are still SHOWN (as
        leaves) but not expanded further.

        Passing a LIST of seeds means the initial graph shows ALL of them
        as nodes immediately — this is the "I uploaded 5 statements, show
        me 5 nodes to start" workflow.

        Returns {"nodes": [...], "edges": [...], "meta": {...}} — ready to
        JSON-serialize straight to the Next.js frontend.
        """
        seeds = [seed] if isinstance(seed, str) else list(seed)

        df = self.filtered_df(min_amount, date_from, date_to)
        full_graph = self._build_graph(df)

        valid_seeds = [s for s in seeds if s in full_graph]
        if not valid_seeds:
            return {"nodes": [], "edges": [],
                    "meta": {"error": f"None of the seed account(s) {seeds} found in filtered data"}}

        max_nodes = max(max_nodes, len(valid_seeds) * 10)

        visited = set(valid_seeds)
        frontier = list(valid_seeds)
        expanded_nodes = set()

        for hop in range(max_hops):
            next_frontier = []
            for node in frontier:
                txn_count = self.account_txn_counts.get(node, 0)

                # Uploaded/known seed accounts always expand at least once,
                # regardless of transaction count, so their counterparties
                # show up immediately in the initial view.
                if txn_count <= incremental_threshold and node not in valid_seeds:
                    continue  # leaf — shown, but not expanded further

                expanded_nodes.add(node)
                for neighbor in set(full_graph.successors(node)) | set(full_graph.predecessors(node)):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)

                if len(visited) >= max_nodes and node not in valid_seeds:
                    break
            frontier = next_frontier
            if len(visited) >= max_nodes and hop > 0:
                break

        subgraph = full_graph.subgraph(visited).copy()
        return self._to_json(subgraph, expanded_nodes, incremental_threshold, known_accounts=set(valid_seeds))

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

    # ── NODE CLICK: full account dashboard (holder, risk, activity summary) ─

    def get_account_dashboard(self, account_id: str) -> dict:
        """
        Returns everything needed for the side-panel dashboard when an
        investigator clicks a node: holder identity, risk profile, and
        activity summary. Works for both known/uploaded accounts (has a
        holder name) and counterparty/pseudo accounts (holder name is None).
        """
        if account_id in self._account_summary_cache:
            return self._account_summary_cache[account_id]

        df = self.raw_df[self.raw_df["account_id"] == account_id]

        # Also gather this account's role as a COUNTERPARTY across the
        # dataset (money it received/sent as someone else's counterparty),
        # in case it's a pseudo-node with no rows of its own.
        as_counterparty = self.raw_df[self.raw_df["counterparty_account"] == account_id]

        if df.empty and as_counterparty.empty:
            return {"error": f"Account '{account_id}' not found"}

        total_txns = len(df)
        flagged_txns = int(df["is_flagged"].sum()) if total_txns else 0
        flag_pct = round(flagged_txns / total_txns * 100, 1) if total_txns else 0.0
        avg_score = round(df["anomaly_score"].mean(), 4) if total_txns else 0.0
        max_score = round(df["anomaly_score"].max(), 4) if total_txns else 0.0

        total_debit = round(df["debit"].sum(), 2) if total_txns else 0.0
        total_credit = round(df["credit"].sum(), 2) if total_txns else 0.0

        holder = self._account_holder_map.get(account_id)
        bank_name = df["bank_name"].dropna().iloc[0] if total_txns and df["bank_name"].notna().any() else None

        date_min = df["datetime"].min() if total_txns else None
        date_max = df["datetime"].max() if total_txns else None

        # Risk tier label, mirroring the model's own tiering scheme
        if flag_pct >= 80: risk_tier = "Critical"
        elif flag_pct >= 65: risk_tier = "High"
        elif flag_pct >= 50: risk_tier = "Medium"
        elif flag_pct >= 30: risk_tier = "Low"
        else: risk_tier = "Very Low"

        result = {
            "account_id": account_id,
            "account_holder": holder,
            "bank_name": bank_name,
            "is_known_account": holder is not None,
            "total_transactions": total_txns,
            "flagged_transactions": flagged_txns,
            "flag_pct": flag_pct,
            "risk_tier": risk_tier,
            "avg_anomaly_score": avg_score,
            "max_anomaly_score": max_score,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "net_flow": round(total_credit - total_debit, 2),
            "activity_date_from": str(date_min) if date_min is not None else None,
            "activity_date_to": str(date_max) if date_max is not None else None,
            "appears_as_counterparty_count": len(as_counterparty),
        }
        self._account_summary_cache[account_id] = result
        return result

    # ── EDGE CLICK: full transaction detail (mode, amount, narration, date) ─

    def get_transaction_detail(self, source: str, target: str, transaction_id: str = None) -> list:
        """
        Returns the underlying transaction row(s) for a clicked edge —
        mode of transaction, exact amount, narration, date, flag status.
        If transaction_id is given, returns just that one row; otherwise
        returns all transactions that produced an edge between source/target.
        """
        df = self.raw_df

        if transaction_id:
            match = df[df.get("transaction_id", "") == transaction_id]
        else:
            match = df[
                ((df["account_id"] == source) & (df["counterparty_account"] == target)) |
                ((df["account_id"] == target) & (df["counterparty_account"] == source))
            ]

        results = []
        for _, row in match.iterrows():
            results.append({
                "transaction_id": row.get("transaction_id", ""),
                "account_id": row["account_id"],
                "counterparty_account": row["counterparty_account"],
                "mode": row.get("channel", "") or "UNKNOWN",
                "amount": round(float(row["debit"] if row["debit"] > 0 else row["credit"]), 2),
                "direction": "debit" if row["debit"] > 0 else "credit",
                "narration": row.get("narration", ""),
                "datetime": str(row["datetime"]),
                "is_flagged": int(row["is_flagged"]),
                "anomaly_score": round(float(row["anomaly_score"]), 4),
                "balance_after": round(float(row["balance"]), 2) if pd.notna(row.get("balance")) else None,
            })
        return results

    # ── convert a networkx graph into clean JSON for the frontend ───────────

    def _to_json(self, G: nx.MultiDiGraph, expanded_nodes: set, threshold: Optional[int],
                 known_accounts: Optional[set] = None) -> dict:
        known_accounts = known_accounts or set()
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

            holder_name = self._account_holder_map.get(n)
            is_known = n in known_accounts

            n_str = str(n).strip()
            if n_str in self._risk_scores_map:
                risk_score = self._risk_scores_map[n_str]["risk_score"]
                risk_tier = self._risk_scores_map[n_str]["risk_tier"]
            else:
                acct_rows = self.raw_df[self.raw_df["account_id"] == n]
                if acct_rows.empty:
                    acct_rows = self.raw_df[self.raw_df["counterparty_account"] == n]
                
                risk_score = 0.0
                risk_tier = "LOW"
                if not acct_rows.empty:
                    max_score = float(acct_rows["anomaly_score"].max())
                    risk_score = round(max_score * 100, 2)
                    flagged_count = int(acct_rows["is_flagged"].sum())
                    total_rows = len(acct_rows)
                    flag_ratio = flagged_count / total_rows if total_rows > 0 else 0
                    
                    if flag_ratio >= 0.8 or risk_score >= 75:
                        risk_tier = "CRITICAL"
                    elif flag_ratio >= 0.5 or risk_score >= 50:
                        risk_tier = "HIGH"
                    elif flag_ratio >= 0.3 or risk_score >= 25:
                        risk_tier = "MEDIUM"
                    else:
                        risk_tier = "LOW"

            nodes.append({
                "id": n,
                # Show the real holder's name on the node label when we have
                # one (uploaded/known accounts) — falls back to the raw id
                # for counterparty/pseudo nodes without a holder on file.
                "label": holder_name if holder_name else n,
                "account_holder": holder_name,
                "is_known_account": is_known,   # True = one of the uploaded statement accounts
                "role": role,
                "total_transactions": txn_count,
                "is_expanded": n in expanded_nodes,
                "is_expandable": (threshold is not None) and (txn_count > threshold) and (n not in expanded_nodes),
                "total_received": round(in_amt, 2),
                "total_forwarded": round(out_amt, 2),
                "max_anomaly_score": round(max_risk, 4),
                "in_degree": G.in_degree(n),
                "out_degree": G.out_degree(n),
                "risk_score": risk_score,
                "risk_tier": risk_tier,
            })

        edges = []
        for u, v, k, d in G.edges(keys=True, data=True):
            edges.append({
                "id": f"{u}->{v}->{k}",
                "source": u,
                "target": v,
                "amount": round(d["amount"], 2),
                "mode": d.get("channel", "") or "UNKNOWN",   # e.g. UPI / NEFT / IMPS / RTGS
                "transaction_id": d.get("transaction_id", ""),
                "narration": d.get("narration", ""),
                "datetime": d.get("datetime", ""),
                "direction": d.get("direction", ""),
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